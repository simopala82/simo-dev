#!/usr/bin/env python3
"""Report the model and effort of the current dev-flow session's main thread.

Used by simo-dev:mr-followup step 8.5 to fill the journal's `model` and `effort`
fields, which were hardcoded `unknown` since no environment variable carries the model id.
The main-thread transcript (located by session_tokens.find_main_transcript)
has been observed to record both on assistant lines — `message.model` and a
top-level `effort` — though not on every one (synthetic and some older lines lack
`effort`).
The LAST assistant turn is reported — model and effort both from that same turn,
so a turn without `effort` reports `unknown` rather than an older turn's value; a
session that switched models via /model also gets ` (mixed: a, b)` in first-seen
order. A mid-session /effort change is not flagged: only the last value shows. Sidechain lines and the harness's
`<synthetic>` model are ignored. Read-only, best-effort — never fabricate.

Usage: python3 session_meta.py [SESSION_ID]
  SESSION_ID defaults to $CLAUDE_CODE_SESSION_ID.
Prints exactly one line on stdout whenever the module runs:
  rc 0: `model=<m> effort=<e>[ (mixed: <a>, <b>)]` (`effort=unknown` when absent)
  rc 4: `model=unknown effort=unknown (<reason>)` — nothing to report
  rc 3: `model=unknown effort=unknown (internal error - see stderr)`
Same exit-code rationale as session_tokens.py: 4, not 2, for "nothing to report",
and an empty stdout means python never reached this module.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_tokens import find_main_transcript  # noqa: E402

UNKNOWN = "unknown"


def read_meta(path):
    """Return (last_model, last_effort, models_first_seen), or None if unreadable."""
    last_model = last_effort = None
    models = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "assistant" or obj.get("isSidechain"):
                    continue
                message = obj.get("message")
                model = message.get("model") if isinstance(message, dict) else None
                if not isinstance(model, str) or not model or model == "<synthetic>":
                    continue
                last_model = model
                if model not in models:
                    models.append(model)
                effort = obj.get("effort")
                last_effort = effort if isinstance(effort, str) and effort else None
    except OSError:
        return None
    return last_model, last_effort, models


def format_line(model, effort, models, reason=None):
    line = f"model={model or UNKNOWN} effort={effort or UNKNOWN}"
    if reason:
        return f"{line} ({reason})"
    if len(models) > 1:
        line += f" (mixed: {', '.join(models)})"
    return line


def main():
    session_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        print(format_line(None, None, [], reason="no session id"))
        return 4
    path = find_main_transcript(session_id)
    meta = read_meta(path) if path else None
    if meta is None:
        print(format_line(None, None, [], reason="main transcript not found"))
        return 4
    model, effort, models = meta
    if model is None:
        print(format_line(None, None, [], reason="no assistant turn"))
        return 4
    print(format_line(model, effort, models))
    return 0


def cli():
    try:
        return main()
    except Exception:
        traceback.print_exc()
        print(format_line(None, None, [], reason="internal error - see stderr"))
        return 3


if __name__ == "__main__":
    sys.exit(cli())
