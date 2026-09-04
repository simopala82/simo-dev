#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PreToolUse hook on Bash. Self-gates to `git commit` commands (no settings.json `if`
— same reasoning as mr_followup_remind.py). Emits an additionalContext reminder when no
code review covers the files this commit can include. The verdict belongs to
lib/review_marker.py; this script only decides WHICH repo and WHICH files to ask about.

Windows-compat port of review-loop-warn.sh (OSS migration step 7b) — behavior
intentionally identical; PEP 723 + stdlib-only per the skill_logger.py precedent.
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
_CD_TARGET = re.compile(git_match._SEP + r"cd\s+(" + git_match._GITARG + r")")   # noqa: SLF001


def main() -> int:
    if not _plugin_enabled():
        return 0

    try:
        payload = json.loads(sys.stdin.read())
        cmd = (payload.get("tool_input") or {}).get("command") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0

    # Not anchored to string start alone: simo-dev:commit step 4 stages and commits, so
    # `git -C W add ... && git -C W commit ...` arrives as ONE Bash call.
    match = git_match.git_subcommand_re("commit").search(cmd)
    if not match:
        return 0
    # Keep only the invocation. Everything after `commit` -- message text, pathspecs --
    # is attacker-shaped input: a `-C /somewhere` inside a commit message would
    # otherwise hijack the repo resolution below and silence the reminder.
    invocation = match.group(0)

    # Repo resolution, least to most specific. The payload cwd is the harness session
    # dir, a DIFFERENT worktree from the task one under the worktree policy, so it is
    # only a last resort: both explicit forms below beat it.
    payload_cwd = payload.get("cwd") or ""
    target = payload_cwd
    cd_match = _CD_TARGET.search(cmd)
    if cd_match:
        target = git_match.unquote(cd_match.group(1))
    dash_c_match = _DASH_C.search(invocation)
    if dash_c_match:
        target = git_match.unquote(dash_c_match.group(1))

    toplevel = _git("-C", target or ".", "rev-parse", "--show-toplevel")
    # An explicit path that does not resolve must not buy silence -- retry from the
    # session cwd first.
    if not toplevel:
        toplevel = _git("-C", payload_cwd or ".", "rev-parse", "--show-toplevel")
    if not toplevel:
        return 0

    # Candidate files, resolved WITHOUT consulting the index -- at PreToolUse the
    # command has not run yet, so a chained `git add ... && git commit ...` still has
    # an empty index. Over-approximating is deliberate.
    # -z on both listings, split on NUL: without it git C-quotes a path holding a tab, a
    # carriage return, a quote, a backslash or a non-ASCII byte, and that literal is
    # neither on disk nor in lib/review_marker.py's deletion set, so check() calls the
    # list unusable. lib/review_marker.py's _tracked_deletions docstring documents this
    # as an invariant both callers must uphold.
    # Captured as BYTES, not text=True: universal-newline translation silently rewrites a
    # literal \r inside a filename to \n, which is exactly the kind of byte -z exists to
    # preserve (measured: a "we\rird.txt" path came back as "we\nird.txt" under text=True).
    try:
        diff_out = subprocess.run(
            ["git", "-C", toplevel, "diff", "--name-only", "-z", "HEAD"],
            capture_output=True,
        ).stdout
        untracked_out = subprocess.run(
            ["git", "-C", toplevel, "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
        ).stdout
    except OSError:
        return 0
    # surrogateescape, not "replace": lib/review_marker.py's _tracked_deletions decodes the
    # same kind of raw -z path with surrogateescape, and the two must agree on the exact
    # string for a filename holding a genuinely invalid UTF-8 byte -- "replace" collapses it
    # to U+FFFD (lossy, re-encodes to different bytes), while surrogateescape round-trips.
    files = sorted({
        f.decode("utf-8", errors="surrogateescape")
        for f in (diff_out + untracked_out).split(b"\0") if f
    })
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
           f"(e.g. trivial doc/typo fix).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
