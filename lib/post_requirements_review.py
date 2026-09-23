#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Write one ticket's draft Requirements / Acceptance Criteria block, if configured.

Single call site for both simo-dev:task-intake places that write requirements
back to a ticket: step 4.5 (ticket mode, key known immediately after step 4's
gate) and step 5.5 (prompt mode, key resolved only once step 5 concludes).
Routes through tracker_config.py's requirements_review_cmd field, so writing
is a silent no-op wherever requirements_review_cmd is unconfigured for the
ticket's prefix -- the same no-op-if-unconfigured contract every other
tracker_config field already has.

Usage:
    post_requirements_review.py <TICKET_KEY> <BODY_FILE> [--replace]

--replace is forwarded verbatim to the configured command, which otherwise
refuses to overwrite a requirements block already on the ticket.

Prints exactly one status line to stdout. Always exits 0 (via main()) --
writing back requirements is best-effort, never a gate a caller should fail
on:
    skipped: no ticket key
    skipped: requirements_review_cmd not configured for <PREFIX>
    skipped: prior requirements block present on <KEY>   (configured command exited 4)
    posted: <KEY>
    failed: requirements_review_cmd exited <rc> for <KEY>
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import tracker_config as tc  # noqa: E402


def post(key: str, body_file: str, replace: bool = False) -> str:
    if not key:
        return "skipped: no ticket key"
    prefix = key.split("-")[0].upper()
    path = tc.config_path()
    if not tc.get_field(prefix, "requirements_review_cmd", path):
        return f"skipped: requirements_review_cmd not configured for {prefix}"
    args = [key, body_file] + (["--replace"] if replace else [])
    rc = tc.run_field(prefix, "requirements_review_cmd", args, path)
    if rc == 0:
        return f"posted: {key}"
    if rc == 4:
        return f"skipped: prior requirements block present on {key}"
    return f"failed: requirements_review_cmd exited {rc} for {key}"


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or (len(argv) == 3 and argv[2] != "--replace"):
        print("usage: post_requirements_review.py <TICKET_KEY> <BODY_FILE> [--replace]", file=sys.stderr)
        return 1
    print(post(argv[0], argv[1], replace=len(argv) == 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
