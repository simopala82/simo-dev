#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Tell simo-dev:task-intake where an existing piece of work re-enters the flow.

    resume_point.py --repo PATH [--base BRANCH] [--mr-rc RC] [--no-fetch]

Reads the checkout at PATH and prints one JSON object. `entry` is the first rule that matches:

    stop          a protected branch (master/main/develop or the base) carries tracked changes
                  or local commits (not on origin/<branch>, else ahead of origin/<base>), or the
                  branch's MR/PR is merged or closed and tracked changes or unpushed commits are
                  left on it
    intake        nothing to resume: a protected branch with only untracked files or nothing,
                  a merged or closed MR/PR with only untracked files or nothing local, or a
                  branch with no uncommitted change and no commit ahead of origin/<base>
    mr-followup   the branch's MR/PR is open and nothing is uncommitted or unpushed (`plans` still
                  lists a plan the branch adds: an MR/PR opened before the plan ran)
    review-loop   the branch's MR/PR is open with new local work, or there is work and it is
                  not only intake artifacts
    plan-execute  the branch adds a plan under docs/plans/ and changes nothing but intake
                  artifacts (CONTEXT.md, docs/adr, docs/specs, docs/plans)

Changes = uncommitted paths (untracked included, both sides of a rename) plus the paths of
commits ahead of origin/<base> (renames split into delete + add). --mr-rc is check_mr_state.py's exit code for the branch (0 open, 1 merged or
closed, 2 unverifiable, 3 none); omitted, negative (-1: not checked) or on a detached HEAD, the
MR/PR is not considered.
rc 2 counts as none, with a warning. On a merged or closed MR/PR whose remote branch is gone,
commits made after the merge cannot be told apart and read as nothing local: on a harness tree step
6's merged-branch path checks them; elsewhere they stay on the old branch. --base defaults to origin/HEAD, else origin/main, else
origin/master. `git fetch origin <base>` (and the branch) runs first unless --no-fetch; a failed
fetch is a warning.

Other keys: reason, branch ("" when detached), base, protected, dirty (count), ahead (count),
unpushed (count of commits not on origin/<branch>, null when that ref does not exist), plans
(paths the branch adds under docs/plans/ that still exist on disk), mr (open|terminal|absent|unknown|not-checked),
warnings.

Exit codes: 0 printed; 2 not a git checkout, or origin/<base> does not resolve (stderr names it).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROTECTED = frozenset({"master", "main", "develop"})
INTAKE_ARTIFACTS = ("CONTEXT.md", "docs/adr/", "docs/specs/", "docs/plans/")
PLAN_DIR = "docs/plans/"
MR_STATES = {0: "open", 1: "terminal", 2: "unknown", 3: "absent"}


def git(repo: str, *args: str) -> tuple[int, str]:
    # --no-optional-locks: a plain `git status` refreshes the index under index.lock, which can
    # collide with another session committing in the same (shared main) checkout.
    r = subprocess.run(["git", "--no-optional-locks", "-C", repo, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def resolves(repo: str, ref: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", ref)[0] == 0


def default_base(repo: str) -> str:
    rc, out = git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if rc == 0 and out.strip():
        return out.strip().replace("refs/remotes/origin/", "")
    for name in ("main", "master"):
        if resolves(repo, f"refs/remotes/origin/{name}"):
            return name
    return ""


def status_entries(out: str) -> list[tuple[str, list[str]]]:
    """`git status --porcelain -z` -> (XY, paths): a rename or copy carries both of its paths."""
    result, fields = [], iter(out.split("\0"))
    for field in fields:
        if len(field) > 3:
            xy, paths = field[:2], [field[3:]]
            if "R" in xy or "C" in xy:
                paths.append(next(fields, ""))
            result.append((xy, [p for p in paths if p]))
    return result


def zpaths(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def is_artifact(path: str) -> bool:
    return any(path == a or (a.endswith("/") and path.startswith(a)) for a in INTAKE_ARTIFACTS)


def inspect(repo: str, base: str | None, mr_rc: int | None, fetch: bool) -> tuple[int, dict]:
    if git(repo, "rev-parse", "--git-dir")[0] != 0:
        return 2, {"error": f"{repo} is not a git checkout"}
    warnings: list[str] = []
    base = base or default_base(repo)
    branch = git(repo, "symbolic-ref", "--short", "--quiet", "HEAD")[1].strip()
    if fetch and base:
        refs = [base] + ([branch] if branch and branch != base else [])
        for ref in refs:
            if git(repo, "fetch", "--quiet", "origin", ref)[0] != 0 and ref == base:
                warnings.append(f"fetch of origin/{base} failed: counts are against the local copy")
    if not base or not resolves(repo, f"refs/remotes/origin/{base}"):
        return 2, {"error": f"origin/{base or '<default>'} does not resolve: no base to compare against"}

    entries = status_entries(git(repo, "status", "--porcelain", "-z", "--untracked-files=all")[1])
    dirty_paths = [p for _, paths in entries for p in paths]
    tracked_dirty = any(xy != "??" for xy, _ in entries)
    ahead = int(git(repo, "rev-list", "--count", f"origin/{base}..HEAD")[1].strip() or 0)
    span = f"origin/{base}...HEAD"
    committed = zpaths(git(repo, "diff", "--name-only", "-z", "--no-renames", span)[1]) if ahead else []
    added_committed = zpaths(git(repo, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=A",
                                 span, "--", PLAN_DIR)[1]) if ahead else []
    dirty_plans = [p for xy, paths in entries for p in paths[:1] if p.startswith(PLAN_DIR)
                   and not resolves(repo, f"origin/{base}:{p}")]
    plans = sorted({p for p in added_committed + dirty_plans
                    if p.endswith(".md") and (Path(repo) / p).is_file()})
    changed = set(dirty_paths) | set(committed)

    unpushed = None
    if branch and resolves(repo, f"refs/remotes/origin/{branch}"):
        unpushed = int(git(repo, "rev-list", "--count", f"origin/{branch}..HEAD")[1].strip() or 0)

    protected = branch in PROTECTED or branch == base
    if not branch or protected or mr_rc is None or mr_rc < 0:
        mr = "not-checked"
    else:
        mr = MR_STATES.get(mr_rc, "unknown")
        if mr == "unknown":
            warnings.append("MR/PR state unverifiable: treated as no MR/PR")

    def result(entry: str, reason: str) -> tuple[int, dict]:
        return 0, {"entry": entry, "reason": reason, "branch": branch, "base": base,
                   "protected": protected, "dirty": len(entries), "ahead": ahead,
                   "unpushed": unpushed, "plans": plans, "mr": mr, "warnings": warnings}

    if protected:
        local_commits = unpushed if unpushed is not None else ahead
        if tracked_dirty or local_commits:
            return result("stop", f"protected branch {branch} carries changes: create a feature branch first")
        return result("intake", f"protected branch {branch}, nothing to resume")
    if mr == "open":
        if entries or unpushed is None or unpushed > 0:
            return result("review-loop", "open MR/PR with new local work")
        return result("mr-followup", "open MR/PR, nothing uncommitted or unpushed")
    if mr == "terminal":
        if tracked_dirty or (unpushed or 0) > 0:
            return result("stop", f"the MR/PR of {branch} is merged or closed: start new work from a fresh branch")
        return result("intake", f"the MR/PR of {branch} is merged or closed, no tracked change or unpushed commit on it")
    if not changed:
        return result("intake", "no uncommitted change and no commit ahead of the base")
    if plans and all(is_artifact(p) for p in changed):
        return result("plan-execute", "the branch adds a plan and nothing but intake artifacts")
    return result("review-loop", "work on the branch, no MR/PR yet")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base")
    parser.add_argument("--mr-rc", type=int)
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args(argv)
    rc, data = inspect(args.repo, args.base, args.mr_rc, not args.no_fetch)
    if rc:
        print(f"resume_point: {data['error']}", file=sys.stderr)
        return rc
    print(json.dumps(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
