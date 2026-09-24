#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Flag plans whose declared targets were just auto-fixed elsewhere.

`simo-dev:plan-write`'s no-placeholder convention requires a plan to embed
exact code/text for every code target it declares (never a placeholder), and
a plan file (docs/plans/*.md) stays in the repo as part of the MR diff, not a
scratch document. When `simo-dev:review-loop` or `simo-dev:mr-followup`
auto-applies a reviewer-approved fix directly to a source file, nothing today
checks whether an open plan still embeds the pre-fix version of that file
(observed twice on the same real flow, 2026-09-06/07: a regex fix in
lib/detect_independent_task_groups.py, then a concurrency-gap fix in
skills/plan-execute/SKILL.md -- both left a plan silently stale until a human
happened to notice).

This is detection only, not resync: it answers "which non-merged plan(s)
declare this file as a target", nothing about whether the specific embedded
text actually drifted. A false positive (the plan cites the file but the
reviewed passage is unaffected) costs a few seconds of reading; a false
negative reproduces the bug this script exists to close, so detection stays
intentionally coarse -- full reasoning in
docs/specs/2026-09-07-plan-drift-resync-design.md, "Why detection is
path-level, not content-level". Resyncing the embedded text is a manual Edit
by whoever applied the fix -- see that same spec's "Why resync stays
manual".

Reuses GitError / files_from_plan() / _file_exists_at_ref() / default_base()
/ fetch_base() from check_base_freshness.py (same lib/ dir) instead of
duplicating the plan-file-bullet regex and the merged-plan exclusion.

Usage:
    check_plan_drift.py [--repo PATH] [--base BRANCH] --changed-file PATH [PATH ...]

--repo defaults to cwd (pass the worktree abs path -- the Bash cwd does not
follow a worktree). --base defaults to the remote default branch, same
fallback as check_base_freshness.py's default_base().

Exit codes:
    0  clean -- no non-merged plan declares any of the changed files
    1  found -- at least one non-merged plan declares a changed file. NOT a
       flow-blocking STOP the way check_base_freshness.py's rc 1 is (that
       one needs the park_and_rebase.py recovery procedure) -- this is an
       informational signal the calling skill step resolves inline: read
       the flagged plan, resync the embedded text by hand if it drifted,
       continue.
    2  error -- git failed / base undetectable (never a false rc 1)
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

# Single-source the plan-file-bullet extraction from the sibling script.
# Inserting the script's own dir makes `import check_base_freshness` resolve
# both when run as `python3 check_plan_drift.py` and when loaded via
# importlib in tests (same pattern as afk_state.py -> repo_identity.py).
_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from check_base_freshness import GitError, _file_exists_at_ref, default_base, fetch_base, files_from_plan  # noqa: E402


def plans_mentioning(repo: str, base: str, changed_files: list[str]) -> list[tuple[str, str]]:
    """Return (plan_relpath, changed_file) pairs: every docs/plans/*.md
    under repo, not already present in origin/<base> (a merged plan's
    declared files are stale intent, same exclusion
    check_base_freshness.py's files_from_plan_dir() applies), whose
    declared **Files:** targets intersect changed_files. One pair per
    (plan, file) match -- a plan declaring two of the changed files yields
    two pairs, never merged into one.
    """
    changed = set(changed_files)
    pairs: list[tuple[str, str]] = []
    for plan_path in sorted(glob.glob(str(Path(repo) / "docs" / "plans" / "*.md"))):
        rel = f"docs/plans/{Path(plan_path).name}"
        if _file_exists_at_ref(repo, f"origin/{base}", rel):
            continue
        try:
            text = Path(plan_path).read_text()
        except FileNotFoundError:
            # an untracked plan another session moved away after the glob listed it
            continue
        declared = files_from_plan(text)
        for f in sorted(declared & changed):
            pairs.append((rel, f))
    return pairs


def decide(pairs: list[tuple[str, str]]) -> tuple[int, str]:
    if not pairs:
        return (0, "")
    lines = "\n".join(f"{plan} mentions {f}" for plan, f in pairs)
    return (1, lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base")
    parser.add_argument("--changed-file", dest="changed_files", nargs="+", required=True, metavar="PATH")
    args = parser.parse_args(argv)

    try:
        base = args.base or default_base(args.repo)
        fetch_base(args.repo, base)
        pairs = plans_mentioning(args.repo, base, args.changed_files)
    except (GitError, OSError, UnicodeDecodeError) as e:
        print(str(e), file=sys.stderr)
        return 2

    rc, message = decide(pairs)
    if message:
        print(message)
    return rc


if __name__ == "__main__":
    sys.exit(main())
