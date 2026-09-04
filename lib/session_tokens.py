#!/usr/bin/env python3
"""Sum output_tokens for the current dev-flow session (main thread + subagents).

Used by simo-dev:mr-followup step 8.5 to record the `tokens` field in the journal.
Read-only, best-effort, honest: the main-thread transcript lives in a stable
location (`~/.claude/projects/<enc>/<session-id>.jsonl`); subagent transcripts
live in a harness-internal temp dir (`<tmp>/.../<session-id>/tasks/*.output`)
that may be absent (non-SDD tasks, other platforms). When the subagent dir is
missing we report main-thread only and say so — never fabricate a total.

Usage: python3 session_tokens.py [SESSION_ID]
  SESSION_ID defaults to $CLAUDE_CODE_SESSION_ID.
Whenever this module runs at all it prints exactly one line on stdout, even on
failure: `main=<n> subagent=<n|na> total=<n> (<note>)`.
Exit codes: 0 = total computed; 4 = nothing to count (no session id, or the main
transcript was not found); 3 = unexpected internal error, traceback on stderr.
Nothing here returns 1 or 2. Those come from outside the module: 2 when CPython
cannot open the script, 1 on a syntax error, 127 with no usable python3 — observed
values, not a closed set — and all of them come with EMPTY stdout, so the caller
must treat an empty `$TOKENS` as "the script never ran" before it reads the rc at
all (measured 2026-08-18, python 3.14.6). That is why "nothing to count" is 4 and
not the obvious 2: the caller derives this path from `$CLAUDE_PLUGIN_ROOT`, and a
wrong root is exactly the case that produces the interpreter's 2.
rc 0 with empty stdout is never produced on purpose — the caller must be able to
tell a crash from "nothing to count" (2026-08-18: an AttributeError on a non-object
line made the journal record a `tokens: unknown` indistinguishable from a missing
transcript).
"""
import glob
import json
import os
import sys
import traceback


def sum_output_tokens(path):
    """Return (total_output_tokens, skipped_lines), or None if the path is unreadable.

    A line is skipped when it is not JSON, or when it is JSON that does not
    deserialize to an object: the subagent glob is `tasks/*.output`, which also
    picks up captured tool output, and a file whose first physical line was the
    bare integer `572` crashed this function on `obj.get` (2026-08-18). A record
    whose `message` is not an object is a normal record with no usage, not a skip.
    A record carrying a non-integer `output_tokens` counts as skipped too: dropping
    it silently would shrink the total with no trace, which is the failure mode this
    function exists to remove. `bool` is excluded on purpose — `isinstance(True, int)`
    is True in Python, so a JSON `true` would otherwise add 1 to the total.
    """
    total = 0
    skipped = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if not isinstance(obj, dict):
                    skipped += 1
                    continue
                message = obj.get("message")
                usage = message.get("usage") if isinstance(message, dict) else None
                if isinstance(usage, dict):
                    count = usage.get("output_tokens")
                    if isinstance(count, int) and not isinstance(count, bool):
                        total += count
                    elif count is not None:
                        skipped += 1
    except OSError:
        return None
    return total, skipped


def find_main_transcript(session_id):
    home = os.path.expanduser("~")
    hits = glob.glob(f"{home}/.claude/projects/*/{session_id}.jsonl")
    return hits[0] if hits else None


def find_subagent_transcripts(session_id):
    # /tmp is a symlink to /private/tmp on macOS, so overlapping bases return the
    # same files under different paths — dedupe by real path to avoid double-counting.
    bases = ["/private/tmp", "/tmp"]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        bases.append(tmpdir.rstrip("/"))
    seen = {}
    for base in bases:
        for p in glob.glob(f"{base}/claude-*/*/{session_id}/tasks/*.output"):
            seen[os.path.realpath(p)] = True
    return list(seen)


def note(base, skipped):
    return f"{base}, {skipped} lines skipped" if skipped else base


def main():
    session_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        print("main=0 subagent=na total=0 (no session id)")
        return 4

    main_path = find_main_transcript(session_id)
    counted = sum_output_tokens(main_path) if main_path else None
    if counted is None:
        print("main=0 subagent=na total=0 (main transcript not found)")
        return 4
    main_tokens, skipped = counted

    sub_files = find_subagent_transcripts(session_id)
    if not sub_files:
        print(f"main={main_tokens} subagent=na total={main_tokens} ({note('subagents not captured', skipped)})")
        return 0

    sub_tokens = 0
    read = unreadable = 0
    for f in sub_files:
        counted = sum_output_tokens(f)
        if counted is None:
            unreadable += 1
        else:
            sub_tokens += counted[0]
            skipped += counted[1]
            read += 1
    total = main_tokens + sub_tokens
    base = f"{read} subagent transcripts"
    if unreadable:
        base += f", {unreadable} unreadable"
    print(f"main={main_tokens} subagent={sub_tokens} total={total} ({note(base, skipped)})")
    return 0


def cli():
    """Wrap main() so an unexpected exception still leaves stdout parseable.

    Without this the caller sees rc 1 and an empty stdout, which is what
    `main transcript not found` also looks like once stderr is discarded.
    """
    try:
        return main()
    except Exception:
        traceback.print_exc()
        print("main=0 subagent=na total=0 (internal error - see stderr)")
        return 3


if __name__ == "__main__":
    sys.exit(cli())
