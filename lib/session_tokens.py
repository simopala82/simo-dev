#!/usr/bin/env python3
"""Sum the tokens of one Claude Code session (main thread + subagents).

All four usage categories (input, cache creation, cache read, output) are
summed, deduped by `message.id` (lib/transcript_usage.py). The main transcript
is `~/.claude/projects/<enc>/<session-id>.jsonl`, the subagents
`~/.claude/projects/<enc>/<session-id>/subagents/agent-*.jsonl`. A standalone
CLI: the flow itself attributes tokens per task with `flow_telemetry.py attribute`.

Usage: python3 session_tokens.py [SESSION_ID]
  SESSION_ID defaults to $CLAUDE_CODE_SESSION_ID.
Whenever this module runs at all it prints exactly one line on stdout, even on
failure: `main=<n> subagent=<n|na> total=<n> (<note>)`; the note carries the
per-category breakdown.
Exit codes: 0 = total computed; 4 = nothing to count (no session id, or the main
transcript was not found); 3 = unexpected internal error, traceback on stderr.
Nothing here returns 1 or 2. Those come from outside the module: 2 when CPython
cannot open the script, 1 on a syntax error, 127 with no usable python3 — observed
values, not a closed set — and all of them come with EMPTY stdout, so a shell
caller must treat an empty capture as "the script never ran" before it reads the
rc at all (measured 2026-08-18, python 3.14.6). That is why "nothing to count" is
4 and not the obvious 2: a caller deriving this path from `$CLAUDE_PLUGIN_ROOT`
with a wrong root is exactly the case that produces the interpreter's 2.
rc 0 with empty stdout is never produced on purpose — the caller must be able to
tell a crash from "nothing to count".
"""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcript_usage import (  # noqa: E402
    find_main_transcript,
    find_subagent_transcripts,
    read_usage,
    totals,
)


def note(base, skipped):
    return f"{base}, {skipped} lines skipped" if skipped else base


def breakdown(counts):
    return (f"in={counts['input_tokens']} cache_write={counts['cache_creation_input_tokens']} "
            f"cache_read={counts['cache_read_input_tokens']} out={counts['output_tokens']}")


def main():
    session_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        print("main=0 subagent=na total=0 (no session id)")
        return 4
    main_path = find_main_transcript(session_id)
    counted = read_usage(main_path) if main_path else None
    if counted is None:
        print("main=0 subagent=na total=0 (main transcript not found)")
        return 4
    main_records, skipped = counted
    sub_records = {}
    sub_files = find_subagent_transcripts(main_path)
    read = unreadable = 0
    for f in sub_files:
        got = read_usage(f)
        if got is None:
            unreadable += 1
            continue
        sub_records.update(got[0])
        skipped += got[1]
        read += 1
    m = totals(main_records.values())
    s = totals(sub_records.values())
    both = {k: m[k] + s[k] for k in m}
    main_total, sub_total = sum(m.values()), sum(s.values())
    base = f"{read} subagent transcripts" if sub_files else "no subagent transcripts"
    if unreadable:
        base += f", {unreadable} unreadable"
    sub_field = str(sub_total) if sub_files else "na"
    print(f"main={main_total} subagent={sub_field} total={main_total + sub_total} "
          f"({note(base + '; ' + breakdown(both), skipped)})")
    return 0


def cli():
    """Wrap main() so an unexpected exception still leaves stdout parseable."""
    try:
        return main()
    except Exception:
        traceback.print_exc()
        print("main=0 subagent=na total=0 (internal error - see stderr)")
        return 3


if __name__ == "__main__":
    sys.exit(cli())
