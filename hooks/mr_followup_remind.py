#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""PostToolUse hook on Bash. Self-gates to `git push` by reading tool_input.command
directly (no settings.json `if` — Bash matching there fails OPEN on anything it cannot
parse as simple, which fired this reminder after every ls/rg/git-rev-parse before).

The push reminder is silent unless the simo-dev flow is active (lib/flow_state.py).

It also self-gates to a forge merge (`glab mr merge`, `gh pr merge`, or `glab api` on the
GitLab merge endpoint; git_match.forge_merge_re): when the session holds a task with
no outcome (flow_telemetry.open_tasks), it reminds to close it (mr-followup step 8.5).
That one does not read the flow flag: an adopted task has none.

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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import flow_state  # noqa: E402


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
    reminders = []
    if git_match.git_subcommand_re_multiline("push").search(cmd):
        # Silent unless the simo-dev flow is active (lib/flow_state.py); checked only on a push
        # so no other Bash call pays its git subprocess.
        if flow_state.is_active(payload.get("session_id") or "", payload.get("cwd") or ""):
            reminders.append("REMINDER: Run simo-dev:mr-followup to check pipeline status and MR review comments")
    if git_match.forge_merge_re().search(cmd):  # a push and a merge can share one call
        reminders.extend(_close_reminder(payload.get("session_id") or ""))
    if reminders:
        _emit("\n".join(reminders))
    return 0


def _close_reminder(session: str) -> list[str]:
    """A merge typed outside simo-dev:mr-followup never reaches its step 8.5, and the task stays
    without outcome and cost. It only reminds: the command text cannot tell a merge that went
    through from a refused one, nor which of the session's tasks the MR belongs to. Gated on the
    session holding a task with no outcome, not on the flow flag (an adopted task has none)."""
    try:
        import flow_telemetry

        held = flow_telemetry.open_tasks(session) if session else []
    except Exception:  # noqa: BLE001 - a telemetry failure must never fail the hook
        return []
    if not held:
        return []
    names = ", ".join(f"{task} ({branch})" if branch else task for task, branch in held[-5:])  # the newest
    more = f" (+{len(held) - 5} older)" if len(held) > 5 else ""
    return ["REMINDER: if this merged the MR of one of the tasks below, and simo-dev:mr-followup's post-merge "
            "is not already running, its task still needs step 8.5 (skills/mr-followup/lib/post-merge.md, "
            "\"Dev-flow journal\"), once, with TASK set to that task. "
            f"Tasks of this session with no outcome: {names}{more}"]


if __name__ == "__main__":
    sys.exit(main())
