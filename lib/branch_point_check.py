#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Classify In-scope improvement targets (review findings, plan-execute edits) against the branch point (docs/canonical-flow.md "In-scope improvements").

  branch_point_check.py --repo W --base-sha SHA FILE:LINE...

One stdout line per target, input order: FILE:LINE<TAB>pre-existing | branch-added | error: <reason>.
FILE is repo-root-relative, LINE is in working-tree coordinates. pre-existing = FILE exists at SHA and
LINE lies outside every added range of `git diff -U0 SHA -- FILE` (working tree vs SHA; a pure deletion's
range is the two lines around it). FILE absent at
SHA is branch-added. Errors include e.g. an absolute FILE, FILE missing from the working tree, LINE
outside it, FILE at SHA but not in the index, assume-unchanged or skip-worktree, a filter attribute, a bad SHA. rc 0 when every target got a line, rc 2 on bad args.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, encoding="utf-8", errors="surrogateescape")


def added_ranges(diff: str) -> list[range]:
    ranges = []
    for line in diff.splitlines():
        m = HUNK.match(line)
        if m:
            start, count = int(m.group(1)), int(m.group(2) or "1")
            # A pure deletion (+N,0) lands between lines N and N+1: both border the branch's change.
            ranges.append(range(start, start + count) if count else range(max(start, 1), start + 2))
    return ranges


def verdict(top: str, sha: str, path: str, lineno: int) -> str:
    if git(top, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}").returncode != 0:
        return f"error: base {sha} is not a commit"
    if Path(path).is_absolute():
        return f"error: {path} is not repo-root-relative"
    work = Path(top) / path
    if not work.is_file():
        return f"error: {path} not found in working tree"
    data = work.read_bytes()
    if not 1 <= lineno <= data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0):
        return f"error: line {lineno} outside {path}"
    if git(top, "cat-file", "-e", f"{sha}:{path}").returncode != 0:
        return "branch-added"
    spec = f":(top,literal){path}"
    if git(top, "ls-files", "--error-unmatch", "--", spec).returncode != 0:
        return f"error: {path} is at base but not in the index"
    tag = git(top, "ls-files", "-v", "--", spec).stdout[:1]
    if tag.islower() or tag == "S":
        return f"error: {path} is assume-unchanged or skip-worktree"
    attr = git(top, "check-attr", "filter", "--", path).stdout.rsplit(": ", 1)[-1].strip()
    if attr not in ("unspecified", "unset"):
        return f"error: {path} has a clean filter"
    diff = git(top, "diff", "-U0", "--inter-hunk-context=0", "--text", "--no-color", "--no-ext-diff", "--no-textconv", sha, "--", spec)
    if diff.returncode != 0:
        return f"error: git diff rc {diff.returncode}"
    return "branch-added" if any(lineno in r for r in added_ranges(diff.stdout)) else "pre-existing"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("targets", nargs="+")
    args = ap.parse_args()
    parsed = []
    for t in args.targets:
        path, sep, line = t.rpartition(":")
        if not sep or not path or not line.isdigit():
            print(f"bad target (want FILE:LINE): {t}", file=sys.stderr)
            return 2
        parsed.append((t, path, int(line)))
    top = git(args.repo, "rev-parse", "--show-toplevel")
    for t, path, lineno in parsed:
        v = verdict(top.stdout.strip(), args.base_sha, path, lineno) if top.returncode == 0 else f"error: {args.repo} is not a git work tree"
        print(f"{t}\t{v}")
    return 0


if __name__ == "__main__":
    # A Windows pipe/redirect defaults to cp1252, which cannot encode most non-ASCII paths.
    sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
