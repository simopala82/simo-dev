#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""simo-dev:task-intake step 7.5's pre-gate eligibility check: does this run bypass the spawn option?

Usage:
    plan_gate_precheck.py --repo <repo> --branch <branch> --row <1|2|3s|3a|3b|4> --submodule <yes|no>

`--row` and `--submodule` are the `row:` and `submodule:` literals step 3's `worktree_resolve.py resolve`
printed. Prints:
    ls-remote rc=<n>
    bypass: none | <condition>[, <condition>...]

Conditions, in order: `follow-up — origin/<branch> exists` (`git ls-remote --exit-code origin refs/heads/<branch>`: exit 2 means
absent, any other exit counts as present), `linked-worktree (row <r>)` for rows 3s/3a/3b/4, and
`submodule` for row 1 with `submodule: yes`. Why each one forces step 8's In-session row:
skills/task-intake/lib/plan-gate-rare.md. Exit 2 on a bad argument, else 0.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROWS = ("1", "2", "3s", "3a", "3b", "4")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--row", required=True, choices=ROWS)
    p.add_argument("--submodule", required=True, choices=("yes", "no"))
    args = p.parse_args(argv)

    rc = subprocess.run(["git", "-C", args.repo, "ls-remote", "--exit-code", "origin", f"refs/heads/{args.branch}"],
                        capture_output=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).returncode
    fired = []
    if rc != 2:
        fired.append(f"follow-up — origin/{args.branch} exists" if rc == 0
                     else f"follow-up — ls-remote rc {rc}, treated as existing")
    if args.row in ("3s", "3a", "3b", "4"):
        fired.append(f"linked-worktree (row {args.row})")
    if args.row == "1" and args.submodule == "yes":
        fired.append("submodule")
    print(f"ls-remote rc={rc}")
    print("bypass: " + (", ".join(fired) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
