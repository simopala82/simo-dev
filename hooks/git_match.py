#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Shared git-command matching for the simo-dev hooks. Python port of git-match.sh
(OSS migration step 7b) — same regex semantics, same SEP-vs-SEP_ML split, same trailing
boundary class that stops a prefix match on another subcommand (`git pushed-nothing`).

Imported by mr_followup_remind.py and review_loop_warn.py via same-directory sys.path
insertion (these are hook scripts invoked by absolute path, not a package).

IMPORTANT: neither pattern below uses re.MULTILINE. Bash's `[[ =~ ]]` anchors `^` only
at the true start of the string (no REG_NEWLINE), so `^` here must mean the same thing —
re.MULTILINE would make `^` also match right after every embedded newline, which is
exactly the newline-BLIND property git_subcommand_re must NOT have (that property is
what protects a caller that parses BASH_REMATCH-equivalent groups from a decoy line
inside a heredoc — see the author's own hook notes (`claude-code-hooks.md`, not shipped
in the published plugin), paragraph "Whether the separator class may include a newline
depends on what the caller does with the match"). The multiline variant gets its
newline-awareness entirely from having a literal `\n` inside its separator character
class, not from an anchoring mode.

review_loop_warn.py parses a newline-aware match, made safe by `strip_heredocs` (drops
heredoc bodies) + `first_unquoted_match` (skips matches inside quotes or comments), falling
back to the newline-blind `git_subcommand_re` when nothing unquoted matches.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

_GITARG = r"(?:'[^']*'|\"[^\"]*\"|[^\s'\"])+"
_GIT_GLOBALS = (
    r"(?:-C\s+" + _GITARG + r"|-c\s+" + _GITARG +
    r"|--(?:git-dir|work-tree)(?:=" + _GITARG + r"|\s+" + _GITARG + r"))"
)
# A real command separator, not bare whitespace — a class that accepted bare whitespace
# and omitted `|` would let prose like `echo "run git commit later"` match it.
_SEP = r"(?:^|[&;|(]\s*)"
_SEP_ML = r"(?:^|[&;|(\n]\s*)"


def unquote(v: str) -> str:
    """Strips one layer of surrounding single or double quotes, each end independently
    (mirrors the bash 4-line `${v%\"}; ${v#\"}; ${v%'}; ${v#'}` sequence — quotes need
    not match: `"foo'` strips to `foo`, exactly as the shell version does)."""
    if v.endswith('"'):
        v = v[:-1]
    if v.startswith('"'):
        v = v[1:]
    if v.endswith("'"):
        v = v[:-1]
    if v.startswith("'"):
        v = v[1:]
    return v


def _pattern(sep: str, subcommand: str) -> str:
    return (
        sep + r"(?:rtk\s+)?git(?:\s+" + _GIT_GLOBALS + r")*\s+" +
        re.escape(subcommand) + r"(?:[\s&;|(]|$)"
    )


def git_subcommand_re(subcommand: str) -> re.Pattern[str]:
    """Pattern for one `git <subcommand>` invocation, newline-blind. NOT anchored to
    string start alone: simo-dev:commit chains `git add ... && git commit ...` into one
    call. Use for a hook that PARSES the match (extracts -C, etc.) — re.search returns
    the LEFTMOST match, so a decoy line inside quoted text (a heredoc body) can be
    leftmost and retarget a caller that parses it if the multiline variant were used
    raw (incident 2026-08-03, see the author's own hook notes, `claude-code-hooks.md`,
    not shipped in the published plugin, paragraph "Whether the separator class may
    include a newline depends on what the caller does with the match"). A parsing caller
    may use the multiline form only through `strip_heredocs` + `first_unquoted_match`."""
    return re.compile(_pattern(_SEP, subcommand))


def git_subcommand_re_multiline(subcommand: str) -> re.Pattern[str]:
    """Use raw ONLY when the match is the whole answer — a hook that decides
    remind-or-not and reads nothing out of the match. Catches multi-line calls the plain
    form misses, at the cost of matching a heredoc/quoted line that STARTS with the
    subcommand. A caller that parses the match must go through `strip_heredocs` +
    `first_unquoted_match` first."""
    return re.compile(_pattern(_SEP_ML, subcommand))


def forge_merge_re() -> re.Pattern[str]:
    """`glab mr merge` / `gh pr merge`, or a `glab api` call on the GitLab merge endpoint
    (`.../merge_requests/<iid>/merge`, quoted or not, method not checked; an auto-merge arming
    PUT matches too), as a command, multi-line (`\\`-newline continuations included). Match-or-not
    only, like `git_subcommand_re_multiline`. Not seen (observed, not a closed list): a merge
    inside a script file, after `do`/`then`/`{`, behind an env assignment or a wrapper
    (`timeout`), with a global flag before `mr` (`glab -R g/p mr merge`), or the endpoint path
    built from a variable. Seen though not a merge: a `glab api` call on another endpoint whose
    quoted argument holds a merge-endpoint path (`-f body='see merge_requests/3/merge'`)."""
    return re.compile(
        _SEP_ML + r"(?:rtk\s+)?(?:(?:glab\s+mr|gh\s+pr)\s+merge(?:[\s&;|(]|$)"
        r"|glab\s+api\s(?:[^\n;&|]|\\\n)*?merge_requests/[^/\s'\"]+/merge(?![\w/-]))"
    )


# `<<` that is neither part of `<<<` (here-string) nor glued to a word/`)` (arithmetic
# `x<<y`), then an optional `-`, then a quoted word or a bare word (`END-X`, `EOF.x`;
# the leading letter keeps a spaced numeric shift `<< 2` from reading as an opener).
_HEREDOC_OPEN = re.compile(
    r"(?<![\w<)])<<(?!<)(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][^\s;&|<>()'\"\\]*))"
)
_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
# An assignment value ends at the first unquoted separator, unlike _GITARG.
_ASSIGN_VALUE = r"(?:'[^']*'|\"[^\"]*\"|[^\s'\";&|()<>])+"


def strip_heredocs(cmd: str) -> str:
    """Drops heredoc BODIES (and their terminator lines), keeping each opener line.
    Lets a caller use the newline-aware pattern without a body line, such as a commit
    message mentioning `git -C /x commit`, becoming the leftmost match. Openers inside a
    comment or a quoted span are skipped, the span tracked over the kept text so far (a
    quote opened on an earlier line counts). A false opener the tracking misses swallows
    the lines up to a matching terminator, so the command reads shorter, never longer --
    with no terminator it swallows to the end of the command, so the caller misses every
    match after it. Known gaps: an opener glued to the command word (`cat<<EOF`) is not
    recognized, since it cannot be told apart from an arithmetic shift (`x<<y`) without
    parsing; a spaced shift on a name (`$(( x << y ))`) is read as one. An unrecognized
    opener leaves its body in place, where an apostrophe opens a phantom quote in
    `_quoted_at` and hides every later match, and a body line starting with
    `git -C /x commit` becomes a decoy match that retargets a parsing caller; a quote
    nested in `"$(...)"`, which `_quoted_at` does not track, opens a phantom too. A caller
    that must not go silent on these falls back to the newline-blind `git_subcommand_re`
    when nothing unquoted matches. Linear: the quote state is carried line to line."""
    out: list[str] = []
    pending: list[tuple[bool, str]] = []
    state = ""   # quote state at the start of the next kept line
    for line in cmd.split("\n"):
        if pending:
            strip_tabs, term = pending[0]
            if (line.lstrip("\t") if strip_tabs else line) == term:
                pending.pop(0)
            continue
        out.append(line)
        pending = [(m.group(1) == "-", m.group(2) or m.group(3) or m.group(4))
                   for m in _HEREDOC_OPEN.finditer(line) if not _scan(line, m.start(), state)]
        state = _scan(line + "\n", len(line) + 1, state)
    return "\n".join(out)


def _quoted_at(text: str, pos: int) -> bool:
    """True when `pos` sits inside a quoted span (or a comment) opened before it.
    Tracks '...', "...", $'...' (backslash escapes, unlike plain '...') and an unquoted
    word-start `#` comment running to end of line, so an apostrophe in a comment does
    not open a phantom quote."""
    quote = _scan(text, pos)
    # A comment ends at the newline itself, which a newline-aware match starts on.
    return bool(quote) and not (quote == "#" and text[pos:pos + 1] == "\n")


def _scan(text: str, end: int, quote: str = "") -> str:
    """Quote state ("", "'", '"', "$'" or "#") after scanning text[:end] from `quote`."""
    i = 0
    while i < end:
        c = text[i]
        if quote == "#":
            if c == "\n":
                quote = ""
        elif c == "\\" and quote != "'":
            i += 2
            continue
        elif quote:
            if c == quote[-1]:
                quote = ""
        elif c == "$" and text[i + 1:i + 2] == "'":
            quote = "$'"
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
            quote = "#"
        i += 1
    return quote


def unquoted_matches(pattern: re.Pattern[str], text: str) -> Iterator[re.Match[str]]:
    """Matches of `pattern` whose start is not inside quoted text or a comment, left to
    right. Walks with search(pos) rather than finditer: a skipped match must not consume
    the separator (the newline) the next real match starts on. With pos > 0, `^` still
    only matches at the true string start."""
    m = pattern.search(text)
    while m:
        if not _quoted_at(text, m.start()):
            yield m
        m = pattern.search(text, m.start() + 1)


def first_unquoted_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """Leftmost unquoted match: what the newline-aware form needs before a caller may
    parse the match (a multi-line quoted string or a comment can hold a decoy
    `git -C /x commit`)."""
    return next(unquoted_matches(pattern, text), None)


def resolve_vars(value: str, prefix: str) -> str | None:
    """Expands $NAME / ${NAME} in `value` from the LAST `NAME=...` assignment (optionally
    `export`ed) at a command start in `prefix`. None when any reference stays unresolved:
    no assignment, or one whose own value still holds a `$` (an environment variable
    this hook cannot see)."""
    unresolved = False

    def _lookup(name: str) -> str | None:
        found = None
        pat = re.compile(_SEP_ML + r"(?:export\s+)?" + re.escape(name) + r"=(" + _ASSIGN_VALUE + r")")
        for m in unquoted_matches(pat, prefix):
            found = unquote(m.group(1))
        return found

    def _sub(m: re.Match[str]) -> str:
        nonlocal unresolved
        v = _lookup(m.group(1) or m.group(2))
        if v is None or "$" in v:
            unresolved = True
            return m.group(0)
        return v

    out = _VAR_REF.sub(_sub, value)
    return None if unresolved else out
