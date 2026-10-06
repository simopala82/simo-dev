#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read-only listing of the files a commit can include: tracked changes plus untracked files.

Shared by hooks/review_loop_warn.py and skills/commit/SKILL.md step 1; both feed the
result to lib/review_marker.py `check()`.

Read-only: status porcelain v2 under --no-optional-locks, never porcelain `git diff`,
which refreshes a stat-dirty entry and writes .git/index back through index.lock
whatever GIT_OPTIONAL_LOCKS says (git 2.55.0, measured 2026-10-06) -- in a shared
checkout that lock collides with another session's `git add`/`commit`. The flag is
load-bearing: plain `git status` rewrites the index the same way (measured). Not
`diff-index` either: without --cached it lists a stat-dirty file as modified, and it
detects no renames. The listing matches the old `diff --name-only HEAD` on the states
observed (18 status states, git 2.55.0 and Apple git 2.54, 2026-10-06), not by proof.

-z on both listings, split on NUL: without it git C-quotes a path holding a tab, a
carriage return, a quote, a backslash or a non-ASCII byte, and that literal is
neither on disk nor in lib/review_marker.py's deletion set, so check() calls the
list unusable. lib/review_marker.py's _tracked_deletions docstring documents this
as an invariant both callers must uphold.

Captured as BYTES, not text=True: universal-newline translation silently rewrites a
literal \\r inside a filename to \\n, which is exactly the kind of byte -z exists to
preserve (measured: a "we\\rird.txt" path came back as "we\\nird.txt" under text=True).

Decoded with surrogateescape, not "replace": lib/review_marker.py's _tracked_deletions
decodes the same kind of raw -z path with surrogateescape, and the two must agree on the
exact string for a filename holding a genuinely invalid UTF-8 byte -- "replace" collapses
it to U+FFFD (lossy, re-encodes to different bytes), while surrogateescape round-trips.

CLI: `changed_files.py --repo <path>` writes each path followed by NUL to stdout, rc 0;
rc 2 when the listing cannot be produced. Pass the work tree's top level: from a subdirectory
`ls-files --others` prints paths relative to it while porcelain v2 prints them from the root,
so the list would mix two forms. Both callers pass the top level.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def changed_tracked(toplevel: str) -> list[bytes]:
    """Raw paths of tracked files differing from HEAD, staged or not; a rename yields its
    new path, or its source when the new path is gone from disk. Porcelain v2 records:
    `1 XY sub mH mI mW hH hI path`, `2 XY sub mH mI mW hH hI Xscore path` followed by the
    source path as its own -z token, `u XY sub m1 m2 m3 mW h1 h2 h3 path`.

    A path gone from disk (mW 000000) is kept only if HEAD has it -- then it is a real
    deletion. One in neither (`git add` then `rm`) would make review_marker check() find it
    missing from disk and absent from its deletion set, and call the whole list unusable.
    HEAD membership is read from the tree, not from the record's modes: `add -N` reports a
    non-zero mH, and after `git stash apply` a `u` record's stage 2 is the index, not HEAD.

    A failing `status` raises subprocess.CalledProcessError instead of yielding an empty,
    "nothing changed" list.
    """
    out = subprocess.run(
        ["git", "--no-optional-locks", "-C", toplevel, "status", "--porcelain=v2", "-z",
         "--untracked-files=no"],
        capture_output=True, check=True,
    ).stdout
    tokens = iter(out.split(b"\0"))
    paths, gone = [], []
    for tok in tokens:
        kind = tok[:2]
        if kind == b"1 ":
            f = tok.split(b" ", 8)
            (gone if f[5] == b"000000" else paths).append(f[8])
        elif kind == b"2 ":
            f = tok.split(b" ", 9)
            orig = next(tokens, b"")   # the rename's source path, its own -z token
            # dst gone from disk. R: only the source's deletion remains; C: the source is
            # listed already as modified, and the caller's set dedups it.
            if f[5] == b"000000":
                gone.append(orig)
            else:
                paths.append(f[9])
        elif kind == b"u ":
            f = tok.split(b" ", 10)
            (gone if f[6] == b"000000" else paths).append(f[10])
    if gone:
        head = subprocess.run(
            ["git", "--no-optional-locks", "-C", toplevel, "ls-tree", "-r", "-z", "--name-only", "HEAD"],
            capture_output=True,
        )
        # git cannot list HEAD (unborn, corrupt): keep them all -- at worst check() warns
        in_head = set(head.stdout.split(b"\0")) if head.returncode == 0 else set(gone)
        paths += [g for g in gone if g in in_head]
    return paths


def candidates(toplevel: str) -> list[str]:
    """Tracked changes plus untracked non-ignored files, decoded with surrogateescape so a
    genuinely invalid UTF-8 byte round-trips to the same string _tracked_deletions builds."""
    untracked = subprocess.run(
        ["git", "--no-optional-locks", "-C", toplevel, "ls-files", "--others",
         "--exclude-standard", "-z"],
        capture_output=True, check=True,
    ).stdout
    return sorted({
        f.decode("utf-8", errors="surrogateescape")
        for f in [*changed_tracked(toplevel), *untracked.split(b"\0")] if f
    })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    args = ap.parse_args(argv)
    # An empty list reads as "nothing to review" downstream, so a listing that could not
    # be produced must exit non-zero, never print nothing at rc 0.
    try:
        files = candidates(args.repo)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"changed_files: cannot list {args.repo}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(b"".join(
        p.encode("utf-8", errors="surrogateescape") + b"\0" for p in files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
