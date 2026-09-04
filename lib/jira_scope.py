#!/usr/bin/env python3
"""Fetch ticket scope text for dev-mr-review, via the tracker adapter.

Never raises and never exits non-zero on a *fetch* problem: prints empty stdout on
any failure (no key, no scope_fetch_cmd configured for the prefix, the prefix
explicitly declined ("-"), a malformed or wrongly-shaped ~/.claude/simo-dev/
tracker.json (invalid JSON, or valid JSON that isn't an object), a malformed
scope_fetch_cmd value (unbalanced quoting), adapter command missing/non-zero,
timeout, empty render, or a .claude/no-tracker marker under --repo-root). The
skill runs unattended, so a missing ticket must degrade silently.
(CLI-argument errors are a caller bug and still exit non-zero via argparse,
as usual.)

The actual ticket-view fetch is delegated to tracker_config.py's
scope_fetch_cmd for the key's prefix (contract: `<CMD> <key> <comments>` ->
plain text on stdout). This module owns only the generic parts: the char
cap, the .claude/no-tracker marker check, and ANSI-stripping. The JIRA-CLI
-specific adapter lives in jira_scope_fetch.py.

DEV_REVIEW_JIRA_TIMEOUT overrides the subprocess timeout (seconds, default 15).
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tracker_config import config_path, get_field

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

LIGHT_CAP = 4000
FULL_CAP = 8000


def _timeout():
    try:
        return float(os.environ.get("DEV_REVIEW_JIRA_TIMEOUT", "15"))
    except ValueError:
        return 15.0


def fetch(mode, key, repo_root, comments):
    if not key:
        return ""
    if repo_root and (Path(repo_root) / ".claude" / "no-tracker").exists():
        return ""
    prefix = key.split("-", 1)[0]
    try:
        cmd = get_field(prefix, "scope_fetch_cmd", config_path())
    except (SystemExit, AttributeError):
        return ""
    if not cmd or cmd == "-":
        return ""
    n = "0" if mode == "light" else str(comments)
    try:
        argv = shlex.split(cmd) + [key, n]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_timeout())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return ""
    if proc.returncode != 0:
        return ""
    text = _ANSI_RE.sub("", proc.stdout).strip()
    cap = LIGHT_CAP if mode == "light" else FULL_CAP
    return text[:cap]


def main():
    ap = argparse.ArgumentParser(description="Fetch ticket scope via the tracker adapter (degrades silently).")
    ap.add_argument("--mode", choices=["light", "full"], required=True)
    ap.add_argument("--key", default="")
    ap.add_argument("--repo-root", default="")
    ap.add_argument("--comments", type=int, default=10)
    args = ap.parse_args()
    sys.stdout.write(fetch(args.mode, args.key.strip(), args.repo_root, args.comments))


if __name__ == "__main__":
    main()
