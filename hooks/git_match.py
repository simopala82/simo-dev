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
inside a heredoc — see review-loop-warn.sh's incident comment, ported in
review_loop_warn.py). The multiline variant gets its newline-awareness entirely from
having a literal `\n` inside its separator character class, not from an anchoring mode.
"""
from __future__ import annotations

import re

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
    instead (incident 2026-08-03, see review_loop_warn.py)."""
    return re.compile(_pattern(_SEP, subcommand))


def git_subcommand_re_multiline(subcommand: str) -> re.Pattern[str]:
    """Use ONLY when the match is the whole answer — a hook that decides remind-or-not
    and reads nothing out of the match. Catches multi-line calls the plain form misses,
    at the cost of matching a heredoc/quoted line that STARTS with the subcommand."""
    return re.compile(_pattern(_SEP_ML, subcommand))
