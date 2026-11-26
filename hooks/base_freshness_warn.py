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

Silent unless the simo-dev flow is active (lib/flow_state.py).

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import flow_state  # noqa: E402


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


def _is_task_worktree(toplevel: str) -> bool:
    """A task worktree: canonical <REAL>/.worktrees/<name>, or a desktop-app tree at
    <REAL>/.claude/worktrees/<name> carrying the app's marker in its own git dir. The
    marker decides, not the path alone; a main checkout is neither."""
    parent = Path(toplevel).parent
    if parent.name == ".worktrees":
        return True
    if parent.name != "worktrees" or parent.parent.name != ".claude":
        return False
    gitdir = _git("-C", toplevel, "rev-parse", "--absolute-git-dir")
    return bool(gitdir) and (Path(gitdir) / "claude-desktop-worktree").is_file()


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

    # Only task worktrees (see _is_task_worktree): canonical ones, and every tree the
    # desktop app creates, since the app marks each one -- a session that skipped
    # task-intake included. An edit in a main checkout must never fire.
    if not _is_task_worktree(toplevel):
        return 0

    if not flow_state.is_active(payload.get("session_id") or "", toplevel):
        return 0   # flow dormant: silent, and the per-branch arm stays unconsumed

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

    lib_dir = Path(__file__).resolve().parent.parent / "lib"
    cmd = [sys.executable, str(lib_dir / "check_base_freshness.py"), "--repo", toplevel, "--plan-files", "--no-fetch"]
    # Same target simo-dev:commit uses (stacked parent, else origin/HEAD); any failure keeps the default.
    # --prune first, so a parent deleted on the remote is never picked from a stale ref (one-shot hook).
    # This one fetch serves the guard too (--no-fetch): one network round-trip under the hook's short timeout.
    _git("-C", toplevel, "fetch", "origin", "--prune", "--no-tags", "--quiet")
    try:
        target = subprocess.run(
            [sys.executable, str(lib_dir / "build_mr_ctx.py"), "--repo", toplevel, "--print-target"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if target.returncode == 0 and target.stdout.strip():
            cmd += ["--base", target.stdout.strip()]
    except OSError:
        pass
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if result.returncode != 1:
        return 0
    out = result.stdout.strip()   # check_base_freshness.py prints the rc-1 message to stdout

    ctx = (f"REMINDER: origin base advanced since this task's worktree was created, "
           f"touching files its plan declares -- {out}. Re-verify the plan is still "
           f"current before continuing (see simo-dev:task-intake step 7.5, the Plan review gate).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
