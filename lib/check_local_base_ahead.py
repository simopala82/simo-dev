#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Guard against generating a diff/review range whose local base carries
unpushed commits already in the current branch's ancestry.

Mirror-image of check_base_freshness.py: that one stops when origin/<base>
has advanced *beyond* the branch's fork point; this one stops when the
*local* <base> is ahead of origin/<base> and those extra commits are
already part of the current branch's ancestry (e.g. the branch was
created from, or later rebased/merged onto, a local base that was never
pushed). A three-dot `origin/<base>...HEAD` diff (or an equivalent
two-dot merge-base computation) would then include those local-only
commits as if they belonged to the branch.

merge-base --is-ancestor guards against false positives from unrelated
local-base work the current branch never incorporated.

Usage:
    check_local_base_ahead.py --repo PATH --base BRANCH

--repo and --base are both required -- unlike check_base_freshness.py,
every call site already knows its own target/base branch name from
context, so there is no default-base resolution here.

Exit codes:
    0  safe -- local base not ahead, or ahead but not an ancestor of HEAD
    1  STOP -- local base is ahead of origin/<base> and those commits are
       in HEAD's ancestry
    2  error -- git failed (never a false STOP)
"""
from __future__ import annotations

import argparse
import subprocess
import sys


class GitError(Exception):
    pass


def _git(repo: str, args: list[str]) -> str:
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise GitError(f"git {' '.join(args)}: {detail.strip()}") from e


def _is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    # `merge-base --is-ancestor` exits 1 for a legitimate "not an ancestor" --
    # that is a real False, not an error. Any OTHER non-zero exit (bad ref,
    # missing object, unfetched branch) is a git failure and must raise, or
    # an unverifiable state would silently read as "safe" (rc 0).
    try:
        subprocess.run(
            ["git", "-C", repo, "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True, text=True, check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:
            return False
        detail = (e.stderr or "").strip()
        raise GitError(f"git merge-base --is-ancestor {ancestor} {descendant}: {detail}") from e
    except FileNotFoundError as e:
        raise GitError(f"git merge-base --is-ancestor: {e}") from e


def analyze(repo: str, base: str) -> tuple[bool, list[str]]:
    """Return (local_base_ahead, commit_log_lines).

    local_base_ahead is True only when the local <base> is an ancestor of
    HEAD (i.e. its extra commits are already baked into the current
    branch) AND origin/<base>..<base> is non-empty (there is at least one
    such extra commit).
    """
    if not _is_ancestor(repo, base, "HEAD"):
        return (False, [])
    commits = _git(repo, ["rev-list", f"origin/{base}..{base}"])
    commit_lines = [line for line in commits.splitlines() if line]
    if not commit_lines:
        return (False, [])
    log = _git(repo, ["log", "--oneline", f"origin/{base}..{base}"])
    return (True, [line for line in log.splitlines() if line])


def decide(ahead: bool, commits: list[str], base: str = "<base>") -> tuple[int, str]:
    if not ahead:
        return (0, "")
    listed = "\n".join(f"  {c}" for c in commits)
    return (
        1,
        f"STOP: local '{base}' is ahead of origin/{base}, and those commits are in this "
        "branch's ancestry — they would show up in the diff as unrelated changes:\n"
        f"{listed}\n"
        "There is no bypass: ask the user for explicit confirmation before pushing the "
        f"local '{base}' branch (a protected-branch push) — never push it unasked. If the "
        "user declines, this cannot proceed until they do (or the branch is rebased onto a "
        "base that no longer carries these commits). Once pushed, re-run — the calling step "
        "already re-fetches origin before invoking this check, so the retry passes cleanly "
        "with no further action.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args(argv)

    try:
        ahead, commits = analyze(args.repo, args.base)
    except (GitError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2

    rc, message = decide(ahead, commits, args.base)
    if message:
        print(message, file=sys.stdout if rc == 1 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
