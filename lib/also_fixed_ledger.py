#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Per-worktree ledger of In-scope improvements (docs/canonical-flow.md "In-scope improvements").

Two files per branch in the worktree's own git dir (survive compaction and session changes, never committed):
the full list, for the MR body, and the pending subset not yet carried by a commit message.

  add         --repo W LINE...   append each LINE not already listed (full list + pending); a leading "- "/"* " is dropped
  pending     --repo W           print the pending lines
  section     --repo W           print the MR body's "### Also fixed" section, or nothing when the full list is empty
  commit-done --repo W           empty the pending list (a commit message now carries it)
  drop        --repo W LINE...   remove each LINE from both lists (a reverted improvement); rc 1 if one is not listed
  clear       --repo W           empty both lists (nothing downstream will read them)
"""
import argparse
import subprocess
import sys
from pathlib import Path

PREFIX = "simo-dev-also-fixed"


def git_dir(repo: str) -> Path:
    out = subprocess.run(["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-dir"], capture_output=True, check=True,
                         encoding="utf-8", errors="surrogateescape")
    return Path(out.stdout.strip())


def branch_key(repo: str) -> str:
    out = subprocess.run(["git", "-C", repo, "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True,
                         encoding="utf-8", errors="surrogateescape")
    return out.stdout.strip().replace("/", "-") if out.returncode == 0 else "detached"


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="surrogateescape").splitlines() if path.exists() else []


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8", errors="surrogateescape")


def normalize(lines: list[str]) -> list[str]:
    return [ln.strip().removeprefix("- ").removeprefix("* ").strip() for ln in lines if ln.strip()]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["add", "pending", "section", "commit-done", "drop", "clear"])
    p.add_argument("--repo", required=True)
    p.add_argument("lines", nargs="*")
    args = p.parse_args()
    if not Path(args.repo).is_absolute():
        print(f"ERROR: --repo must be an absolute path, got {args.repo!r}", file=sys.stderr)
        return 2
    try:
        gd = git_dir(args.repo)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: not a git repo: {args.repo}: {e.stderr.strip()}", file=sys.stderr)
        return 2
    key = branch_key(args.repo)
    ledger, pending = gd / f"{PREFIX}.{key}.md", gd / f"{PREFIX}.{key}.pending.md"
    listed, waiting = read_lines(ledger), read_lines(pending)
    if args.cmd == "add":
        for ln in normalize(args.lines):
            if ln not in listed:
                listed.append(ln)
                waiting.append(ln)
        write_lines(ledger, listed)
        write_lines(pending, waiting)
    elif args.cmd == "pending":
        for ln in waiting:
            print(ln)
    elif args.cmd == "section":
        if listed:
            print("### Also fixed")
            print()
            for ln in listed:
                print(f"- {ln}")
    elif args.cmd == "commit-done":
        write_lines(pending, [])
    elif args.cmd == "drop":
        gone = normalize(args.lines)
        missing = [ln for ln in gone if ln not in listed]
        write_lines(ledger, [ln for ln in listed if ln not in gone])
        write_lines(pending, [ln for ln in waiting if ln not in gone])
        for ln in missing:
            print(f"not listed: {ln}", file=sys.stderr)
        if missing:
            return 1
    else:
        write_lines(ledger, [])
        write_lines(pending, [])
    return 0


if __name__ == "__main__":
    # A Windows pipe/redirect defaults to the ANSI code page (cp1252), which cannot encode
    # most non-ASCII text. stderr keeps backslashreplace: reconfigure() without errors= resets it to strict.
    # stdout keeps surrogateescape: ledger lines come from argv, which may not be valid UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
