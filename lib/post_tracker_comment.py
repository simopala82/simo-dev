#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Post one gate-output comment to the ticket tracker, if configured.

Single call site for every comment the canonical dev flow posts to a
ticket: task-intake's direction and plan gates, review-loop's
tests-performed and implementation-summary gates, and the verification notes
posted at MR open (skills/commit/lib/verification-notes.md). Routes through tracker_config.py's
comment_cmd field, so posting is a silent no-op wherever comment_cmd is
unconfigured for the ticket's prefix -- the same no-op-if-unconfigured
contract every other tracker_config field already has.

Usage:
    post_tracker_comment.py <TICKET_KEY> <BODY_FILE>
    post_tracker_comment.py --branch-of <REPO_DIR> <BODY_FILE>

The second form derives the key from <REPO_DIR>'s current branch with
derive_ticket_key.py (cwd <REPO_DIR>, so its no-tracker marker and
ticket-key override apply), in one call instead of two.

Prints exactly one status line to stdout. Always exits 0 (via main()) --
posting a gate comment is best-effort, never a gate a caller should fail
on:
    skipped: no ticket key
    skipped: no branch at <REPO_DIR>      (--branch-of only: git could not read the branch there)
    skipped: comment_cmd not configured for <PREFIX>
    posted: <KEY>
    failed: comment_cmd exited <rc> for <KEY>
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import derive_ticket_key  # noqa: E402
import tracker_config as tc  # noqa: E402


def post(key: str, body_file: str) -> str:
    if not key:
        return "skipped: no ticket key"
    prefix = key.split("-")[0].upper()
    path = tc.config_path()
    if not tc.get_field(prefix, "comment_cmd", path):
        return f"skipped: comment_cmd not configured for {prefix}"
    rc = tc.run_field(prefix, "comment_cmd", [key, body_file], path)
    if rc == 0:
        return f"posted: {key}"
    return f"failed: comment_cmd exited {rc} for {key}"


def key_of(repo: str) -> str | None:
    proc = subprocess.run(["git", "-C", repo, "branch", "--show-current"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return derive_ticket_key.derive(proc.stdout.strip(), cwd=repo)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--branch-of"] and len(argv) == 3:
        key = key_of(argv[1])
        if key is None:
            print(f"skipped: no branch at {argv[1]}")
            return 0
        argv = [key, argv[2]]
    if len(argv) != 2 or argv[0] == "--branch-of":
        print("usage: post_tracker_comment.py <TICKET_KEY> <BODY_FILE>\n"
              "       post_tracker_comment.py --branch-of <REPO_DIR> <BODY_FILE>", file=sys.stderr)
        return 1
    print(post(argv[0], argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
