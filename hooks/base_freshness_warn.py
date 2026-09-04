#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PreToolUse hook on Write|Edit|MultiEdit. See design:
docs/specs/2026-08-24-plan-review-gate-freshness-guard-design.md

Emits an advisory reminder, once per (repo, branch), when origin/<base> has advanced
past this task worktree's fork point on files the task's plan(s) declare. Fires on the
first write OUTSIDE docs/plans/** in a task worktree, then never again for that branch
(armed-state file).

Windows-compat port of base-freshness-warn.sh (OSS migration step 7b) — behavior
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


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


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

    # The plan itself being authored/edited -- not the "real" first write, and it
    # usually runs before the plan exists to check against.
    if "/docs/plans/" in file_path:
        return 0

    toplevel = _git("-C", str(Path(file_path).parent), "rev-parse", "--show-toplevel")
    if not toplevel:
        return 0

    # Only task-intake worktrees -- canonical layout is <REAL>/.worktrees/<name>. An
    # edit in a main checkout must never fire.
    if Path(toplevel).parent.name != ".worktrees":
        return 0

    branch = _git("-C", toplevel, "branch", "--show-current")
    if not branch:
        return 0   # detached HEAD -- not an adopted task branch (yet)

    common_dir = _git("-C", toplevel, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common_dir:
        return 0
    real = str(Path(common_dir).parent)   # the main checkout owning this worktree

    state_file = Path(os.environ.get("BASE_FRESHNESS_HOOK_STATE_FILE")
                       or os.path.expanduser("~/.claude/state/base-freshness-hook"))
    key = f"{real}:{branch}"
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        if state_file.exists():
            lines = state_file.read_text(encoding="utf-8", errors="replace").splitlines()
            if any(line.startswith(key + " ") for line in lines):
                return 0   # already armed for this repo+branch -- never re-checks
    except (OSError, UnicodeError):
        pass

    # Arm FIRST, unconditionally: a python/git failure below must never leave this hook
    # re-checking (and re-fetching origin) on every subsequent write.
    try:
        with open(state_file, "a", encoding="utf-8") as f:
            f.write(f"{key} {int(time.time())}\n")
    except (OSError, UnicodeError):
        pass

    lib = Path(__file__).resolve().parent.parent / "lib" / "check_base_freshness.py"
    try:
        result = subprocess.run(
            [sys.executable, str(lib), "--repo", toplevel, "--plan-files"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return 0
    if result.returncode != 1:
        return 0
    out = result.stdout.strip()   # check_base_freshness.py prints the rc-1 message to stdout

    ctx = (f"REMINDER: origin base advanced since this task's worktree was created, "
           f"touching files its plan declares -- {out}. Re-verify the plan is still "
           f"current before continuing (see simo-dev:task-intake step 7.5's recovery).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
