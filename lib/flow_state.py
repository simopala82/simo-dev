#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Opt-in state of the simo-dev flow: the flow is dormant until simo-dev:task-intake
switches it on.

Two keys, either one means "active":
- session flag: <active-dir>/<session_id>, written by task-intake step 1. The
  active dir is ~/.claude/state/dev-flow/active, or $SIMO_DEV_FLOW_ACTIVE_DIR.
  Lives for the session, even past the task's merge (only `off` ends it); every
  `on` write prunes flags not seen active for 7 days (a positive check refreshes it).
- worktree marker: <git-dir>/simo-dev-flow-active, written by task-intake step 6
  and removed by mr-followup's post-merge cleanup. Survives a resumed session whose
  cwd is that worktree, and is written again by a start_session child's own step 6.

Both keys may carry the task ID after a tab (`<epoch>\t<task-id>`, see
lib/flow_telemetry.py); a legacy `<epoch>` line is active with no task. An `on`
without --task-id keeps the ID already stored, and the marker inherits the session's.
clear_session_task() ends the task and keeps the flow on.

Hooks import this module; skill bodies call the CLI:
    flow_state.py is-active [--session S] [--cwd P]              rc 0 active, 1 not
    flow_state.py on|off [--session S] [--repo P] [--task-id T]  rc 0 done, 2 usage / nothing written
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MARKER_NAME = "simo-dev-flow-active"
TASK_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{4}-[A-Za-z0-9]{1,8}$")
PRUNE_AFTER_S = 7 * 24 * 3600


def active_dir() -> Path:
    return Path(os.environ.get("SIMO_DEV_FLOW_ACTIVE_DIR")
                or os.path.expanduser("~/.claude/state/dev-flow/active"))


def _safe_session(session: str) -> bool:
    return bool(session) and "/" not in session and session not in (".", "..")


def _git_dir(path: str) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out) if out else None


def _safe_task(task_id: str) -> bool:
    return not task_id or bool(TASK_ID_RE.match(task_id))


def _read_task(path: Path) -> str:
    try:
        fields = path.read_text(encoding="utf-8").strip().split("\t")
    except OSError:
        return ""
    return fields[1] if len(fields) > 1 else ""


def _usable_task(task_id: str) -> str:
    """A bad task ID is dropped, never a reason to leave the flow off."""
    if _safe_task(task_id):
        return task_id
    print(f"flow_state: ignoring malformed task id {task_id!r}", file=sys.stderr)
    return ""


def _flag_line(task_id: str) -> str:
    return f"{int(time.time())}\t{task_id}\n" if task_id else f"{int(time.time())}\n"


def session_task(session: str) -> str:
    if not _safe_session(session):
        return ""
    return _read_task(active_dir() / session)


def worktree_task(path: str) -> str:
    git_dir = _git_dir(path) if path else None
    return _read_task(git_dir / MARKER_NAME) if git_dir else ""


def session_active(session: str) -> bool:
    if not _safe_session(session):
        return False
    flag = active_dir() / session
    if not flag.is_file():
        return False
    try:
        os.utime(flag)   # a live session keeps its flag clear of the 7-day prune
    except OSError:
        pass
    return True


def worktree_active(path: str) -> bool:
    if not path:
        return False
    git_dir = _git_dir(path)
    return bool(git_dir) and (git_dir / MARKER_NAME).is_file()


def is_active(session: str, path: str) -> bool:
    return session_active(session) or worktree_active(path)


def _prune(directory: Path, now: float) -> None:
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if now - entry.stat().st_mtime > PRUNE_AFTER_S:
                entry.unlink()
        except OSError:
            pass


def set_session(session: str, on: bool, task_id: str = "") -> bool:
    if not _safe_session(session):
        return False
    task_id = _usable_task(task_id)
    directory = active_dir()
    flag = directory / session
    try:
        if on:
            task_id = task_id or session_task(session)
            directory.mkdir(parents=True, exist_ok=True)
            flag.write_text(_flag_line(task_id), encoding="utf-8")
            _prune(directory, time.time())
        else:
            flag.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def clear_session_task(session: str) -> bool:
    """End the task, not the flow: the flag stays, with no task ID."""
    if not session_active(session):
        return False
    try:
        (active_dir() / session).write_text(_flag_line(""), encoding="utf-8")
    except OSError:
        return False
    return True


def set_worktree(path: str, on: bool, task_id: str = "") -> bool:
    git_dir = _git_dir(path) if path else None
    if not git_dir:
        return False
    task_id = _usable_task(task_id)
    marker = git_dir / MARKER_NAME
    try:
        if on:
            task_id = task_id or _read_task(marker)
            marker.write_text(_flag_line(task_id), encoding="utf-8")
        else:
            marker.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow_state.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("is-active")
    check.add_argument("--session", default="")
    check.add_argument("--cwd", default="")
    for name in ("on", "off"):
        p = sub.add_parser(name)
        p.add_argument("--session", default="")
        p.add_argument("--repo", default="")
        p.add_argument("--task-id", default="")
    args = parser.parse_args(argv)

    if args.cmd == "is-active":
        return 0 if is_active(args.session, args.cwd) else 1

    on = args.cmd == "on"
    task_id = args.task_id or (session_task(args.session) if on and args.session else "")
    done = []
    if args.session and set_session(args.session, on, task_id):
        done.append("session")
    if args.repo and set_worktree(args.repo, on, task_id):
        done.append("worktree")
    if not done:
        print(f"flow: {args.cmd} failed (nothing written)", file=sys.stderr)
        return 2
    print(f"flow: {args.cmd} ({'+'.join(done)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
