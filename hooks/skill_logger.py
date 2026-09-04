#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PostToolUse hook on Skill. Appends every skill invocation to the dev-flow
skill log so the evaluation journal (simo-dev:mr-followup step 8.5) can record
which skills actually fired during a task — including auto-fires the agent
wouldn't recall.

Log:  ~/.claude/state/dev-flow-skills.log
Line: <iso8601-utc>\t<session-id>\t<repo>\t<branch>\t<skill>
Keyed on the session id, not repo+branch: the payload cwd is the session dir,
whose branch is the harness branch (claude/<slug>), never the task branch the
journal looks up. Reader/pruner: lib/skill_log.py — keep the fields in sync.

Windows-compatibility pilot port of skill-logger.sh (OSS migration step 7):
behavior is intentionally identical, see
docs/specs/2026-08-27-windows-compat-skill-logger-pilot-design.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _plugin_enabled() -> bool:
    """Fail-open: only a literal `false` for simo-dev@simo-local disables it."""
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
        return data.get("enabledPlugins", {}).get("simo-dev@simo-local") is not False
    except (OSError, json.JSONDecodeError, AttributeError, UnicodeError):
        return True


def _repo_and_branch(cwd: str) -> tuple[str, str]:
    """Returns (repo, branch); repo is '' when cwd is not inside a git repo."""
    try:
        common = subprocess.run(
            ["git", "-C", cwd or ".", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "", ""
    if not common:
        return "", ""
    repo = Path(common).parent.name
    try:
        branch = subprocess.run(
            ["git", "-C", cwd or ".", "branch", "--show-current"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        branch = ""
    return repo, branch


def main() -> int:
    if not _plugin_enabled():
        return 0

    try:
        payload = json.loads(sys.stdin.read())
        skill = (payload.get("tool_input") or {}).get("skill") or ""
        session = payload.get("session_id") or ""
        cwd = payload.get("cwd") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0

    if not skill:
        return 0

    repo, branch = _repo_and_branch(cwd)
    if not repo:
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts}\t{session}\t{repo}\t{branch}\t{skill}\n"
    try:
        state_dir = Path(os.path.expanduser("~/.claude/state"))
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(state_dir / "dev-flow-skills.log", "a", encoding="utf-8") as f:
            f.write(line)
    except (OSError, UnicodeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
