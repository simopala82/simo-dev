#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""UserPromptSubmit hook. Warns once per session when a session whose flow task has ended gets a
new prompt after more than an hour idle: the prompt cache is gone, so the session rewrites its
whole context, and new work is cheaper in a new session.

Fires when all hold: the session's last main-chain API call is more than IDLE_S old (read from
the transcript tail); the session appears in a task file (lib/flow_telemetry.py) and holds no open
task there (every task it touched has an `outcome`, or it released it with `handoff`); no warning
was given to this session yet (<telemetry dir>/reuse-warned/<session>, pruned with the adopted
IDs). Output: a `systemMessage` for the user and one `additionalContext` line. Never blocks: rc 0
on any input, any error (an rc 2 here would block the prompt).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import flow_telemetry  # noqa: E402
import gate_reminder  # noqa: E402
import transcript_usage  # noqa: E402

IDLE_S = 3600
TAIL_BYTES = 4 * 1024 * 1024


def _plugin_enabled() -> bool:
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8", errors="replace"))
        return data.get("enabledPlugins", {}).get("simo-dev@simo-local") is not False
    except (OSError, json.JSONDecodeError, AttributeError, UnicodeError):
        return True


def last_call(path: str) -> dict | None:
    """The newest main-chain API call in the transcript's tail: time, prompt size, model."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        prompt = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        ts = transcript_usage.parse_ts(rec.get("timestamp"))
        if prompt and ts:
            return {"ts": ts, "prompt": prompt, "model": message.get("model") or ""}
    return None


def warning(session: str, transcript: str, now: datetime) -> str:
    """Cheapest checks first: the marker, then the task files (small; a session outside the flow
    stops here), then the transcript tail, then the open-task scan."""
    marker = flow_telemetry.telemetry_dir() / "reuse-warned" / flow_telemetry._safe_name(session)
    if marker.exists() or not flow_telemetry._in_a_task(session):
        return ""
    call = last_call(transcript)
    if not call or (now - call["ts"]).total_seconds() <= IDLE_S or flow_telemetry.open_tasks(session):
        return ""
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(now.isoformat() + "\n", encoding="utf-8")
    idle = int((now - call["ts"]).total_seconds() // 60)
    return (f"simo-dev: this session's task has ended and it sat idle {idle} min, so this prompt rewrites its "
            f"{call['prompt'] // 1000}k context (~${gate_reminder.rewrite_cost(call):.2f}). "
            "New work is cheaper in a new session.")


def main() -> int:
    try:
        if not _plugin_enabled():
            return 0
        payload = json.loads(sys.stdin.read())
        session = payload.get("session_id") or ""
        transcript = payload.get("transcript_path") or ""
        if not session or not transcript:
            return 0
        msg = warning(session, transcript, datetime.now(timezone.utc))
        if msg:
            print(json.dumps({"systemMessage": msg, "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": msg + " Say this to the user once, in one line, then do what was asked."}}))
    except Exception:  # noqa: BLE001 - a warning hook must never block or break the prompt
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
