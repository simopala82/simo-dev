#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""SubagentStop hook. Records a review for this repo+session when the code-reviewer
subagent CONCLUDES. See docs/adr/0005-review-record-fires-on-subagent-stop.md for why
SubagentStop (not PostToolUse:Skill or PostToolUse:Agent) is the right trigger, and why
the `REPO:` marker must be line 1 of the transcript's FIRST user message (security
boundary, not a formatting rule — see lib/agent_marker.py).

Windows-compat port of review-record.sh (OSS migration step 7b) — behavior was identical
at the port; the SubagentHandback completion check was added after it. PEP 723 +
stdlib-only per the skill_logger.py precedent.
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


HANDBACK_TOOL = "SubagentHandback"


def _handback_concluded(transcript: str) -> bool:
    """True when the subagent's LAST message is a SubagentHandback carrying a report.

    With handback the final assistant turn is that tool_use, not text, so SubagentStop has
    no last_assistant_message; the tool_result is appended only after the hook runs
    (observed 2026-10-05, CLI 2.1.286). Only the last message-bearing entry counts: an
    interrupted SendMessage resume shares the transcript, and its earlier round's handback
    must not pass for this round's conclusion. That handback's own tool_result as the only
    entry after it is tolerated too, so the gate does not hang on a write order seen on two
    probes only; a resume was observed to add a new user message after it. The handback must
    be the only tool_use of its response: each block of one response is written as its own
    entry sharing message.id, so a parallel tool_use still running leaves the handback last
    while the subagent may yet be interrupted. The two parallel handbacks observed (2.1.284)
    later ended in text, so last_assistant_message covers that shape anyway. An interrupt
    landing while the handback call itself runs leaves the accepted shape and records; that
    window is accepted, since the full report is already in the transcript."""
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("message"), dict):
            entries.append(entry)
    if not entries:
        return False
    last = entries[-1]
    trailing_result_ids = _tool_result_ids(last) if last.get("type") == "user" else None
    if trailing_result_ids:
        if len(entries) < 2:
            return False
        last = entries[-2]
    if last.get("type") != "assistant":
        return False
    content = last["message"].get("content")
    if not isinstance(content, list):
        return False
    response_id = last["message"].get("id")
    response = [e for e in entries if e is last or (
        isinstance(response_id, str) and e.get("type") == "assistant"
        and e["message"].get("id") == response_id)]
    tool_uses = [b for e in response if isinstance(e["message"].get("content"), list)
                 for b in e["message"]["content"]
                 if isinstance(b, dict) and b.get("type") == "tool_use"]
    if len(tool_uses) != 1:
        return False
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_use"
                and block.get("name") == HANDBACK_TOOL):
            continue
        block_id = block.get("id")
        if trailing_result_ids and not (isinstance(block_id, str)
                                        and trailing_result_ids == {block_id}):
            continue
        tool_input = block.get("input")
        report = tool_input.get("message") if isinstance(tool_input, dict) else None
        if isinstance(report, str) and report.strip():
            return True
    return False


def _tool_result_ids(entry: dict) -> set | None:
    """tool_use_ids of a user entry made only of non-error tool_result blocks, else None."""
    content = entry["message"].get("content")
    if not isinstance(content, list) or not content:
        return None
    ids = set()
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_result"
                and isinstance(block.get("tool_use_id"), str)
                and block.get("is_error") is not True):
            return None
        ids.add(block["tool_use_id"])
    return ids


def main() -> int:
    if not _plugin_enabled():
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0

    if (payload.get("agent_type") or "") not in ("simo-dev:code-reviewer", "code-reviewer"):
        return 0

    session = payload.get("session_id") or ""
    if not session:
        return 0

    transcript = payload.get("agent_transcript_path") or ""
    if not transcript:
        return 0

    # SubagentStop also fires for an INTERRUPTED subagent -- neither verdict text nor a
    # closing handback means no verdict was reached, so recording would be false coverage.
    if not (payload.get("last_assistant_message") or "") and not _handback_concluded(transcript):
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
