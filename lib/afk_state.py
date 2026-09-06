#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Per-(repo+branch) AFK liveness state for the dev-afk skill.

Fixes the shared-global-marker collision: the old design used two singleton
flag files (~/.claude/state/afk-active, ~/.claude/state/afk-stopped) shared by
ALL simo-dev:afk sessions, so when one session parked it wiped the markers of every
other still-running session. State is now keyed per repo+branch.

State file: ~/.claude/state/afk (override: AFK_STATE_FILE).
Entry format: <repo-id>:<branch>:<state>:<epoch>
  Parsed via rsplit(':', 3). repo-id has no ':' (normalized in repo_identity, or
  a remoteless fallback path that may legitimately contain ':'), branch has no
  ':' (git refname rule), state in {active, stopped}, epoch numeric.
repo-id / branch come from lib/repo_identity.py (single-sourced identity).
Entries expire after TTL_SECONDS; malformed/expired entries are purged on write.

States:
  active   AFK is driving this repo+branch.
  stopped  AFK was disarmed (user interrupt) or parked (tombstone) — a stale
           ScheduleWakeup re-fire must exit instead of resuming this task.
  absent   no entry — never armed, or expired/purged.

Usage:
    afk_state.py arm          [--repo PATH]  # mark repo+branch active (refresh epoch)
    afk_state.py stop         [--repo PATH]  # mark repo+branch stopped (tombstone)
    afk_state.py is-stopped   [--repo PATH]  # exit 0 iff repo+branch is stopped
    afk_state.py is-active    [--repo PATH]  # exit 0 iff repo+branch is active
    afk_state.py status       [--repo PATH]  # print active|stopped|absent for repo+branch

--repo resolves identity from PATH instead of the ambient process cwd (worktree-safe).
Omitted --repo: behavior is unchanged (resolves from the ambient cwd).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Single-source repo+branch identity from the sibling helper. Inserting the
# script's own dir makes `import repo_identity` resolve both when run as
# `python3 afk_state.py` and when loaded via importlib in tests.
_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from repo_identity import current_branch, repo_id  # noqa: E402

TTL_SECONDS = 7 * 24 * 3600
_STATES = ("active", "stopped")


def _now() -> float:
    return time.time()


def _state_file() -> Path:
    return Path(os.environ.get("AFK_STATE_FILE") or os.path.expanduser("~/.claude/state/afk"))


def _parse(line: str) -> tuple[str, str, str, int] | None:
    parts = line.rsplit(":", 3)
    if len(parts) != 4 or parts[2] not in _STATES or not parts[3].isdigit():
        return None
    return parts[0], parts[1], parts[2], int(parts[3])


def _live_entries(now: float) -> list[tuple[str, str, str, int]]:
    f = _state_file()
    if not f.exists():
        return []
    entries = []
    for line in f.read_text().splitlines():
        entry = _parse(line.strip()) if line.strip() else None
        if entry and now - entry[3] <= TTL_SECONDS:
            entries.append(entry)
    return entries


def _write(entries: list[tuple[str, str, str, int]]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(f"{r}:{b}:{s}:{t}\n" for r, b, s, t in entries))


def _state_of(rid: str, branch: str, now: float) -> str:
    for r, b, s, _ in _live_entries(now):
        if r == rid and b == branch:
            return s
    return "absent"


def _set_state(state: str, repo: str | None = None) -> int:
    now = _now()
    rid, branch = repo_id(repo), current_branch(repo)
    entries = [e for e in _live_entries(now) if (e[0], e[1]) != (rid, branch)]
    entries.append((rid, branch, state, int(now)))
    _write(entries)
    print(f"afk: {state} {rid}:{branch}")
    return 0


def arm(repo: str | None = None) -> int:
    return _set_state("active", repo)


def stop(repo: str | None = None) -> int:
    return _set_state("stopped", repo)


def status(repo: str | None = None) -> int:
    print(_state_of(repo_id(repo), current_branch(repo), _now()))
    return 0


def is_stopped(repo: str | None = None) -> int:
    return 0 if _state_of(repo_id(repo), current_branch(repo), _now()) == "stopped" else 1


def is_active(repo: str | None = None) -> int:
    return 0 if _state_of(repo_id(repo), current_branch(repo), _now()) == "active" else 1


_DISPATCH = {"arm": arm, "stop": stop, "status": status, "is-stopped": is_stopped, "is-active": is_active}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in _DISPATCH:
        p = sub.add_parser(name)
        p.add_argument("--repo", default=None, help="resolve repo-id/branch from this path instead of the ambient cwd")
    args = parser.parse_args(argv)
    if args.repo is not None and not Path(args.repo).is_dir():
        parser.error(f"--repo: not a directory: {args.repo}")
    try:
        return _DISPATCH[args.cmd](args.repo)
    except SystemExit as e:
        if args.repo is not None:
            parser.error(f"--repo {args.repo}: {e}")
        raise


if __name__ == "__main__":
    sys.exit(main())
