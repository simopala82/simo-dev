#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PreToolUse hook on Bash. Self-gates to `git commit` / `git push` commands (no
settings.json `if` — same reasoning as mr_followup_remind.py). The verdict belongs to
lib/review_marker.py; this script only decides WHICH repo and WHICH files to ask about.

Inside the simo-dev flow (lib/flow_state.py: session flag or worktree marker): on a
commit, the full REMINDER when no code review covers the files this commit can include;
a push stays silent (mr_followup_remind.py covers it).
Outside the flow: a one-line `simo-dev advisory: no review recorded` on a commit or a
push of a non-protected branch whose files no review covers, at most once per (session,
repo, branch, event). Never blocks, so headless `claude -p` runs are unaffected.
Known limit: a commit match always wins over a push in the same command (`git commit … &&
git push` is judged as a commit), and the newline-blind commit fallback can read a quoted
`; git commit` as a commit. The cost is a commit-judged verdict: outside the flow a
commit-worded line, or no push advisory at all once that branch's commit line was used; in
the flow the full commit REMINDER on what is really a push.

Windows-compat port of review-loop-warn.sh (OSS migration step 7b); PEP 723 +
stdlib-only per the skill_logger.py precedent. Unlike the retired shell version, it
matches `git commit` on any line: heredoc bodies are stripped, quoted/commented matches
skipped, and `$VAR` path arguments resolved from same-command assignments.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_match  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import changed_files  # noqa: E402
import flow_state  # noqa: E402

PROTECTED_BRANCHES = {"master", "main", "develop"}
ADVISORY_KEEP_S = 7 * 24 * 3600


def _plugin_enabled() -> bool:
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
        return data.get("enabledPlugins", {}).get("simo-dev@simo-local") is not False
    except (OSError, json.JSONDecodeError, AttributeError, UnicodeError):
        return True


def _git(*args: str) -> str | None:
    """Returns stdout on success or None on failure -- mirrors `2>/dev/null`."""
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


_DASH_C = re.compile(r"-C\s+(" + git_match._GITARG + r")")          # noqa: SLF001 -- same module family
_CD_TARGET = re.compile(git_match._SEP_ML + r"cd\s+(" + git_match._GITARG + r")")   # noqa: SLF001
_CD_TARGET_LEGACY = re.compile(git_match._SEP + r"cd\s+(" + git_match._GITARG + r")")   # noqa: SLF001


def _path_arg(raw: str, prefix: str) -> str:
    """Unquotes a -C / cd argument and expands $VAR from assignments in `prefix`.
    Single-quoted stays literal, as in the shell. Unresolvable -> the literal, which
    fails rev-parse and lands on the payload-cwd fallback below."""
    value = git_match.unquote(raw)
    if "$" not in value or raw.startswith("'"):
        return value
    return git_match.resolve_vars(value, prefix) or value


def _origin_default(toplevel: str) -> str | None:
    head = _git("-C", toplevel, "rev-parse", "--abbrev-ref", "origin/HEAD")
    if head and head != "origin/HEAD":
        return head
    for ref in ("origin/main", "origin/master"):
        if _git("-C", toplevel, "rev-parse", "--verify", "--quiet", ref) is not None:
            return ref
    return None


def _pushed_files(toplevel: str) -> list[str]:
    base = _origin_default(toplevel)
    if not base:
        return []
    point = _git("-C", toplevel, "merge-base", "HEAD", base)
    if not point:
        return []
    # Plumbing, -z, deletions excluded: review_marker only covers files that still exist,
    # and porcelain diff C-quotes unusual paths (same reasoning as lib/changed_files.py).
    try:
        raw = subprocess.run(
            ["git", "-C", toplevel, "diff-tree", "-r", "-z", "--name-only", "--no-renames",
             "--diff-filter=d", point, "HEAD"],
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p]


def _first_advisory(session: str, toplevel: str, branch: str, event: str) -> bool:
    """True the first time (session, repo, branch, event) is seen; records it.
    Lines are `<epoch>\\t<key>`; lines older than 7 days are dropped on every write."""
    state = Path(os.environ.get("SIMO_DEV_ADVISORY_STATE_FILE")
                 or os.path.expanduser("~/.claude/state/dev-flow/advisory-seen"))
    key = "\t".join((session, toplevel, branch, event))
    now = int(time.time())
    try:
        lines = state.read_text(encoding="utf-8", errors="replace").splitlines() if state.exists() else []
    except (OSError, UnicodeError):
        lines = []
    kept = []
    for line in lines:
        epoch, _, rest = line.partition("\t")
        if epoch.isdigit() and now - int(epoch) <= ADVISORY_KEEP_S:
            kept.append(line)
            if rest == key:
                return False
    kept.append(f"{now}\t{key}")
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except (OSError, UnicodeError):
        pass
    return True


def _covered(toplevel: str, session: str, files: list[str]) -> bool:
    lib = Path(__file__).resolve().parent.parent / "lib" / "review_marker.py"
    argv = [sys.executable, str(lib), "check", "--repo", toplevel]
    if session:
        argv += ["--session", session]
    argv += ["--files", *files]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return False
    return result.returncode == 0


def _advise(session: str, toplevel: str, event: str) -> int:
    """Outside the flow: one advisory line, feature branches only, once per key."""
    branch = _git("-C", toplevel, "branch", "--show-current") or ""
    if not branch or branch in PROTECTED_BRANCHES:
        return 0
    if event == "push":
        files = _pushed_files(toplevel)
    else:
        try:
            files = changed_files.candidates(toplevel)
        except (OSError, subprocess.CalledProcessError):
            return 0
    if not files or _covered(toplevel, session, files):
        return 0
    if not _first_advisory(session, toplevel, branch, event):
        return 0
    ctx = (f"simo-dev advisory: no review recorded for this {event} on {branch}. "
           f"Advisory only; run simo-dev:review-loop if you want one.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    return 0


def main() -> int:
    if not _plugin_enabled():
        return 0

    try:
        payload = json.loads(sys.stdin.read())
        cmd = (payload.get("tool_input") or {}).get("command") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0

    # Newline-aware, because simo-dev:commit puts `W=...` on its own first line and the
    # newline-blind form never saw a commit below it (2026-10-12: an unreviewed edit was
    # committed with no reminder). Made safe to parse by dropping heredoc bodies and
    # skipping matches inside quoted text: either can hold a decoy `git -C /x commit`.
    stripped = git_match.strip_heredocs(cmd)
    match = git_match.first_unquoted_match(git_match.git_subcommand_re_multiline("commit"), stripped)
    fallback = False
    if not match:
        # The quote tracking has known gaps (a quote nested in "$(...)" opens a phantom
        # span): fall back to the newline-blind form, so this never goes silent where
        # the pre-2026-10-12 matcher fired -- `cd` included, via the old first-match
        # lookup, since the same phantom span would hide it from the quote filter.
        match = git_match.git_subcommand_re("commit").search(cmd)
        if match:
            stripped = cmd
            fallback = True
    event = "commit"
    if not match:
        # Push last, so every command that got the commit REMINDER before still does. Same
        # heredoc-free, quote-aware matching as the commit's first try.
        match = git_match.first_unquoted_match(git_match.git_subcommand_re_multiline("push"), stripped)
        event = "push"
    if not match:
        return 0
    # Keep only the invocation. Everything after `commit` -- message text, pathspecs --
    # is attacker-shaped input: a `-C /somewhere` inside a commit message would
    # otherwise hijack the repo resolution below and silence the reminder.
    invocation = match.group(0)
    prefix = stripped[:match.start()]

    # Repo resolution, least to most specific. The payload cwd is the harness session
    # dir, a DIFFERENT worktree from the task one under the worktree policy, so it is
    # only a last resort: both explicit forms below beat it. $VAR arguments resolve from
    # assignments earlier in the same command (simo-dev:commit's `-C "$W"`).
    payload_cwd = payload.get("cwd") or ""
    target = payload_cwd
    if fallback:
        prefix = git_match.strip_heredocs(prefix)   # $VAR lookup must not see heredoc bodies
        cd_match = _CD_TARGET_LEGACY.search(cmd)
    else:
        cd_match = next(reversed(list(git_match.unquoted_matches(_CD_TARGET, prefix))), None)
        # A phantom quote that closed before the commit can still hide the `cd`: keep the
        # old first-match lookup as the floor (prefix is already heredoc-free).
        cd_match = cd_match or _CD_TARGET_LEGACY.search(prefix)
    if cd_match:
        target = _path_arg(cd_match.group(1), prefix)
    dash_c_match = _DASH_C.search(invocation)
    if dash_c_match:
        target = _path_arg(dash_c_match.group(1), prefix)

    toplevel = _git("-C", target or ".", "rev-parse", "--show-toplevel")
    # An explicit path that does not resolve must not buy silence -- retry from the
    # session cwd first.
    if not toplevel:
        toplevel = _git("-C", payload_cwd or ".", "rev-parse", "--show-toplevel")
    if not toplevel:
        return 0

    session = payload.get("session_id") or ""
    in_flow = flow_state.is_active(session, toplevel) or flow_state.worktree_active(payload_cwd)
    if not in_flow:
        return _advise(session, toplevel, event)
    if event == "push":
        return 0   # in the flow, mr_followup_remind.py owns the push reminder

    # Candidate files, staged or not -- never limited to `--cached`: at PreToolUse the
    # command has not run yet, so a chained `git add ... && git commit ...` still has
    # an empty index. Over-approximating is deliberate. Read-only, -z, byte-exact:
    # lib/changed_files.py documents why.
    try:
        files = changed_files.candidates(toplevel)
    except (OSError, subprocess.CalledProcessError):
        return 0   # the hook never blocks on its own failure; a failed listing skips the reminder
    if not files:
        return 0

    lib = Path(__file__).resolve().parent.parent / "lib" / "review_marker.py"
    argv = [sys.executable, str(lib), "check", "--repo", toplevel]
    if session:
        argv += ["--session", session]
    argv += ["--files", *files]
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        return 0
    # python3 missing or the helper itself failing lands here with empty stdout; keep
    # the reminder diagnosable instead of emitting a dangling "-- .".
    reason_lines = [ln for ln in (result.stdout if result else "").splitlines() if ln.strip()]
    if len(reason_lines) > 10:
        reason = "; ".join(reason_lines[:10]) + f"; and {len(reason_lines) - 10} more"
    else:
        reason = "; ".join(reason_lines)
    reason = reason or "review record could not be read"

    ctx = (f"REMINDER: no code review covers this commit -- {reason}. Per the "
           f"simo-dev:guide flow conventions, run simo-dev:review-loop BEFORE git commit "
           f"to catch medium+ issues. Skip only if the user explicitly opts out "
           f"(e.g. trivial doc/typo fix). Not a trigger for a simo-dev:mr-followup "
           f"run's own commits (simo-dev canonical-flow \"In-scope improvements\", Transparency).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
