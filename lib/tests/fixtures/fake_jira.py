#!/usr/bin/env python3
"""Test double for jira-cli. Behavior keyed off the issue key in argv.

Usage mirrors: jira issue view <KEY> --plain --comments <N>
- KEY containing 'FAIL'  -> exit 2 (issue not found)
- KEY containing 'SLOW'  -> sleep 5s (to trip the caller timeout)
- otherwise              -> print a canned scope, echoing the comment count
"""
import sys
import time

args = sys.argv[1:]
key = args[2] if len(args) >= 3 and args[0] == "issue" and args[1] == "view" else ""
comments = "0"
if "--comments" in args:
    comments = args[args.index("--comments") + 1]

if "FAIL" in key:
    sys.stderr.write("error: issue not found\n")
    sys.exit(2)
if "SLOW" in key:
    time.sleep(5)

sys.stdout.write(f"{key}: Canned summary\n\nDescription body for {key}.\ncomments_shown={comments}\n")
