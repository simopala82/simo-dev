#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PreToolUse hook on Bash. Self-gates to `git commit` commands (no settings.json `if`
— same reasoning as mr_followup_remind.py). Emits an additionalContext reminder when no
code review covers the files this commit can include. The verdict belongs to
lib/review_marker.py; this script only decides WHICH repo and WHICH files to ask about.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_match  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import changed_files  # noqa: E402


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
        stripped = cmd
        match = git_match.git_subcommand_re("commit").search(cmd)
        fallback = True
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

    session = payload.get("session_id") or ""
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
