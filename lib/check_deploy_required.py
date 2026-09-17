#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Detect whether a ticket's merged plan declares `**Deploy required:** no`.

`simo-dev:mr-followup` step 8 reads this to decide between the ordinary
`post_merge_state` and the alternate `post_merge_state_no_deploy` target.
The plan is located by `simo-dev:plan-write`'s own save-path convention
(`docs/plans/YYYY-MM-DD-<key-lower>-<name>.md`), with the ticket key
anchored right after the fixed-width date -- an unanchored substring match
would false-positive on an unrelated plan whose free-text name merely
mentions this ticket's key (caught during this feature's own plan review).

Fail-safe by construction: prints "0" for every ambiguous case (plan not
found, more than one match, field missing/unrecognized) -- never "1" unless
exactly one plan matches and it declares the field explicitly. This mirrors
the existing docs/plans/*.md scanning in check_base_freshness.py and
check_plan_drift.py (same lib/ dir), extracted here into a tested script
rather than embedded inline in mr-followup/SKILL.md's bash block.

Usage:
    check_deploy_required.py --repo PATH <TICKET-KEY>

Exits 0 on every normal invocation -- the answer is the stdout content ("0"
or "1"), not the exit code, since there is no error case distinct from "no
deploy required" here. A missing/malformed argument (e.g. no --repo) still
exits 2 before any output, same as any argparse script; not defended
against, since the sole caller (mr-followup/SKILL.md) always supplies both.
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

_FIELD_RE = re.compile(r"^\*\*Deploy required:\*\*\s*no\s*$", re.IGNORECASE | re.MULTILINE)


def is_no_deploy(repo: Path, ticket_key: str) -> bool:
    key_lower = ticket_key.lower()
    pattern = str(repo / "docs" / "plans" / f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-{key_lower}-*.md")
    matches = glob.glob(pattern)
    if len(matches) != 1:
        return False
    try:
        text = Path(matches[0]).read_text()
    except OSError:
        return False
    return bool(_FIELD_RE.search(text))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("ticket_key")
    p.add_argument("--repo", required=True)
    args = p.parse_args(argv)
    sys.stdout.write("1" if is_no_deploy(Path(args.repo), args.ticket_key) else "0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
