#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""SubagentStop hook. Records a review for this work tree+session when the code-reviewer
subagent CONCLUDES. See docs/adr/0005-review-record-fires-on-subagent-stop.md for why
SubagentStop (not PostToolUse:Skill or PostToolUse:Agent) is the right trigger, and why
the `REPO:` marker must be line 1 of the transcript's FIRST user message (security
boundary, not a formatting rule — see lib/agent_marker.py).

The foreground process only checks the payload and hands a slim copy to a detached
`review_record.py --worker <json>`, which runs every gate and records. On CLI 2.1.308 the
hook starts before the closing SubagentHandback entry is on disk, so a single read found no
handback: 12 of 40 handback conclusions went unrecorded (2026-10-24 16:05Z to 2026-10-25
10:38Z) while every missed transcript passed all the gates once written. The worker re-reads
for up to HANDBACK_WAIT_SECONDS (_handback_lands). Detaching came first, on a suspicion that
the CLI kills the hook at stream teardown; a probe (2026-10-26) saw no kill, but the detach
stays as cheap insurance. The record epoch trails the conclusion by the worker's whole run:
start, the handback wait, the marker subprocess, git and review_marker
(~/.claude/reference/claude-code-hooks.md).

Windows-compat port of review-record.sh (OSS migration step 7b) — behavior was identical
at the port; the SubagentHandback completion check was added after it. PEP 723 +
stdlib-only per the skill_logger.py precedent.
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


HANDBACK_TOOL = "SubagentHandback"
WORKER_FLAG = "--worker"
HANDBACK_WAIT_SECONDS = 2.0
_HANDBACK_POLL_SECONDS = 0.05


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
    # split("\n"), never splitlines(): the CLI leaves NEL, U+2028 and U+2029 raw inside JSON
    # strings, and splitlines() breaks the handback entry there into unparsable fragments.
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").split("\n")
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


def _cheap_gates(payload: dict) -> tuple[str, str] | None:
    """(session, transcript) when the payload is a reviewer stop worth recording, else None.
    No subprocess and no transcript read: the foreground process must exit fast."""
    if (payload.get("agent_type") or "") not in ("simo-dev:code-reviewer", "code-reviewer"):
        return None
    session = payload.get("session_id") or ""
    transcript = payload.get("agent_transcript_path") or ""
    if not session or not transcript:
        return None
    return session, transcript


def _handback_lands(transcript: str) -> bool:
    """_handback_concluded, re-read until it holds or HANDBACK_WAIT_SECONDS pass.

    CLI 2.1.308 fires SubagentStop before the closing SubagentHandback entry is on disk: a
    probe (2026-10-26, 6 of 6 handback-only stops) saw the transcript end at the final
    api-request lines when the hook started, and the handback land 0-50 ms later. An
    interrupted subagent never gains one, so waiting costs it the window and records nothing.

    A True read must hold one poll later: a response whose handback is followed by a parallel
    tool_use is written one entry at a time, and a read between the two would see the
    handback last. A single early read rarely hit that gap; repeated reads would. It covers
    only entries queued together: an interrupt while the parallel block is still streaming
    may never write it, and the handback then stays last (as it did with a single read)."""
    deadline = time.monotonic() + HANDBACK_WAIT_SECONDS
    while True:
        if _handback_concluded(transcript):
            time.sleep(_HANDBACK_POLL_SECONDS)
            if _handback_concluded(transcript):
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_HANDBACK_POLL_SECONDS)


def _record(payload: dict) -> int:
    if not _plugin_enabled():
        return 0
    gated = _cheap_gates(payload)
    if gated is None:
        return 0
    session, transcript = gated

    # SubagentStop also fires for an INTERRUPTED subagent -- neither verdict text nor a
    # closing handback means no verdict was reached, so recording would be false coverage.
    if not (payload.get("last_assistant_message") or "") and not _handback_lands(transcript):
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


def _detach(payload: dict, session: str, transcript: str) -> None:
    """Hand the record path to a worker in its own session and do not wait for it.

    2.1.308 binds SubagentStop hooks to the agent's abort signal and can kill one still
    running at stream teardown; a probe never saw it happen, so this is insurance. The
    record path is slow enough to be worth insuring: it waits for the handback and spawns
    several processes (two Python scripts and git, which review_marker's _tree_id runs
    again). The payload travels in argv, so the spawn is one atomic step with no pipe left
    half-written by a kill; it is slimmed because only the presence of the verdict text
    matters."""
    slim = {"agent_type": payload.get("agent_type"), "session_id": session,
            "agent_transcript_path": transcript,
            "last_assistant_message": "1" if payload.get("last_assistant_message") else ""}
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), WORKER_FLAG, json.dumps(slim)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def _load(text: str) -> dict | None:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def main(argv: list[str]) -> int:
    # The plugin check reads settings.json, so it runs in the worker (_record): every
    # millisecond before the Popen is a window in which a kill loses the record.
    if len(argv) == 3 and argv[1] == WORKER_FLAG:
        payload = _load(argv[2])
        return _record(payload) if payload is not None else 0

    try:
        payload = _load(sys.stdin.read())
    except (AttributeError, UnicodeError):
        return 0
    if payload is None:
        return 0
    gated = _cheap_gates(payload)
    if gated is not None:
        _detach(payload, *gated)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
