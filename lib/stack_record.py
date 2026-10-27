#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Remember the parent a stacked branch targets, and spot when that parent was merged rewritten.

    stack_record.py record --repo PATH --target BRANCH
    stack_record.py check  --repo PATH --base BRANCH

record: BRANCH set and not the default branch (origin/HEAD, else origin/main|master) -> stores
branch.<current>.simoDevStackParent and branch.<current>.simoDevStackBase (the parent tip this
branch is built on: merge-base HEAD origin/BRANCH, or the stored one when the parent was
rebased under it); otherwise unsets both, unless the recorded parent is still on the remote
(rebased so detection fell back to the default branch, or a hand retarget): then it keeps both.
check: rc 1 and `rebase: git -C <repo> rebase --onto origin/<BASE> <sha>` on stdout when the
recorded parent is not BASE and no longer on the remote, <sha> is still in HEAD's history and
<sha> is not in origin/BASE (the parent landed rewritten: squash or rebase merge), followed by one
`drops: <oneline>` line per commit the rebase would drop (origin/BASE..<sha>). rc 0 otherwise.
Exit codes: 0 ok / nothing to do; 1 rebase suggested (check only); 2 git error.
"""
import argparse
import shlex
import subprocess
import sys

PARENT, FORK = "simoDevStackParent", "simoDevStackBase"


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def ok(repo, *args):
    return git(repo, *args)[0] == 0


def fail(msg):
    sys.stderr.write(f"stack_record: {msg}\n")
    return 2


def default_branch(repo):
    _, head_ref = git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if head_ref:
        return head_ref.removeprefix("refs/remotes/origin/")
    for cand in ("main", "master"):
        if ok(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{cand}"):
            return cand
    return ""


def record(repo, branch, target):
    if not target or target == default_branch(repo):
        _, old_parent = git(repo, "config", f"branch.{branch}.{PARENT}")
        if old_parent and ok(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{old_parent}"):
            return 0  # parent still on the remote (rebased away from detection, or a hand retarget): keep its fork
        for key in (PARENT, FORK):
            git(repo, "config", "--unset", f"branch.{branch}.{key}")
        return 0
    rc, fork = git(repo, "merge-base", "HEAD", f"origin/{target}")
    if rc != 0 or not fork:
        return fail(f"no merge-base between HEAD and origin/{target}")
    _, old_parent = git(repo, "config", f"branch.{branch}.{PARENT}")
    _, old_fork = git(repo, "config", f"branch.{branch}.{FORK}")
    if (old_parent == target and old_fork and ok(repo, "merge-base", "--is-ancestor", old_fork, "HEAD")
            and ok(repo, "merge-base", "--is-ancestor", fork, old_fork)):
        fork = old_fork  # parent rebased under us: the new merge-base fell back below the real fork
    git(repo, "config", f"branch.{branch}.{PARENT}", target)
    git(repo, "config", f"branch.{branch}.{FORK}", fork)
    return 0


def check(repo, branch, base):
    if not ok(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}"):
        return fail(f"origin/{base} does not resolve")
    _, parent = git(repo, "config", f"branch.{branch}.{PARENT}")
    _, fork = git(repo, "config", f"branch.{branch}.{FORK}")
    if not parent or not fork or parent == base:
        return 0
    if ok(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{parent}"):
        return 0  # parent still on the remote: a hand retarget, not a merge
    if not ok(repo, "merge-base", "--is-ancestor", fork, "HEAD"):
        return 0  # already rebased off the old parent
    if ok(repo, "merge-base", "--is-ancestor", fork, f"origin/{base}"):
        return 0  # parent merged as-is: its commits are in base already
    print(f"rebase: git -C {shlex.quote(repo)} rebase --onto {shlex.quote(f'origin/{base}')} {fork}")
    # What the rebase drops: a recorded "parent" that was a snapshot of this branch lists its own work here.
    _, dropped = git(repo, "log", "--oneline", f"origin/{base}..{fork}")
    for line in dropped.splitlines():
        print(f"drops: {line}")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["record", "check"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--target", default="")
    ap.add_argument("--base", default="")
    a = ap.parse_args()
    _, branch = git(a.repo, "branch", "--show-current")
    if not branch:
        return fail("detached HEAD or not a repo")
    if a.action == "record":
        return record(a.repo, branch, a.target)
    if not a.base:
        return fail("check needs --base")
    return check(a.repo, branch, a.base)


if __name__ == "__main__":
    sys.exit(main())
