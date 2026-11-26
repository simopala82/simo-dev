#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Print the merge brief `simo-dev:mr-followup` step 7.6 hands to a fresh session.

After a clean exit only the merge and the post-merge steps are left, and they need literals, not
the parked session's history. The brief carries those literals, plus what this script derives:
the task ID (`flow_telemetry.current_task`), the ticket key (`derive_ticket_key.derive`), the main
checkout, and the parked session's ID. One `key: value` per line; the `task id:` line has the
shape `flow_telemetry.prompt_ids` reads, so the fresh session joins the task when it invokes
`simo-dev:mr-followup` (the Skill hook's `adopt`). No task ID → the line is left out.

    merge_brief.py --repo <worktree-abs> --mr <url> --branch <b> --base <base> --head <sha>
                   [--qa-gate <date>] [--session <parked-session-id>]
    merge_brief.py task-state --task <task-id>

`task-state` (the Merge entry's first check) prints `closed` when the task already has an
`outcome` (post-merge already ran somewhere), else `open`.
rc 0 printed, 2 `--repo` is not a git checkout or `--task` is not a task ID.
The main checkout is the parent of `--git-common-dir`: right for a normal checkout, the bare
repo's parent for a worktree of a bare repo (post-merge's cleanup guard stops on that).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import derive_ticket_key  # noqa: E402
import flow_telemetry  # noqa: E402

HEADER = "merge brief (simo-dev:mr-followup merge)"


def main_checkout(repo: str) -> str:
    out = subprocess.run(["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise ValueError(f"not a git checkout: {repo}")
    return str(Path(out.stdout.strip()).resolve().parent)


def brief(repo: str, mr: str, branch: str, base: str, head: str, qa_gate: str = "", session: str = "") -> str:
    main = main_checkout(repo)
    task = flow_telemetry.current_task(session, repo) if session else flow_telemetry.flow_state.worktree_task(repo)
    lines = [HEADER]
    if task:
        lines.append(f"task id: {task}")
    lines += [
        f"mr: {mr}",
        f"branch: {branch}",
        f"base: {base}",
        f"head: {head}",
        f"worktree: {repo}",
        f"main checkout: {main}",
        f"ticket: {derive_ticket_key.derive(branch, repo) or 'none'}",
        f"qa gate: {'passed ' + qa_gate if qa_gate else 'not run'}",
        f"parked session: {session or 'unknown'}",
    ]
    return "\n".join(lines)


def task_state(task: str) -> str:
    if not flow_telemetry.TASK_ID_RE.fullmatch(task):
        raise ValueError(f"not a task id: {task!r}")
    events = flow_telemetry.read_events(flow_telemetry.task_file(task))
    return "closed" if any(e.get("type") == "outcome" for e in events) else "open"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["task-state"]:
        p = argparse.ArgumentParser(prog="merge_brief.py task-state")
        p.add_argument("--task", required=True)
        a = p.parse_args(argv[1:])
        try:
            print(task_state(a.task))
        except ValueError as exc:
            print(f"merge_brief: {exc}", file=sys.stderr)
            return 2
        return 0
    p = argparse.ArgumentParser(prog="merge_brief.py")
    p.add_argument("--repo", required=True)
    p.add_argument("--mr", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--qa-gate", default="")
    p.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    a = p.parse_args(argv)
    try:
        print(brief(a.repo, a.mr, a.branch, a.base, a.head, a.qa_gate, a.session))
    except ValueError as exc:
        print(f"merge_brief: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
