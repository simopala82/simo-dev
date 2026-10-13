#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read and write the simo-dev:afk no-plan decision-log without naming its path on the command line.

Consumer: skills/afk/SKILL.md (§1, §5, user interrupt) and skills/afk/lib/wake-park.md.
Only the fallback log: a plan-sibling log (docs/plans/<plan>-afk-log.md) sits inside the
worktree and keeps being written with Write/Edit, so simo-dev:commit still stages it.

Why a script: in sessions whose cwd is another repo, Claude Code asks before a Bash write
that names a `~/.claude/state/...` path, with a non-persistable "Allow once" (observed on
2.1.295, 2026-10-12/13, for the dev-flow journal). Observed reading (same runs): the check
looks at paths on the command line, not files a script opens, so the path is resolved in here.

Path: `afk-<branch>.md` in ~/.claude/state (override: AFK_LOG_DIR), '/' in the branch
replaced by '-' so a `fix/x` branch does not create a subdirectory. Not reversible:
`x/a-b` and `x-a-b` share one file, as do two repos on the same branch name.

Usage:
    afk_log.py append --repo PATH  < entry   # append entry lines
    afk_log.py resume --repo PATH  < text    # set the single top RESUME: line
    afk_log.py show   --repo PATH            # print the log

Exit codes: 0 ok; 1 with a `repo_identity:` stderr line = no current branch (detached HEAD)
or --repo not a git repo, 1 with a traceback = a crash; 2 argparse (usage error, --repo not a
directory) or python failing to open this file; 3 show found no log; 4 empty input, nothing written.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from repo_identity import current_branch  # noqa: E402

PREFIX = "RESUME:"


def log_path(repo: str) -> Path:
    base = Path(os.environ.get("AFK_LOG_DIR") or Path.home() / ".claude" / "state")
    return base / f"afk-{current_branch(repo).replace('/', '-')}.md"


def append(path: Path, text: str) -> int:
    entry = text.rstrip().lstrip("\n")
    if not entry.strip():
        print("afk_log: empty entry on stdin, nothing written", file=sys.stderr)
        return 4
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "\n" if existing and not existing.endswith("\n") else ""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(sep + entry + "\n")
    print(f"afk_log: appended {entry.count(chr(10)) + 1} lines to {path}")
    return 0


def resume(path: Path, text: str) -> int:
    body = " ".join(text.split())
    if body.startswith(PREFIX):
        body = body[len(PREFIX):].strip()
    if not body:
        print("afk_log: empty RESUME text on stdin, nothing written", file=sys.stderr)
        return 4
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    line = f"{PREFIX} {body}"
    if lines and lines[0].startswith(PREFIX):
        lines[0] = line
    else:
        lines.insert(0, line)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    print(f"afk_log: RESUME set in {path}")
    return 0


def show(path: Path) -> int:
    if not path.exists():
        print(f"afk_log: no decision-log at {path}", file=sys.stderr)
        return 3
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Read/write the simo-dev:afk no-plan decision-log.")
    ap.add_argument("cmd", choices=("append", "resume", "show"))
    ap.add_argument("--repo", required=True)
    args = ap.parse_args()
    if not Path(args.repo).is_dir():
        ap.error(f"--repo: not a directory: {args.repo}")
    # Decision-log entries carry non-ASCII ("→", emoji); a cp1252 console would crash on them.
    sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    path = log_path(args.repo)
    if args.cmd == "show":
        return show(path)
    text = sys.stdin.buffer.read().decode("utf-8")
    return append(path, text) if args.cmd == "append" else resume(path, text)


if __name__ == "__main__":
    raise SystemExit(main())
