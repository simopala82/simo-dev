#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""SessionStart hook. Prints one additionalContext line when the session starts inside
an active simo-dev flow (lib/flow_state.py): the session already carries the flag (a
resume keeps its session id), or its cwd is a worktree carrying the flow marker. Prints
nothing otherwise, so outside the flow this hook costs no context.

Known limit: a session whose cwd is the main checkout while the task worktree sits
elsewhere (task-intake rows 1/2) is recognised only through the session flag."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import flow_state  # noqa: E402


def _plugin_enabled() -> bool:
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
        return data.get("enabledPlugins", {}).get("simo-dev@simo-local") is not False
    except (OSError, json.JSONDecodeError, AttributeError, UnicodeError):
        return True


def main() -> int:
    if not _plugin_enabled():
        return 0
    try:
        payload = json.loads(sys.stdin.read())
        cwd = payload.get("cwd") or ""
        session = payload.get("session_id") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0
    if not flow_state.is_active(session, cwd):
        return 0
    ctx = ("simo-dev flow active in this worktree/session: the CLAUDE.md rules gated on the flow "
           "(review-loop eager, PR Auto-fix) apply here.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
