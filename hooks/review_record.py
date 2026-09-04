#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""SubagentStop hook. Records a review for this repo+session when the code-reviewer
subagent CONCLUDES. See docs/adr/0005-review-record-fires-on-subagent-stop.md for why
SubagentStop (not PostToolUse:Skill or PostToolUse:Agent) is the right trigger, and why
the `REPO:` marker must be line 1 of the transcript's FIRST user message (security
boundary, not a formatting rule — see lib/agent_marker.py).

Windows-compat port of review-record.sh (OSS migration step 7b) — behavior
intentionally identical; PEP 723 + stdlib-only per the skill_logger.py precedent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0

    if (payload.get("agent_type") or "") not in ("simo-dev:code-reviewer", "code-reviewer"):
        return 0

    # SubagentStop also fires for an INTERRUPTED subagent -- an absent last_assistant_message
    # means no verdict was reached, so recording would be false coverage.
    if not (payload.get("last_assistant_message") or ""):
        return 0

    session = payload.get("session_id") or ""
    if not session:
        return 0

    transcript = payload.get("agent_transcript_path") or ""
    if not transcript:
        return 0

    lib = Path(__file__).resolve().parent.parent / "lib"
    try:
        marker_result = subprocess.run(
            [sys.executable, str(lib / "agent_marker.py"), transcript],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return 0
    if marker_result.returncode != 0:
        return 0
    repo = marker_result.stdout.strip()
    if not repo or not os.path.isdir(repo):
        return 0
    try:
        subprocess.run(["git", "-C", repo, "rev-parse", "--git-dir"],
                       capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return 0

    try:
        subprocess.run(
            [sys.executable, str(lib / "review_marker.py"), "record",
             "--repo", repo, "--session", session],
            capture_output=True,
        )
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
