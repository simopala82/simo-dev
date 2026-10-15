#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Guard against committing on a branch built on a stale base ref.

Two failure modes, one symptom (observed 2026-07-24, two separate incidents):

  1. the worktree branch was created off a stale local <base>, or
  2. a concurrent `git pull --ff-only` advanced <base> mid-session.

Either way origin/<base> ends up ahead of the branch's fork point. A two-dot
`git diff <base>` then shows the branch reverting already-merged work; worse,
when the advanced base commits restructured the SAME files the branch touches,
the work is built on outdated foundations and must be rebased before commit.

The guard is deterministic and low-noise: it STOPs only when origin/<base>
advanced beyond the fork point AND the advanced commits touch files the branch
also touches (file overlap). A base that advanced on disjoint files (the common,
benign case) exits 0 with a `note:` on stderr, not silently -- the three-dot
merge-base diff is correct there. Correct is not the same as all-clear when the
branch's prose cites files it does not change: `silent-failures.md`, section
"A claim that was true when written, and false when merged".
The branch side is measured against the working tree (index + unstaged +
untracked), because the guard runs BEFORE the commit -- the changes about to be
committed are not in HEAD yet.

Usage:
    check_base_freshness.py [--repo PATH] [--base BRANCH]
    check_base_freshness.py [--repo PATH] [--base BRANCH] --files PATH [PATH ...]
    check_base_freshness.py [--repo PATH] [--base BRANCH] --plan-files

--repo defaults to cwd (pass the worktree abs path -- the Bash cwd does not
follow a worktree). --base defaults to the remote default branch
(git symbolic-ref refs/remotes/origin/HEAD).

--files and --plan-files (mutually exclusive) REPLACE the working-tree scan
with an explicit file list, for callers that run before the branch has any
real changes to compare -- a task-intake gate, or the base_freshness_warn.py
hook -- where the working tree holds only the plan file and a bare
working-tree scan would always report a false "safe". --plan-files derives
the list from every `**Files:**` bullet across docs/plans/*.md under --repo,
except a path carrying a placeholder (`<P>/...`, `$P/...`): that one is
reported on stderr as an error naming its plan, and the plan's other paths
still count.
--plan-files excludes any docs/plans/*.md file already present in
origin/<base> (i.e. already merged) -- docs/plans/ accumulates every plan
ever written with nothing to archive them post-merge, so an unscoped union
would resurrect stale intent from unrelated, long-since-merged tasks.

Exit codes:
    0  safe -- base not advanced beyond fork point, or advanced on disjoint files
    1  STOP -- base advanced and overlaps the branch's files; rebase first
    2  error -- git failed / base undetectable, or (--plan-files) a plan's
       **Files:** path carries a placeholder and there is no overlap to report
       (never a false STOP that blocks a commit). A real overlap still wins
       rc 1; the placeholder lines then go to stderr alongside it.
"""
from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
from pathlib import Path


class GitError(Exception):
    pass


class PlanPathError(ValueError):
    def __init__(self, bad: list[str], valid: set[str]):
        listed = ", ".join(f"`{b}`" for b in bad)
        super().__init__(f"placeholder in **Files:** path {listed} -- write the literal repo-relative path")
        self.bad = bad
        self.valid = valid


def _git(repo: str, args: list[str]) -> str:
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise GitError(f"git {' '.join(args)}: {detail.strip()}") from e


def default_base(repo: str) -> str:
    try:
        ref = _git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"])
        return ref.replace("refs/remotes/origin/", "")
    except GitError:
        # origin/HEAD is not set on every clone (git fetch does not set it). Fall
        # back to the conventional default branches rather than the branch upstream
        # (a feature branch tracks origin/<itself>, not the base).
        for cand in ("main", "master"):
            try:
                _git(repo, ["rev-parse", "--verify", "--quiet", f"origin/{cand}"])
                return cand
            except GitError:
                continue
        raise GitError("cannot determine default base (origin/HEAD unset, no origin/main|master)")


def fetch_base(repo: str, base: str) -> None:
    _git(repo, ["fetch", "origin", base, "--quiet"])


def _changed_files(repo: str, a: str, b: str | None = None) -> set[str]:
    args = ["diff", "--name-only", a] + ([b] if b else [])
    out = _git(repo, args)
    return {line for line in out.splitlines() if line}


def _branch_files(repo: str, merge_base: str) -> set[str]:
    # vs the working tree, not HEAD: step 0.5 runs BEFORE the commit, so the
    # changes about to be committed live in the index/worktree, not in HEAD yet
    # (on a branch's first commit HEAD still sits at the fork point).
    tracked = _changed_files(repo, merge_base)  # merge_base .. working tree
    untracked = _git(repo, ["ls-files", "--others", "--exclude-standard"])
    return tracked | {line for line in untracked.splitlines() if line}


_PLAN_FILE_BULLET_RE = re.compile(r"^- (?:Create|Modify|Test): `([^`]+)`", re.MULTILINE)
_FENCE_LINE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
_PLACEHOLDER_RE = re.compile(r"<[^<>]+>|^\$\{?\w+\}?/")


def _strip_fences(text: str) -> str:
    """Drop fenced code blocks. A fence closes on a bare line of the same
    character at least as long as the opener (CommonMark). An opener with no
    later closing fence is kept, not dropped to EOF."""
    out: list[str] = []
    fence, held = None, []
    for line in text.splitlines(keepends=True):
        m = _FENCE_LINE_RE.match(line)
        if fence is None:
            # CommonMark: a backtick fence's info string cannot contain a backtick
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence, held = m.group(1), [line]
            else:
                out.append(line)
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
            fence, held = None, []
        else:
            held.append(line)
    return "".join(out + held)


def files_from_plan(text: str) -> set[str]:
    """Extract declared target paths from a plan's `**Files:**` bullets.

    Matches simo-dev:plan-write's documented format: `- Create/Modify/Test:
    \\`path\\``, optionally with a trailing `:123-145` line range inside the
    backticks. A subsequent `` at `<anchor>` `` token (the prose-target form)
    is a second, separate backtick pair on the same line and this regex only
    captures the first.

    Callers match these paths exactly, so a path outside a code fence that
    carries a `<name>` placeholder or a leading `$VAR/` raises PlanPathError
    (with the plan's valid paths attached); `$` elsewhere in a name is
    literal. Literal paths are collected everywhere, fences included.
    """
    paths, bad = set(), []
    unfenced = set(_PLAN_FILE_BULLET_RE.findall(_strip_fences(text)))
    for raw in _PLAN_FILE_BULLET_RE.findall(text):
        if _PLACEHOLDER_RE.search(raw):
            if raw in unfenced and raw not in bad:
                bad.append(raw)
        else:
            paths.add(re.sub(r":\d+(?:-\d+)?$", "", raw))
    if bad:
        raise PlanPathError(bad, paths)
    return paths


def _file_exists_at_ref(repo: str, ref: str, path: str) -> bool:
    try:
        _git(repo, ["cat-file", "-e", f"{ref}:{path}"])
        return True
    except GitError:
        return False


def files_from_plan_dir(repo: str, base: str, errors: list[str] | None = None) -> list[str]:
    """Union of files_from_plan() over docs/plans/*.md under repo, excluding
    any plan file already present in origin/<base> -- a merged plan's
    declared files are stale intent for the CURRENT branch, not overlap.

    With `errors`, a plan with a placeholder path is recorded there as
    `<plan-relpath>: <message>` and its valid paths still count, so one broken
    plan never hides another plan's overlap. With None, PlanPathError propagates.
    """
    paths: set[str] = set()
    for plan_path in sorted(glob.glob(str(Path(repo) / "docs" / "plans" / "*.md"))):
        rel = f"docs/plans/{Path(plan_path).name}"
        if _file_exists_at_ref(repo, f"origin/{base}", rel):
            continue
        try:
            text = Path(plan_path).read_text()
        except FileNotFoundError:
            # an untracked plan another session moved away after the glob listed it
            continue
        try:
            paths |= files_from_plan(text)
        except PlanPathError as e:
            if errors is None:
                raise
            errors.append(f"{rel}: {e}")
            paths |= e.valid
    return sorted(paths)


def analyze(repo: str, base: str, files: list[str] | None = None) -> tuple[bool, list[str]]:
    """Return (base_advanced, overlapping_files).

    files, when given (even an empty list), REPLACES the working-tree scan
    entirely -- at a task-intake gate the worktree holds only the plan file,
    which is not the relevant intent signal; the plan's declared file list is.
    """
    origin_base = f"origin/{base}"
    merge_base = _git(repo, ["merge-base", origin_base, "HEAD"])
    base_head = _git(repo, ["rev-parse", origin_base])
    if merge_base == base_head:
        return (False, [])
    base_files = _changed_files(repo, merge_base, origin_base)
    branch_files = set(files) if files is not None else _branch_files(repo, merge_base)
    return (True, sorted(base_files & branch_files))


def decide(advanced: bool, overlap: list[str], base: str = "<base>") -> tuple[int, str]:
    if not advanced:
        return (0, "")
    if not overlap:
        return (0, "note: base advanced on files disjoint from this branch; merge-base diff is safe.")
    listed = "\n".join(f"  - {f}" for f in overlap)
    return (
        1,
        "STOP: origin base advanced and touched files this branch also changes "
        "(stale-base -- branch may revert merged work):\n"
        f"{listed}\n"
        "Rebase onto the fresh base before committing. A bare `git rebase` needs a clear tree, "
        "so park the pending work first (then restore it) -- see the `simo-dev` commit skill, "
        "step 0.5:\n"
        '  R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"\n'
        '  python3 "$R/lib/park_and_rebase.py" park --repo <repo>\n'
        "  # On park rc 3: resolve, `git -C <repo> -c core.hooksPath=/dev/null rebase --continue`,\n"
        "  # then re-bind -- that is a separate shell, and an unbound R makes restore exit 2,\n"
        "  # which reads as its own 'refused, repo unchanged' while the park is still at HEAD:\n"
        '  R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"\n'
        '  python3 "$R/lib/park_and_rebase.py" restore --repo <repo>',
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--files", nargs="+", metavar="PATH", help="check these paths instead of scanning the working tree")
    group.add_argument("--plan-files", action="store_true", help="derive --files from docs/plans/*.md under --repo")
    args = parser.parse_args(argv)

    plan_errors: list[str] = []
    try:
        base = args.base or default_base(args.repo)
        fetch_base(args.repo, base)
        override_files = files_from_plan_dir(args.repo, base, plan_errors) if args.plan_files else args.files
        if override_files is not None:
            advanced, overlap = analyze(args.repo, base, files=override_files)
        else:
            advanced, overlap = analyze(args.repo, base)
    except (GitError, OSError, UnicodeDecodeError) as e:
        for err in plan_errors:
            print(err, file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 2

    rc, message = decide(advanced, overlap, base)
    if message:
        print(message, file=sys.stderr if rc == 0 else sys.stdout)
    for err in plan_errors:
        print(err, file=sys.stderr)
    return 2 if plan_errors and rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())
