#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Detect whether a plan added on this branch declares `**MR draft:** yes`.

`lib/mr_create.py prepare` (`simo-dev:commit` step 7) reads this, together with its
own `--draft` arg, to decide whether `create` (step 9) opens the MR/PR as Draft. Only plan files the branch
itself adds (`origin/<target>...HEAD`, `docs/plans/*.md`) count, read from
HEAD rather than the working tree, so a plan already on the base never turns
a later, unrelated MR into a Draft -- not even when the branch edits it (a
plan-drift resync does) -- and the answer matches what the MR will actually
carry. Works with or without a ticket key in the plan
name, unlike check_deploy_required.py's key-anchored lookup.

Usage:
    check_mr_draft.py --repo PATH --target BRANCH

Prints "1" or "0", rc 0. rc 3 (WARNING on stderr) when a committed branch
plan's MR draft value is not a bare yes/no, or when the answer is "0" but an
uncommitted (untracked, modified or gitignored) docs/plans file not on the
base declares yes or a non-bare value. A git failure (unresolvable origin/<target>, not a
repo) exits 1 with a message on stderr and prints nothing: the caller stops
rather than silently creating a non-Draft MR the plan asked to hold back.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_FIELD_RE = re.compile(r"^\*\*MR draft:\*\*[ \t]*yes[ \t]*$", re.IGNORECASE | re.MULTILINE)
_ANY_RE = re.compile(r"^\*\*MR draft:\*\*(.*)$", re.IGNORECASE | re.MULTILINE)
_PLANS = "docs/plans/*.md"


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc {res.returncode}): {res.stderr.strip()}")
    return res.stdout


def _header(text: str) -> str:
    text = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return re.split(r"^## ", text, maxsplit=1, flags=re.MULTILINE)[0]


def _declares(text: str) -> bool:
    return bool(_FIELD_RE.search(_header(text)))


def _malformed(text: str) -> bool:
    return any(v.strip().lower() not in ("yes", "no") for v in _ANY_RE.findall(_header(text)))


def _branch_plans(repo: Path, target: str) -> dict[str, str]:
    names = _git(repo, "diff", "--name-only", "-z", "--diff-filter=A", f"origin/{target}...HEAD",
                 "--", _PLANS).split("\0")
    return {name: _git(repo, "show", f"HEAD:{name}") for name in names if name}


def wants_draft(repo: Path, target: str) -> bool:
    return any(_declares(text) for text in _branch_plans(repo, target).values())


def _on_base(repo: Path, target: str, path: str) -> bool:
    res = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"origin/{target}:{path}"], capture_output=True)
    return res.returncode == 0


def uncommitted_flagged(repo: Path, target: str) -> list[tuple[str, bool]]:
    """(path, declares_yes) for each uncommitted plan, not on the base, declaring yes or a non-bare value."""
    out = _git(repo, "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all",
               "--ignored=traditional", "--", _PLANS)
    tokens = iter(out.split("\0"))
    flagged = []
    for tok in tokens:
        if not tok:
            continue
        if tok[0] in "RC" or tok[1] in "RC":
            next(tokens, None)
        path = tok[3:]
        if not (repo / path).is_file() or _on_base(repo, target, path):
            continue
        text = (repo / path).read_text()
        if _declares(text) or _malformed(text):
            flagged.append((path, _declares(text)))
    return flagged


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--target", required=True)
    args = p.parse_args(argv)
    repo = Path(args.repo)
    try:
        plans = _branch_plans(repo, args.target)
        draft = any(_declares(text) for text in plans.values())
        bad = [name for name, text in plans.items() if _malformed(text)]
        pending = [] if draft else uncommitted_flagged(repo, args.target)
    except RuntimeError as e:
        print(f"check_mr_draft: {e}", file=sys.stderr)
        return 1
    for path in bad:
        print(f"WARNING: {path} has an MR draft value other than a bare yes/no", file=sys.stderr)
    for path, yes in pending:
        what = "declares MR draft: yes" if yes else "has an MR draft value other than a bare yes/no"
        print(f"WARNING: {path} {what} but is not committed on this branch", file=sys.stderr)
    sys.stdout.write("1" if draft else "0")
    return 3 if bad or pending else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
