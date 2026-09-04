#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PostToolUse hook on Bash. Self-gates to `git push` by reading tool_input.command
directly (no settings.json `if` — Bash matching there fails OPEN on anything it cannot
parse as simple, which fired this reminder after every ls/rg/git-rev-parse before).

Windows-compat port of mr-followup-remind.sh (OSS migration step 7b) — behavior
intentionally identical; PEP 723 + stdlib-only per the skill_logger.py precedent.
"""
from __future__ import annotations

import json
import os
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


def _emit(ctx: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": ctx}}))


def main() -> int:
    if not _plugin_enabled():
        return 0

    try:
        payload = json.loads(sys.stdin.read())
        cmd = (payload.get("tool_input") or {}).get("command") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0

    # This hook reads nothing out of the match (remind-or-not only), so the multiline
    # form is safe here: a leftmost match on a decoy line costs at most one spurious
    # reminder, never a wrong repo resolution.
    if git_match.git_subcommand_re_multiline("push").search(cmd):
        _emit("REMINDER: Run simo-dev:mr-followup to check pipeline status and MR review comments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
