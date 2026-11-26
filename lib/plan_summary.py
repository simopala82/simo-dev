#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Print the plan-gate summary of a plan file, so the orchestrating session never reads the plan.

simo-dev:task-intake step 7.5 builds its Plan summary from this output: the plan itself is written and
fixed by the plan-writer subagent, and reading it here would put the whole plan back into the session
that review-loop, commit and mr-followup re-read on every call.

Usage:
    plan_summary.py <plan-file>

Output, one block per present field, in the order the gate shows them:
    Goal: <the header's Goal line, first line only>
    Approach:            (from the Architecture line, one bullet per sentence, at most 3; omitted if absent)
    Tasks: <n>           (one `- Task N: <title>` line each)
    Files touched: <n>   (the Create/Modify/Test bullets, deduped in plan order, line ranges stripped)
    Risks:               (the lines under a `Risks` heading or after a `**Risks:**` label; omitted if absent)
    Critique rounds: <header value>   (omitted when the header has none: a bare or fast-path plan)

Fenced code blocks are ignored, so a plan that embeds a plan template does not count its example tasks
or files. Exits 2 when the file cannot be read.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_base_freshness import _PLAN_FILE_BULLET_RE, _strip_fences  # noqa: E402

_GOAL_RE = re.compile(r"^\*{0,2}Goal:\*{0,2}\s*(.+)$", re.MULTILINE)
_ARCH_RE = re.compile(r"^\*{0,2}Architecture:\*{0,2}\s*(.+)$", re.MULTILINE)
_ROUNDS_RE = re.compile(r"^\*{0,2}Critique rounds:\*{0,2}\s*(.+)$", re.MULTILINE)
_TASK_TITLE_RE = re.compile(r"^#{2,3} Task (\d+):\s*(.*)$", re.MULTILINE)
_RISK_HEADING_RE = re.compile(r"^#{2,4} Risks?(?![\w-]).*$", re.MULTILINE | re.IGNORECASE)
_RISK_LABEL_RE = re.compile(r"^\*\*Risks?:\*\*\s*(.*)$", re.MULTILINE | re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`(])")
_ABBREV_END_RE = re.compile(r"(?:\b(?:e\.g|i\.e|etc|vs|cf)\.)$", re.IGNORECASE)
MAX_RISK_LINES = 5


def _risks(text: str) -> list[str]:
    m = _RISK_HEADING_RE.search(text)
    if m:
        nxt = _HEADING_RE.search(text, m.end())
        body = text[m.end():nxt.start() if nxt else len(text)]
        return [ln.strip() for ln in body.splitlines() if ln.strip() and ln.strip() != "---"][:MAX_RISK_LINES]
    m = _RISK_LABEL_RE.search(text)
    return [m.group(1).strip()] if m and m.group(1).strip() else []


def _sentences(line: str) -> list[str]:
    out: list[str] = []
    for part in _SENTENCE_RE.split(line):
        if out and _ABBREV_END_RE.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [s.strip() for s in out if s.strip()]


def summarize(text: str) -> list[str]:
    body = _strip_fences(text)
    out: list[str] = []
    goal = _GOAL_RE.search(body)
    out.append(f"Goal: {goal.group(1).strip() if goal else '(no Goal line)'}")
    arch = _ARCH_RE.search(body)
    if arch:
        out.append("Approach:")
        out += [f"- {s}" for s in _sentences(arch.group(1).strip())[:3]]
    titles = [(m.group(1), m.group(2).strip()) for m in _TASK_TITLE_RE.finditer(body)]
    out.append(f"Tasks: {len(titles)}")
    out += [f"- Task {n}: {t}" for n, t in titles]
    files: list[str] = []
    for raw in _PLAN_FILE_BULLET_RE.findall(body):
        path = re.sub(r":\d+(?:-\d+)?$", "", raw)
        if path not in files:
            files.append(path)
    out.append(f"Files touched: {len(files)}")
    out += [f"- {f}" for f in files]
    risks = _risks(body)
    if risks:
        out.append("Risks:")
        out += [r if r.startswith("- ") else f"- {r}" for r in risks]
    rounds = _ROUNDS_RE.search(body)
    if rounds:
        out.append(f"Critique rounds: {rounds.group(1).strip()}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: plan_summary.py <plan-file>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"plan_summary: {e}", file=sys.stderr)
        return 2
    print("\n".join(summarize(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
