#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Print the highest-priority ready-for-agent issues from this repo's local
markdown tracker (.scratch/<feature>/issues/*.md), for task-intake's no-args
queue-fetch flow. See docs/agents/issue-tracker.md for the issue file
format and docs/specs/2026-09-18-task-intake-priority-queue-design.md for
the overall mechanism.

Generic across any repo using simo-dev's local-markdown tracker option
(not specific to any one repo) -- configure in a repo's
.claude/tracker-queue-cmd:

    python3 <path to this file> --repo <repo-root>

Scans <repo-root>/.scratch/*/issues/*.md, parses each file's `Status:` and
`Priority:` header lines, keeps only `Status: ready-for-agent`, and sorts
by `Priority` (P0 first, then P1, then P2, missing Priority last),
tiebreak on the file's own name. Prints one line per matching issue:
"<path-relative-to-repo-root>\t<title>", title = the file's first `#`
heading, or the filename if none.

Exit codes:
    0   success (stdout may be empty -- zero matches is not an error, and
        a repo with no .scratch/ directory at all is not an error either)
    2   usage error (argparse default)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STATUS_RE = re.compile(r"^Status:\s*(\S+)", re.MULTILINE)
PRIORITY_RE = re.compile(r"^Priority:\s*(P[0-9]+)", re.MULTILINE)
HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
ELIGIBLE_STATUS = "ready-for-agent"
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
UNRANKED = len(PRIORITY_RANK)


def _issue_title(text: str, path: Path) -> str:
    match = HEADING_RE.search(text)
    return match.group(1).strip() if match else path.name


def _priority_rank(text: str) -> int:
    match = PRIORITY_RE.search(text)
    return PRIORITY_RANK.get(match.group(1), UNRANKED) if match else UNRANKED


def find_candidates(repo_root: Path) -> list[tuple[Path, str]]:
    eligible = []
    for path in sorted(repo_root.glob(".scratch/*/issues/*.md")):
        text = path.read_text(encoding="utf-8")
        status_match = STATUS_RE.search(text)
        if not status_match or status_match.group(1) != ELIGIBLE_STATUS:
            continue
        eligible.append((path, text))
    eligible.sort(key=lambda pair: (_priority_rank(pair[1]), pair[0].name))
    return [(path, _issue_title(text, path)) for path, text in eligible]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args(argv)

    for path, title in find_candidates(args.repo):
        print(f"{path.relative_to(args.repo)}\t{title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
