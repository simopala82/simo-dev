#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Partition a plan file's tasks into consecutive batches of mutually
file-disjoint tasks, for simo-dev:plan-execute's execution delegation gate.

A task's file set is the first backtick-quoted token on each "- <label>:"
line of that task's **Files:** block (a leading Create:/Modify:/Test:
label is skipped over; a trailing :123-145 line-range suffix on a
Modify: entry is stripped before comparing paths). A second backtick
span later on the same line -- simo-dev:plan-write's own prose-task
anchor, e.g. "- Modify: `doc.md` at `<anchor>`" -- is not a path and is
ignored, UNLESS the two spans are joined by an arrow ("->" or the
unicode "→"), e.g. "- Modify (move): `old/path.py` -> `new/path.py`" --
there, both spans are paths (the move's source and destination) and
both enter the file set, so a later task that touches the freed-up
source path is correctly treated as overlapping rather than
independent. A task's **Interfaces:** block (Consumes:/Produces:) is never
read -- only **Files:** overlap decides whether two tasks batch together;
the operational implication for the caller is documented in
simo-dev:plan-execute's SKILL.md, in the "Known, unmitigated limitation"
paragraph. Tasks are read in plan order and merged into the
current batch only when their file set is disjoint from every task
already in it; otherwise the current batch closes and a new one starts
with that task. This never reorders tasks -- plan order is preserved
across batch boundaries -- it only ever groups adjacent, file-disjoint
tasks.

Usage:
    detect_independent_task_groups.py <plan-file>

Prints one line per batch: space-separated task numbers, in plan order,
one batch per line. Exits 2 if the plan file cannot be found or contains
no '## Task N:' or '### Task N:' headings.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TASK_HEADER_RE = re.compile(r"^#{2,3} Task (\d+):", re.MULTILINE)
FILES_BLOCK_RE = re.compile(r"\*\*Files:\*\*\n((?:- .+\n?)+)")
# Only the FIRST backtick span on a "- <label>: `path`" line is the path --
# simo-dev:plan-write's own prose-task template (SKILL.md's "anchor and
# outcome" convention) produces lines like "- Modify: `doc.md` at `<anchor>`",
# where a second backtick span is a prose anchor, not a file. The label may
# contain spaces (e.g. "Modify (move):"), so it is matched up to the first
# ":" rather than as a single \S+ token.
LINE_PATH_RE = re.compile(r"^-\s*[^`:]+:\s*`([^`]+)`")
# A move/rename bullet -- "- <label>: `source` -> `dest`" or "... `source` → `dest`" --
# names two real paths, not a path plus a prose anchor: both must enter the
# file set so a later task touching the freed-up source overlaps correctly.
# Only "->" and "→" are recognized connectors -- not a closed set of every
# way a plan might phrase a move (e.g. "... `source` to `dest`" falls through
# to LINE_PATH_RE below and silently keeps only the first path).
MOVE_LINE_RE = re.compile(r"^-\s*[^`:]+:\s*`([^`]+)`\s*(?:->|→)\s*`([^`]+)`")


def _strip_line_range(path: str) -> str:
    return re.sub(r":\d+(-\d+)?$", "", path)


def parse_task_files(plan_text: str) -> list[tuple[int, set[str]]]:
    """Return [(task_number, file_set), ...] in plan order."""
    headers = list(TASK_HEADER_RE.finditer(plan_text))
    results: list[tuple[int, set[str]]] = []
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(plan_text)
        task_number = int(m.group(1))
        section = plan_text[start:end]
        file_set: set[str] = set()
        files_match = FILES_BLOCK_RE.search(section)
        if files_match:
            for line in files_match.group(1).splitlines():
                move_match = MOVE_LINE_RE.match(line)
                if move_match:
                    for path in move_match.groups():
                        file_set.add(_strip_line_range(path))
                    continue
                path_match = LINE_PATH_RE.match(line)
                if path_match:
                    file_set.add(_strip_line_range(path_match.group(1)))
        results.append((task_number, file_set))
    return results


def compute_batches(tasks: list[tuple[int, set[str]]]) -> list[list[int]]:
    """Greedy consecutive partition: a task joins the current batch iff
    its file set is disjoint from every task already in it; otherwise it
    closes the current batch and starts a new one. A task with an empty
    file set (no parseable **Files:** block) never joins an existing
    batch -- an empty set is trivially "disjoint" from anything, which
    would otherwise let a file-less task (e.g. a verification-only task)
    merge into a batch it has no declared relationship to -- and it never
    lets a later task join *its* own batch either, since nothing is known
    about what it touches."""
    batches: list[list[int]] = []
    batch_numbers: list[int] = []
    batch_files: set[str] = set()
    batch_has_unknown = False
    for number, file_set in tasks:
        can_join = (
            batch_numbers
            and not batch_has_unknown
            and file_set
            and not (batch_files & file_set)
        )
        if can_join:
            batch_numbers.append(number)
            batch_files |= file_set
        else:
            if batch_numbers:
                batches.append(batch_numbers)
            batch_numbers = [number]
            batch_files = set(file_set)
            batch_has_unknown = not file_set
    if batch_numbers:
        batches.append(batch_numbers)
    return batches


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: detect_independent_task_groups.py <plan-file>", file=sys.stderr)
        return 2
    plan_path = Path(argv[0])
    if not plan_path.exists():
        print(f"detect_independent_task_groups: {plan_path} not found", file=sys.stderr)
        return 2
    tasks = parse_task_files(plan_path.read_text())
    if not tasks:
        print(
            f"detect_independent_task_groups: no '## Task N:' or '### Task N:' headings in {plan_path}",
            file=sys.stderr,
        )
        return 2
    for batch in compute_batches(tasks):
        print(" ".join(str(n) for n in batch))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
