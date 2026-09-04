#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PreToolUse hook on Write|Edit|MultiEdit. Emits an advisory reminder, once per
session, when this session edits a "real code" file via Edit/Write/MultiEdit without
simo-dev:task-intake having been invoked first (CLAUDE.md "Mandatory entry"). Fires on
a main checkout AND inside a task worktree alike.

KNOWN LIMIT (unchanged from the bash version) -- one session, several tasks: once
simo-dev:task-intake has been invoked anywhere in a session it stays "seen" for the
rest of that session.

Windows-compat port of task-intake-warn.sh (OSS migration step 7b) — behavior
intentionally identical; PEP 723 + stdlib-only per the skill_logger.py precedent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _plugin_enabled() -> bool:
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
        return data.get("enabledPlugins", {}).get("simo-dev@simo-local") is not False
    except (OSError, json.JSONDecodeError, AttributeError, UnicodeError):
        return True


def _is_exempt(file_path: str) -> bool:
    # Docs, plans, scheduled-task defs, scratch notes and personal skills are not
    # "real code" -- CLAUDE.md's task-intake exemptions applied as a path check.
    if file_path.endswith(".md") or file_path.endswith(".txt"):
        return True
    if "/docs/plans/" in file_path:
        return True
    if "/.claude/scheduled-tasks/" in file_path:
        return True
    if "/.scratch/" in file_path:
        return True
    home = os.path.expanduser("~")
    if file_path.startswith(f"{home}/.claude/skills/"):
        return True
    # ~/.claude symlinks into a real checkout -- a file_path already given in
    # resolved-real-path form doesn't match the symlink-prefixed pattern above, so
    # resolve and check it too.
    skills_dir = Path(home) / ".claude" / "skills"
    try:
        skills_real = str(skills_dir.resolve())
    except OSError:
        skills_real = ""
    if skills_real and file_path.startswith(skills_real + "/"):
        return True
    return False


def main() -> int:
    if not _plugin_enabled():
        return 0

    try:
        payload = json.loads(sys.stdin.read())
        file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0
    if not file_path:
        return 0

    if _is_exempt(file_path):
        return 0

    session = payload.get("session_id") or ""
    if not session:
        return 0

    state_file = Path(os.environ.get("TASK_INTAKE_GATE_HOOK_STATE_FILE")
                       or os.path.expanduser("~/.claude/state/task-intake-gate-hook"))
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        if state_file.exists():
            lines = state_file.read_text(encoding="utf-8", errors="replace").splitlines()
            if any(line.startswith(session + " ") for line in lines):
                return 0   # already armed for this session -- never re-checks
    except (OSError, UnicodeError):
        pass

    # Arm FIRST, unconditionally: a python/skill_log failure below must never leave
    # this hook re-checking on every subsequent write.
    try:
        with open(state_file, "a", encoding="utf-8") as f:
            f.write(f"{session} {int(time.time())}\n")
    except (OSError, UnicodeError):
        pass

    lib = Path(__file__).resolve().parent.parent / "lib" / "skill_log.py"
    try:
        result = subprocess.run(
            [sys.executable, str(lib), "read", "--session", session],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        skills = result.stdout.strip()
    except OSError:
        skills = ""
    if "simo-dev:task-intake" in [s for s in skills.split(",") if s]:
        return 0

    ctx = ("REMINDER: this session has edited a code file without invoking "
           "simo-dev:task-intake first (CLAUDE.md \"Mandatory entry\"). If this is more "
           "than a trivial question, typo or single-file lookup, run "
           "simo-dev:task-intake before continuing.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
