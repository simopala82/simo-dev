#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
r"""Read and prune the dev-flow skill log written by hooks/skill_logger.py.

Line format (TSV, 5 fields):
    <iso8601-utc>\t<session-id>\t<repo>\t<branch>\t<skill>

A line belongs to a task when its session id matches, OR when repo AND branch
both match. The session id is the primary key: the hook only sees the session
cwd, whose branch is the harness branch and not the task branch. repo+branch is
the key for sessions whose cwd IS the task worktree (spawn_task children,
terminal-launched, unattended headless callers). Legacy 3-field lines predate the session
column and never match.

KNOWN LIMIT — one session, several tasks. Their entries share a session id, and
the only thing that separates them, the task worktree, is invisible to the hook.
`read` then returns the union, so `skills used` can list an earlier task's skills
and `MODE` can be decided on them. There is deliberately no time bound: the
task's own entries are all logged BEFORE its first commit and even before its
branch exists (the hook fires when a skill is invoked, and simo-dev:task-intake
creates the branch at its step 6), so every anchor available at step 8.5 is too
late and drops the very entries this log exists to record. Over-reporting an
earlier task is the cheaper failure than losing the current one.

Growth: prune only removes repo+branch matches, so a host-launched session's
entries stay in the log for good — they carry the harness branch, never the
merged one. The file is ~70 KB after months of use; age-based pruning is a
separate concern.

Consumer: simo-dev:mr-followup step 8.5 (skills/mr-followup/lib/post-merge.md).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_LOG = Path.home() / ".claude" / "state" / "dev-flow-skills.log"
FIELDS = 5


def _fields(line: str) -> list[str] | None:
    parts = line.rstrip("\n").split("\t")
    return parts if len(parts) == FIELDS else None


def belongs(line: str, session: str, repo: str, branch: str) -> bool:
    parts = _fields(line)
    if parts is None:
        return False
    _, line_session, line_repo, line_branch, _ = parts
    if session and line_session == session:
        return True
    return bool(repo and branch) and line_repo == repo and line_branch == branch


def prunable(line: str, repo: str, branch: str) -> bool:
    """Only what the task owns unambiguously — repo+branch, never session alone.

    A session id can cover two interleaved tasks (one parked at the merge gate
    while the next starts), so pruning by it would delete the other task's
    entries: a destructive failure traded for a cosmetic one. repo+branch is
    task-specific and safe.
    """
    parts = _fields(line)
    if parts is None:
        return False
    _, _, line_repo, line_branch, _ = parts
    return bool(repo and branch) and line_repo == repo and line_branch == branch


def main() -> int:
    ap = argparse.ArgumentParser(description="Read or prune the dev-flow skill log.")
    ap.add_argument("action", choices=["read", "prune"])
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    ap.add_argument("--repo", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args()

    if not args.session and not (args.repo and args.branch):
        sys.exit("skill_log: need --session (or $CLAUDE_CODE_SESSION_ID), or both --repo and --branch")

    if not args.log.exists():
        if args.action == "read":
            print("")
        else:
            print(f"skill_log: no log at {args.log}", file=sys.stderr)
        return 0

    lines = args.log.read_text().splitlines(keepends=True)

    if args.action == "read":
        mine = [l for l in lines if belongs(l, args.session, args.repo, args.branch)]
        print(",".join(sorted({line.rstrip("\n").split("\t")[4] for line in mine})))
        return 0

    kept = [l for l in lines if not prunable(l, args.repo, args.branch)]
    # Say how many went: an unsubstituted "<the merged branch name>" placeholder
    # passes the key guard above and would otherwise prune nothing, silently.
    print(f"skill_log: pruned {len(lines) - len(kept)} lines", file=sys.stderr)

    # Read-modify-write, unsynchronized: a hook append landing between the read
    # above and the replace below is lost (same race as the awk+mv this
    # replaced). The pid in the temp name keeps two concurrent prunes from
    # interleaving into a corrupt log.
    tmp = args.log.with_name(f"{args.log.name}.tmp.{os.getpid()}")
    tmp.write_text("".join(kept))
    tmp.replace(args.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
