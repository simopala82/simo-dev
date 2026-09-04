#!/usr/bin/env python3
"""Test double for the scope_fetch_cmd contract: `<key> <comments>` -> stdout.

Mirrors fake_jira.py's canned behavior, but on the NEW positional contract
(jira_scope.py invokes the configured scope_fetch_cmd this way directly --
it no longer knows about `jira issue view --plain --comments`, that shape
now lives only inside jira_scope_fetch.py).

- KEY containing 'FAIL' -> exit 2 (issue not found)
- KEY containing 'SLOW' -> sleep 5s (to trip the caller timeout)
- otherwise             -> print a canned scope, echoing the comment count
"""
import sys
import time

key = sys.argv[1] if len(sys.argv) > 1 else ""
comments = sys.argv[2] if len(sys.argv) > 2 else "0"

if "FAIL" in key:
    sys.stderr.write("error: issue not found\n")
    sys.exit(2)
if "SLOW" in key:
    time.sleep(5)

sys.stdout.write(f"{key}: Canned summary\n\nDescription body for {key}.\ncomments_shown={comments}\n")
