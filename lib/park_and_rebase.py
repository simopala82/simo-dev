#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Park pending work in a throwaway commit, rebase onto the fresh base, put it back.

Recovery for `check_base_freshness.py` rc 1 (see the `simo-dev` `commit` skill,
step 0.5). The rebase needs a clear tree; the commit skill's steps 1, 3 and 4 all
read the working diff, so the work cannot simply be left committed.

Why a commit and not a hand-rolled park: git already carries binaries, renames,
deletes, mode bits and same-basename paths. Five review rounds on a prose procedure
doing this by hand (`diff` to a scratch file, `mv`, `apply -3`) measured work loss
on every one of those -- and on `git stash`, whose own traps are logged in the author's
own notes, §17 (worth keeping a similar log).

What a commit does NOT carry is the index/worktree split: `add -A` collapses it and
the mixed reset cannot rebuild it, so a path staged with content different from the
worktree comes back as one state. The staged path LIST is recorded and re-staged one
path at a time, and any path that could not be re-staged is named -- a single `git add`
over the whole list is atomic, so one path the rebase deleted would silently stage
none of them. The staged blob itself is not preserved. Anything that must survive
byte-exact on both sides does not belong in a park at all.

Two commands, because the operator has to resolve rebase conflicts in between:

    park_and_rebase.py park    --repo PATH [--base BRANCH]
    park_and_rebase.py restore --repo PATH [--allow-dropped]

`park` refuses up front rather than classifying its way through a dirty state, and
records the pre-park HEAD, the branch's commit list and the staged paths under the
worktree's own git dir. `restore` refuses unless that record is USABLE -- it parses, it
is a JSON object, it carries every key in `RECORD_KEYS`, and it holds each field in
`RECORD_TYPES` at the right type and, for `expected_drop`, inside `0..len(commits)` --
AND `HEAD` is still the park commit, AND the branch still carries the commits the rebase
was expected to keep -- so the reset can never land on a real commit, and a
`git rebase --skip` that dropped one is caught rather than trusted. That last check
under-predicts the drops a rebase makes legitimately, so it errs toward stopping;
`--allow-dropped` is the way through once the reflog says which happened.

Exit codes:
    park:     0 parked and rebased -- run `restore`
              2 refused or failed; the repo was NOT changed
              3 parked, rebase stopped on a conflict -- resolve, `git rebase --continue`
                (never `--skip`: it drops commits and leaves a clean-looking tree), then `restore`.
                If the conflicts are add/add or content conflicts on EVERY path a sibling branch
                touched, and this branch was earlier rebased onto that sibling's unmerged commits
                (`git rebase --autostash origin/<sibling>`) which has since merged into --base: this
                is not a normal conflict -- `git rebase --abort` and use
                `git rebase --onto origin/<base> <last-sibling-commit-sha>` instead, which drops the
                now-redundant sibling commits rather than replaying them. See silent-failures.md
                section 36.
              4 nothing left to restore -- either the tree was already clear, or the base
                already contained the park's exact diff and the rebase dropped it by patch-id
              5 the repo IS changed and needs attention -- read the message, do not branch on
                2 alone. Observed sources: an unfinished rebase; a park commit whose record could
                not be written or removed; a failed park commit whose index rollback also failed;
                a park still at HEAD under a rewritten subject; and HEAD's message being
                unreadable, which is what tells those two apart. A rebase that refuses before
                starting with nothing parked changes nothing and returns 2 instead.
    restore:  0 restored -- re-run commit step 0.5's guarded block, then continue into step 1. READ STDERR
                even here: nothing may fail once the reset has landed, so a path that could not
                be re-staged is reported on a zero exit, not by the code.
              2 refused; the tree was not touched
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

PARK_SUBJECT = "PARK: do not push"
# A per-park token in the commit BODY. The subject is what the flow reads at a glance, but
# it is also what a `prepare-commit-msg` hook rewrites; a trailer in the body survives a
# subject rewrite, so it is what tells "the rebase dropped the park by patch-id" apart from
# "the park is still HEAD under a different subject" -- two states with one signal, where
# reading the second as the first deletes the record over work headed for the push.
PARK_TOKEN_KEY = "simo-dev-park-id"
RECORD_NAME = "simo-dev-park.json"
CORRUPT = object()  # record file present but unreadable -- never the same as absent
# The keys a record must carry. Unchanged from before any of this typing existed, and kept
# separate from the table below on purpose -- the two sets are no longer the same set.
RECORD_KEYS = frozenset({"commits", "staged", "expected_drop", "token"})
# The subset whose TYPE is checked, and it is chosen by WHEN a field is read, not by which
# fields happen to exist. These two are read before `git reset HEAD~1`, inside the verdict
# that decides whether the reset is safe at all. The record is a file on disk a human can
# edit, `json.loads` yields seven types, and presence alone left the verdict to fend for
# itself: measured 2026-08-22 on `restore_verdict`, `commits: 3` and `expected_drop: "z"`
# raise an uncaught TypeError, so the caller gets rc 1 where this module's exit table
# promises rc 2; `commits: "a1b2"` counts characters and refuses with an invented number;
# and `commits: {}` or `""` make `expected` 0, which lets a branch that came out of a
# `git rebase --skip` with 1 of its 3 commits pass rc 0 -- the drop guard failing open,
# which is worse than the traceback.
#
# `token` is deliberately NOT here either, and for a different reason: nothing reads it back
# at all -- `cmd_park` writes it and compares its own local against `git log`, so a record
# whose token is not a string is still perfectly usable. Typing it would refuse a record over
# a field no code path touches, which is a worse outcome than accepting it. It stays in
# RECORD_KEYS because its presence is what tells a record written by this version from an
# older one.
#
# RECORD_TYPES must stay a SUBSET of RECORD_KEYS. The loop below reads `data[field]` after
# the presence check has run over RECORD_KEYS only, so a typed key that is not required
# raises an uncaught KeyError out of `read_record` -- rc 1 from the one function whose whole
# contract is turning a bad record into rc 2 (measured 2026-08-22 by adding `parked_times`
# to the table). `test_every_typed_field_is_also_a_required_one` pins it.
#
# `staged` and `parked_times` are deliberately NOT here. Both are read only AFTER the reset,
# where refusing is no longer available, and both already carry a post-reset guard that
# gives the work back, names what it could not re-stage and still rewinds the mtimes --
# strictly more useful there than a pre-reset refusal, and reviewed as such (merged as
# 41c414ca). Typing them here would turn that rc 0 into rc 2 and delete the recovery path.
RECORD_TYPES = {"commits": list, "expected_drop": int}
IN_PROGRESS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")


class GitError(Exception):
    pass


def _git(repo: str, args: list[str]) -> str:
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise GitError(f"git {' '.join(args)}: {detail.strip()}") from e


def _git_rc(repo: str, args: list[str]) -> tuple[int, str]:
    """Run git tolerating failure -- for the rebase, whose rc 1 is a real outcome."""
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return (out.returncode, (out.stdout + out.stderr).strip())


def toplevel(repo: str) -> str:
    """The repo root, so every path list below lives in one namespace.

    `git diff --name-only` is root-relative by default while `git ls-files` is relative
    to the cwd AND limited to its subtree, so a `--repo` pointing below the root (or the
    `.` default, from wherever the caller happens to sit) makes `refusals`' set
    intersections empty by construction -- a guard that silently never fires -- and
    makes `_restage`'s pathspecs miss. Resolving once removes the mismatch for both.
    (`--relative` / `diff.relative=true` moves the first one too; resolving still holds.)
    """
    return _git(repo, ["rev-parse", "--show-toplevel"])


def git_dir(repo: str) -> Path:
    # A linked worktree gets its own git dir, so the record is per-worktree and two
    # tasks parking at once cannot read each other's -- unlike refs/stash, which is
    # one stack shared by every worktree of the repo (silent-failures.md §17).
    return Path(_git(repo, ["rev-parse", "--path-format=absolute", "--git-dir"]))


def default_base(repo: str) -> str:
    try:
        return _git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"]).replace("refs/remotes/origin/", "")
    except GitError:
        for cand in ("main", "master"):
            try:
                _git(repo, ["rev-parse", "--verify", "--quiet", f"origin/{cand}"])
                return cand
            except GitError:
                continue
        raise GitError("cannot determine default base (origin/HEAD unset, no origin/main|master)")


def in_progress(repo: str) -> str | None:
    """Name of an operation already holding the worktree, or None."""
    gd = git_dir(repo)
    for marker in IN_PROGRESS:
        if (gd / marker).exists():
            return marker
    return None


def _lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line]


# Every path-listing read below is unquoted. Not for the intersections in `refusals` --
# git's C-quoting of non-ASCII is symmetric across `diff --name-only`, `ls-files` and
# `ls-files --others --ignored`, so those agree either way (measured 2026-08-15). It is
# for the one comparison that leaves git: `_hidden_from_add` probes the filesystem, and a
# quoted `"sub/caff\303\250.txt"` matches no file on disk, so the refusal silently drops
# exactly the paths it exists to catch. Applied to every read so all four stay aligned.
# Lifts the non-ASCII case only: `"`, `\` and control characters stay quoted regardless.
QUOTE_OFF = ["-c", "core.quotePath=false"]

# The park commit's subject is what every later check keys on, and a rebase replays the
# commit through the same hooks that could rewrite it -- so the suppression has to cover
# the replay too, not just the initial commit. Scoped to this rebase of a local feature
# branch; nothing the flow depends on runs in a commit hook.
NO_HOOKS = ["-c", "core.hooksPath=/dev/null"]


def _base_files(repo: str, base: str) -> set[str]:
    merge_base = _git(repo, ["merge-base", f"origin/{base}", "HEAD"])
    return set(_lines(_git(repo, [*QUOTE_OFF, "diff", "--name-only", merge_base, f"origin/{base}"])))


def _ignored_files(repo: str) -> set[str]:
    return set(_lines(_git(repo, [*QUOTE_OFF, "ls-files", "--others", "--ignored", "--exclude-standard"])))


def _hidden_from_add(repo: str) -> set[str]:
    """Tracked paths `git add -A` will not pick up: skip-worktree ('S') / assume-unchanged (lowercase).

    Filtered to paths that actually exist on disk. A sparse checkout marks every
    excluded path skip-worktree, and those are absent from the working tree, so the
    rebase writes them without conflict -- refusing on them would block a perfectly
    safe park and send the operator to `git update-index --no-skip-worktree`, which
    corrupts the sparse checkout instead of repairing anything (measured 2026-08-14).
    """
    hidden = set()
    for line in _lines(_git(repo, [*QUOTE_OFF, "ls-files", "-v"])):
        if line[1] != " ":
            continue
        tag, path = line[0], line[2:]
        if (tag == "S" or tag.islower()) and (Path(repo) / path).exists():
            hidden.add(path)
    return hidden


def refusals(base_files: set[str], ignored: set[str], hidden: set[str]) -> list[str]:
    """Reasons to refuse the park, as operator-facing lines. Empty list = proceed.

    Both classes are invisible to `git add -A`, so the park would not carry them
    while the rebase checkout still writes over them -- measured 2026-08-14 on a
    gitignored path the base also adds: overwritten at rc 0, no warning.
    """
    out = []
    clobbered = sorted(base_files & ignored)
    if clobbered:
        out.append(
            "gitignored paths the incoming base also adds -- `git add -A` cannot park them and the "
            "rebase overwrites them silently. Move them aside yourself first:\n"
            + "\n".join(f"  - {f}" for f in clobbered)
        )
    shadowed = sorted(base_files & hidden)
    if shadowed:
        out.append(
            "paths present on disk but flagged skip-worktree/assume-unchanged that the incoming base "
            "also touches -- `git add -A` skips them and the rebase refuses to detach HEAD. Clear the "
            "flag first (`git update-index --no-skip-worktree|--no-assume-unchanged`):\n"
            + "\n".join(f"  - {f}" for f in shadowed)
        )
    return out


def _unusable(what: str):
    """Say why the record cannot be used, and return the sentinel that says so.

    Every rejection in `read_record` goes through here, so every one of them names its
    cause. It has to be here and not in `restore_verdict`, whose CORRUPT branch runs from
    the sentinel alone and so cannot say which field or which leg.

    Each caller ends its own sentence. A shared "edited by hand or written by another
    tool" tail was tried and removed: on the unreadable leg it is wrong -- `cmd_park`
    documents a park killed mid-write, which leaves a truncated record nobody edited --
    and on the missing-key leg it dropped the alternative that leg had just offered.
    """
    print(f"the park record ({RECORD_NAME}, in this worktree's git dir) is unusable: {what}",
          file=sys.stderr)
    return CORRUPT


def read_record(repo: str):
    """The record dict, None when absent, or CORRUPT when present and unusable.

    Unusable is four shapes, not one: unreadable -- `OSError` or `UnicodeDecodeError` from
    the read, `json.JSONDecodeError` or `RecursionError` from the parse, all four caught as
    `(OSError, ValueError, RecursionError)` because the two middle ones are `ValueError`
    (see the `except` below for what that covers and what is deliberately left out); valid
    JSON that is not an object; missing one of `RECORD_KEYS`; and holding one of
    `RECORD_TYPES` at the wrong type or -- for `expected_drop` -- outside the range
    `_snapshot` can produce. The last shape exists because the record is a file on disk a
    human can edit. Provenance of the other three, and "unchanged" is about the accept/reject
    condition only: shapes 2 and 3 test byte-identically to what the base tested, shape 1's
    `except` tuple was widened here -- but all three changed behaviour, because all four now
    report which through `_unusable` instead of returning a bare sentinel. What is NOT here
    is `staged` and `parked_times`: read after the reset, guarded there, and the note on
    `RECORD_TYPES` says why that is the right place.

    Collapsing CORRUPT into None fails open: `park` would start a second park on top
    of the first, and `restore` would then leave the first as a real commit on the
    branch (measured 2026-08-14).
    """
    path = git_dir(repo) / RECORD_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    # `json.JSONDecodeError` alone was not enough, and neither is adding `UnicodeDecodeError`
    # by name: both are `ValueError`, and bytes that are not UTF-8 raise the second out of
    # `read_text()` -- past `cmd_restore`'s `except GitError`, so a traceback and rc 1 out of
    # the one function whose job is to turn a bad record into rc 2. That one is realistic and
    # `test_read_record_reports_bytes_that_are_not_utf_8` pins it. `RecursionError` is in the
    # tuple because it costs nothing, not because it is reachable by hand: the C scanner took
    # 100_000 nesting levels without complaint and raised only at 200_000 (CPython 3.14.6),
    # and that threshold is interpreter-dependent, which is why no test pins it. Observed
    # set, not a proven closed one -- `MemoryError` on a huge file is left uncaught on
    # purpose (all measured 2026-08-22).
    except (OSError, ValueError, RecursionError) as e:
        return _unusable(f"it could not be read ({type(e).__name__}: {e}). A park killed "
                         "mid-write leaves exactly this, and so does a hand edit")
    if not isinstance(data, dict):
        return _unusable(f"it holds a {type(data).__name__}, not a JSON object -- edited by "
                         "hand, or written by another tool")
    missing = sorted(RECORD_KEYS - data.keys())
    if missing:
        return _unusable("it is missing " + ", ".join(f"`{f}`" for f in missing)
                         + " -- written by an older version, edited by hand, or written by "
                           "another tool")
    for field, want in RECORD_TYPES.items():
        value = data[field]
        # `bool` is an `int` to isinstance, and `expected_drop: true` shrinks `expected` by
        # one -- one dropped commit waved through, no exception (measured 2026-08-22).
        if not isinstance(value, want) or (want is int and isinstance(value, bool)):
            return _unusable(f"`{field}` is {type(value).__name__}, not {want.__name__}")
        # No element check on `commits`: `len()` is the only thing that ever touches it
        # (three call sites, all `len(...)`), so its elements are never dereferenced and a
        # check on them would be error handling for a case that cannot happen. The field
        # whose elements DO reach a subprocess is `staged`, and it is guarded at that call
        # site instead -- see the note on RECORD_TYPES.
    # Below the type loop by necessity, not by habit: `len(data["commits"])` on a record the
    # loop has not vetted raises the very TypeError this function exists to convert into a
    # refusal. Deleting the loop and keeping this check is mutation 1 in the plan's table,
    # and the traceback escapes BOTH callers -- `cmd_park` and `cmd_restore` -- since both
    # reach it through `read_record` (measured 2026-08-22).
    # A range check rather than a type check, and the last TYPE- OR RANGE-driven route from
    # the record to a drop guard that is off -- not the last route, which is a claim this
    # cannot make: the guard compares the record's own counts, so a well-typed in-range
    # record still turns it off whenever those counts are wrong. `{"commits": [],
    # "expected_drop": 0}` and `{"commits": ["a","b","c"], "expected_drop": 3}` are both
    # accepted here and both make `expected` 0, so `kept < expected` is false for every
    # count (measured 2026-08-22). Closing that would mean cross-checking `commits` against
    # the repo, which this validation deliberately does not do -- it validates the record
    # against itself. `_snapshot` counts `expected_drop` as a subset of the same commit
    # list, so `0 <= expected_drop <= len(commits)` holds by construction. Above the top,
    # `expected` in `restore_verdict` goes negative and `kept < expected` is false for every
    # count -- rc 0 with the guard silently off, the same failure a `commits` of `{}` used
    # to produce. Below zero it inflates `expected` and refuses with a wrong number
    # instead (both measured 2026-08-22).
    if not 0 <= data["expected_drop"] <= len(data["commits"]):
        top = len(data["commits"])
        return _unusable(f"`expected_drop` is {data['expected_drop']}, outside 0..{top} "
                         "(the commits the record lists)")
    return data


def restore_verdict(record, busy: str | None, head_subject: str, kept: int | None,
                    allow_dropped: bool = False) -> tuple[int, str]:
    """Decide whether `git reset HEAD~1` is safe. Pure -- the whole point of the helper.

    `kept` is the number of non-park commits between the base and HEAD after the rebase;
    None when it could not be counted.
    """
    if busy:
        return (2, f"REFUSED: {busy} still in progress. Finish the rebase (`git rebase --continue`) first.")
    if record is CORRUPT:
        return (2, f"REFUSED: the park record ({RECORD_NAME}, in this worktree's git dir) is "
                   "unusable -- unreadable, not a JSON object, holding a field at the wrong "
                   "type or outside its range, or written by an older version that did not record "
                   "every field this one needs. The line above names which.\n"
                   "Do not reset blind. Read `git log -1`: if HEAD is "
                   f"the park commit ({PARK_SUBJECT!r}), `git reset HEAD~1` gives the work back and "
                   "the record can then be deleted; if it is not, find the park in `git reflog` "
                   "first. Deleting the record without doing that strands the park.")
    if record is None:
        return (2, "REFUSED: no park record for this worktree. Nothing was parked here, so the reset "
                   "would delete a real commit.")
    if head_subject != PARK_SUBJECT:
        return (2, "REFUSED: HEAD is not the park commit "
                   f"(subject: {head_subject!r}, expected {PARK_SUBJECT!r}).\n"
                   "Two causes, and they need opposite fixes. If that subject looks like the park's "
                   "with something added, a `prepare-commit-msg` hook rewrote it during a bare "
                   "`git rebase --continue` -- the work is right there at HEAD, so put the subject "
                   "back and re-run. Suppress the hook while doing it, a bare amend re-runs the very "
                   "hook that rewrote it, and keep the body's park id — `park` reports it in the "
                   "message that sent you here:\n"
                   f"    git -c core.hooksPath=/dev/null commit --amend -m '{PARK_SUBJECT}' "
                   f"-m '{PARK_TOKEN_KEY}: <id>'\n"
                   "Otherwise the park is gone, most likely to a `git rebase --skip`, which leaves a "
                   "clean tree that reads as complete: find it in `git reflog` before doing anything "
                   "else.")
    # `git cherry` marked some commits as already upstream by patch-id before the park, and the
    # rebase drops exactly those; subtracting them stops the check firing on every squash-merged
    # or cherry-picked branch -- the very shape that makes the guard return rc 1. It does not
    # predict every legitimate drop: a commit whose change the base already subsumes becomes
    # empty, and a merge commit is counted by `rev-list` but skipped by `git cherry` and flattened
    # by the rebase (both measured 2026-08-14). Those still refuse; the message says so and
    # --allow-dropped is the way through, because under-predicting errs toward stopping.
    expected = len(record["commits"]) - record["expected_drop"]
    if kept is None:
        return (2, "REFUSED: cannot count the branch's commits, so a dropped one would go unnoticed. "
                   "Compare `git log` with `git reflog` by hand.")
    if kept < expected and not allow_dropped:
        return (2, f"REFUSED: the branch should carry {expected} commit(s) after this rebase and carries "
                   f"{kept}. Check `git reflog` before anything else -- the cause worth catching is a "
                   "`git rebase --skip`, which drops the commit git was replaying and leaves a clean tree "
                   "that reads as complete. Benign causes exist too: a commit the base already subsumes "
                   "becomes empty, and a merge commit gets flattened. Once you have identified which, "
                   "re-run with --allow-dropped.")
    return (0, "")


def _printable(paths) -> str:
    """Join paths for stderr in a form that can always be encoded.

    The same hand-edited record that motivates the guards in cmd_restore can hold a path
    an unpaired surrogate makes unencodable, so printing it raw raises UnicodeEncodeError
    -- past the reset, where nothing may fail, and from the very line whose job is to
    report the problem. A NUL is encodable but invisible, which is worse than an escape
    for something the operator has to act on. Measured 2026-08-22.
    """
    return ", ".join(p.encode("utf-8", "backslashreplace").decode("utf-8").replace("\x00", "\\x00")
                     for p in paths)


def _restage(repo: str, paths: list[str]) -> tuple[list[str], list[str]]:
    """Stage `paths` one at a time; return (done, failed).

    Never one `git add -- <many>`: a single rc cannot describe what happened to the list.
    A path the rebase deleted takes the whole command down (`rc 128`, nothing staged);
    a path the new base's .gitignore now matches gives `rc 1` and stages every OTHER path
    (both measured 2026-08-15). Per-path is the only form whose result is knowable.

    The net is for the values `subprocess` refuses to hand to execve at all, which never
    reach an exit code: a NUL inside the string raises ValueError("embedded null byte") and
    an unpaired surrogate raises UnicodeEncodeError. Both are producible by `json.loads`,
    so a hand-edited park record reaches them, and cmd_restore calls this past the point
    where nothing may fail. A caller-side `isinstance(p, str)` cannot close this -- what
    execve accepts is knowable only here -- and the net does not loosen the trusted path,
    since the `cmd_park` side gets its list from `_snapshot`, whose `_lines()` split of git
    output can produce neither value. Measured 2026-08-22.
    """
    done, failed = [], []
    for path in paths:
        try:
            ok = _git_rc(repo, ["add", "--", path])[0] == 0
        except (ValueError, UnicodeEncodeError):
            ok = False
        (done if ok else failed).append(path)
    return (done, failed)


def _snapshot(repo: str, base: str) -> tuple[list[str], list[str], int]:
    """(branch commits above the base, staged paths, commits the rebase will legitimately drop).

    All three taken before `add -A`, so `commits` and the `git cherry` reading cover the
    branch alone and not the park commit.
    """
    commits = _lines(_git(repo, ["rev-list", f"origin/{base}..HEAD"]))
    staged = _lines(_git(repo, [*QUOTE_OFF, "diff", "--name-only", "--cached"]))
    expected_drop = sum(1 for line in _lines(_git(repo, ["cherry", f"origin/{base}", "HEAD"]))
                        if line.startswith("-"))
    return (commits, staged, expected_drop)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _capture_times(repo: str) -> list[dict]:
    """(path, mtime_ns, sha256) for every staged path that is a plain file on disk.

    Read after `git add -A`, which does not touch the working tree -- so these are still the
    times the review was measured against. The rebase two steps later rewrites every file in
    the park commit whether or not its content ends up different; `restore` uses this to hand
    the time back where it did not (see _rewind_times).

    Symlinks are skipped: st_mtime_ns of the target says nothing about the link, and utime
    would follow it. A path that cannot be stat'd or read is left out rather than raised on --
    it forfeits only its own rewind, and no part of the park is worth failing over that.

    Third skip, from the listing rather than the loop: QUOTE_OFF lifts C-quoting for non-ASCII
    bytes only, so a path holding a tab, a carriage return, a double quote or a backslash still
    arrives quoted (measured 2026-08-16, git 2.55.0 -- skills/commit/SKILL.md step 1 records
    the same six probes, and moved that caller to `-z` for it). The quoted literal matches
    nothing on disk, so `is_file()` fails and the path is simply never rewound. Same
    convention, and the same limitation, as _snapshot and _restage above; the cost here is one
    forfeited rewind, not a wrong one.
    """
    try:
        staged = _lines(_git(repo, [*QUOTE_OFF, "diff", "--name-only", "--cached", "HEAD"]))
    except GitError:
        return []
    out = []
    for rel in staged:
        p = Path(repo) / rel
        try:
            if p.is_symlink() or not p.is_file():
                continue
            out.append({"path": rel, "mtime_ns": p.stat().st_mtime_ns, "sha256": _sha256(p)})
        except OSError:
            continue
    return out


def cmd_park(args) -> int:
    try:
        repo = toplevel(args.repo)
    except GitError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        busy = in_progress(repo)
        if busy:
            print(f"REFUSED: {busy} already in progress in this worktree.", file=sys.stderr)
            return 2
        try:
            _git(repo, ["symbolic-ref", "--quiet", "HEAD"])
        except GitError:
            print("REFUSED: detached HEAD -- the park commit would not land on any branch.", file=sys.stderr)
            return 2
        if read_record(repo) is not None:
            print(f"REFUSED: a park record already exists for this worktree ({RECORD_NAME} in its git "
                  "dir); run `restore` first.", file=sys.stderr)
            return 2
        if _git(repo, ["log", "-1", "--format=%s"]) == PARK_SUBJECT:
            # A park killed between the commit and the record write leaves exactly this: a clean
            # tree with the work inside the park commit and no record. Parking again would rebase
            # it onto the fresh base and let step 5 push it.
            print(f"REFUSED: HEAD is already a {PARK_SUBJECT!r} commit with no record -- an earlier "
                  f"park did not finish. Undo it first: git -C {repo} reset HEAD~1", file=sys.stderr)
            return 2

        base = args.base or default_base(repo)
        _git(repo, ["fetch", "origin", base, "--quiet"])
        blocked = refusals(_base_files(repo, base), _ignored_files(repo), _hidden_from_add(repo))
        if blocked:
            print("REFUSED before touching anything:\n" + "\n".join(blocked), file=sys.stderr)
            return 2

        pre_head = _git(repo, ["rev-parse", "HEAD"])  # breadcrumb for manual reflog recovery only
        commits, staged, expected_drop = _snapshot(repo, base)
        dirty = _git(repo, ["status", "--porcelain"])
    except GitError as e:
        print(str(e), file=sys.stderr)
        return 2

    parked = bool(dirty)
    if parked:
        try:
            _git(repo, ["add", "-A"])
            parked_times = _capture_times(repo)
            # `--no-verify` covers pre-commit and commit-msg and NOT prepare-commit-msg, which
            # is the one that rewrites the subject (a JIRA-prefix hook, husky, pre-commit). A
            # rewritten subject defeats every guard downstream, all of which key on it. Disable
            # hooks outright instead, and override signing, which --no-verify never touched.
            # The commit is never pushed, so neither is a policy decision about the branch.
            token = uuid.uuid4().hex[:12]
            # Two `-m` are joined by a blank line, which is the subject/body split we want and
            # is escape-free: `-m` does NOT expand `\n`, so a single-argument form would bury
            # the token in the subject.
            _git(repo, [*NO_HOOKS, "-c", "commit.gpgsign=false", "commit", "--no-verify",
                        "-m", PARK_SUBJECT, "-m", f"{PARK_TOKEN_KEY}: {token}"])
            # Belt and braces: whatever else rewrites a message in this repo, verify BOTH the
            # subject and the token rather than trusting the hook suppression above to be
            # complete. The token especially -- a later absence of it is read as "the rebase
            # dropped the park", so it has to be established that it was there to begin with.
            # Undo and refuse otherwise: reporting rc 4 "nothing to restore" over a park nobody
            # can restore is the worst outcome this module has.
            if (_git(repo, ["log", "-1", "--format=%s"]) != PARK_SUBJECT
                    or f"{PARK_TOKEN_KEY}: {token}" not in _git(repo, ["log", "-1", "--format=%B"])):
                reset_rc = _git_rc(repo, ["reset", "HEAD~1"])[0]
                _, failed = _restage(repo, staged)
                if reset_rc != 0:
                    print("STOP: something in this repo rewrote the park commit's message, which every "
                          "check downstream keys on, AND undoing it failed. That commit is still HEAD "
                          "with all the pending work in it -- do NOT push it. Undo it by hand: "
                          f"git -C {repo} reset HEAD~1", file=sys.stderr)
                    return 5
                if failed:
                    print("STOP: something in this repo rewrote the park commit's message, which every "
                          "check downstream keys on. The commit was undone, but these paths were staged "
                          f"before and are not now: {', '.join(failed)}. Stage them by hand before "
                          "anything else reads the diff.", file=sys.stderr)
                    return 5
                print("REFUSED: something in this repo rewrote the park commit's message, which every "
                      "check downstream keys on. The commit was undone and the repo is unchanged. Park "
                      "by hand, or find what rewrites it.", file=sys.stderr)
                return 2
        except GitError as e:
            # `add -A` staged everything; put the index back the way it was found. rc 2
            # promises the repo is unchanged, so the rollback has to be checked, not assumed.
            _git_rc(repo, ["reset", "--quiet"])
            _, failed = _restage(repo, staged)
            if failed:
                print(f"STOP: park commit failed ({e}) AND the index could not be put back -- these "
                      f"paths were staged before and are not now: {', '.join(failed)}", file=sys.stderr)
                return 5
            print(f"park commit failed, index restored, nothing else changed: {e}", file=sys.stderr)
            return 2
        try:
            (git_dir(repo) / RECORD_NAME).write_text(json.dumps(
                {"pre_head": pre_head, "base": base, "commits": commits,
                 "staged": staged, "expected_drop": expected_drop, "token": token,
                 "parked_times": parked_times}
            ))
        except (GitError, OSError) as e:
            print(f"STOP: parked but could not write the record ({e}). The repo IS changed: the park "
                  f"commit is HEAD and must not be pushed -- undo it with: git -C {repo} reset HEAD~1",
                  file=sys.stderr)
            return 5

    rc, out = _git_rc(repo, [*NO_HOOKS, "rebase", f"origin/{base}"])
    print(out)
    if rc != 0:
        # A rebase that refuses BEFORE detaching (an untracked or skip-worktree collision)
        # leaves nothing in progress, so telling the operator to `--continue` is wrong.
        try:
            stopped_midway = in_progress(repo) is not None
        except GitError as e:
            print(f"STOP: the rebase failed and its state could not be read ({e}). Inspect the "
                  f"worktree by hand{'; the park commit is HEAD and must not be pushed' if parked else ''}.",
                  file=sys.stderr)
            return 5
        if not parked:
            if not stopped_midway:
                print("\nnothing was parked and the rebase refused before starting -- the repo is "
                      "unchanged. Fix the cause above; no `restore` is owed.", file=sys.stderr)
                return 2
            print("\nnothing was parked, but the rebase did not complete and the worktree IS changed. "
                  "Resolve and `git rebase --continue` (never `--skip`); no `restore` is owed.",
                  file=sys.stderr)
            return 5
        if not stopped_midway:
            print("\nparked, but the rebase refused before starting -- there is no rebase to continue. "
                  "Fix the cause above, re-run the rebase yourself, then `park_and_rebase.py restore`; "
                  f"the park commit is HEAD meanwhile and must not be pushed.", file=sys.stderr)
            return 5
        print("\nrebase stopped -- fix whatever it reported above (a conflict is the usual cause, but "
              "a rebase can also stop on something like a signing failure, where there is nothing to "
              "merge). Then continue it with the hook suppression, NOT a bare `--continue`:\n"
              f"    git -C {repo} -c core.hooksPath=/dev/null rebase --continue\n"
              "That is a fresh git process, so it re-runs `prepare-commit-msg` and rewrites the park "
              "subject every guard here keys on. NEVER `git rebase --skip`: it drops the commit being "
              "replayed and leaves a tree that reads as complete. Then run `park_and_rebase.py restore`.",
              file=sys.stderr)
        return 3
    if not parked:
        print("nothing to park -- tree was already clear; no restore is owed.", file=sys.stderr)
        return 4
    subj_rc, subj = _git_rc(repo, ["log", "-1", "--format=%s"])
    if subj_rc == 0 and subj != PARK_SUBJECT:
        # Two states share this signal, and only one is benign. The body token settles it:
        # a subject rewrite leaves it intact, a rebase that dropped the commit takes it with
        # the commit. A commit count cannot -- it leans on the same `git cherry` prediction
        # this module documents as under-predicting drops, and here that under-prediction
        # errs toward proceeding: one unpredicted drop makes the counts match and the park
        # reads as gone while it is still HEAD, holding work headed for step 5's push.
        body_rc, body = _git_rc(repo, ["log", "-1", "--format=%B"])
        if body_rc != 0:
            print(f"STOP: could not read HEAD's message ({body}), so a park surviving under a "
                  "rewritten subject cannot be told from one the rebase dropped. Check "
                  f"`git -C {repo} log -1` before doing anything else.", file=sys.stderr)
            return 5
        if f"{PARK_TOKEN_KEY}: {token}" in body:
            print("STOP: the park commit is still HEAD but its subject was rewritten "
                  f"(now {subj!r}). The work is in that commit -- do NOT push it. Put the subject "
                  "back with the hook suppression; a bare amend re-runs the hook that did this:\n"
                  f"    git -C {repo} -c core.hooksPath=/dev/null commit --amend "
                  f"-m '{PARK_SUBJECT}' -m '{PARK_TOKEN_KEY}: {token}'\n"
                  "(two -m, joined by a blank line: -m does not expand escapes, so a single "
                  "argument would bury the token in the subject.) Then run "
                  "`park_and_rebase.py restore`.", file=sys.stderr)
            return 5
        # The base already carried the park's exact diff, so the rebase dropped it by patch-id.
        # The content is on disk either way; `restore` would read this as a dropped park and
        # send the operator to the reflog for nothing.
        try:
            (git_dir(repo) / RECORD_NAME).unlink(missing_ok=True)
        except (GitError, OSError) as e:
            print(f"STOP: the park was dropped by patch-id but its record could not be removed ({e}) -- "
                  f"delete {RECORD_NAME} from this worktree's git dir before the next park.",
                  file=sys.stderr)
            return 5
        print("the base already contained this work -- the rebase dropped the park by patch-id. "
              "Nothing to restore.", file=sys.stderr)
        return 4
    print("parked and rebased -- run `park_and_rebase.py restore`.", file=sys.stderr)
    return 0


def _rewind_times(repo: str, parked_times: list) -> tuple[int, int, int]:
    """Give back the pre-park mtime wherever the content is byte-identical.

    Returns (restored, changed, unreadable). The rebase rewrites every file in the park
    commit whether or not its content ends up different, and lib/review_marker.py decides
    coverage by comparing those times against the review record -- so without this an
    untouched file reads as edited and costs a review loop that only reconfirms (observed
    2026-08-18).

    The invariant that makes this safe by construction rather than by policy: it can only
    restore a value the path already held BEFORE the park, never an older one. A file edited
    after the review and before the park was already newer than the record then, and gets
    exactly that time back, so the pre-flight keeps flagging it. The gate is not loosened;
    a broken proxy is repaired.

    Every failure is counted, never raised: this runs past the point in cmd_restore where the
    work is already back in the working tree and nothing may return non-zero. The net is PER
    ENTRY because the record is a file on disk that a human may have edited by hand, so each
    field it reads can hold any JSON value. Sweeping every JSON-representable shape through this
    loop body at each of the four -- entry, path, mtime_ns, sha256 -- leaves exactly these
    escaping, which is what the net lists: TypeError from an entry that is not a dict and from a
    non-str path or a non-int mtime_ns; KeyError from a dict missing path or sha256; OSError
    from a path that stats but cannot then be opened or utime'd (PermissionError after chmod 0);
    OverflowError from an mtime_ns whose seconds part does not fit time_t -- os.utime divmods ns
    by 10**9, so the boundary is asymmetric like int64 itself: it raises at mtime_ns >=
    2**63 * 10**9 on the positive side but only below -(2**63 * 10**9) on the negative side,
    because that value's floor-divmod lands exactly on int64's min. 2**63 alone does NOT
    raise either way, which is why the value has to be ~28 digits and none had shown up
    before. ValueError
    is absent because it cannot arrive, not by oversight: its only source is an embedded NUL in
    the path, and is_file() delegates to os.path.isfile, which catches (OSError, ValueError) and
    returns False, so that path is counted unreadable above before _sha256 or os.utime see it.
    Python 3.14.6, macOS; all measured 2026-08-23. A `parked_times` that is not a list at all
    fails the `for` itself, outside this net -- cmd_restore type-checks it at the call site
    instead.

    The identity test is SHA-256 over the bytes, so it does not cover the file mode. Read from
    the code, not observed in the wild: if the incoming base changes only the exec bit on a
    path the park also modifies, the hash still matches, the mtime is rewound, and check() --
    which reads mtime alone -- never sees the mode change.
    """
    restored = changed = unreadable = 0
    for entry in parked_times:
        try:
            p = Path(repo) / entry["path"]
            if p.is_symlink() or not p.is_file():
                unreadable += 1
                continue
            if _sha256(p) != entry["sha256"]:
                changed += 1
                continue
            os.utime(p, ns=(p.stat().st_atime_ns, entry["mtime_ns"]))
            restored += 1
        except (OSError, KeyError, TypeError, OverflowError):
            unreadable += 1
    return (restored, changed, unreadable)


def cmd_restore(args) -> int:
    try:
        repo = toplevel(args.repo)
    except GitError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        # `busy` first, and the record read only when not busy: `read_record` prints why a
        # record is unusable, and with a rebase in progress that line would sit above a
        # verdict that talks only about the rebase -- an orphan the operator reads as a
        # second, unexplained problem. The verdict checks `busy` before anything else, so
        # the None this passes on that path is never inspected.
        busy = in_progress(repo)
        record = None if busy else read_record(repo)
        head_subject = "" if busy else _git(repo, ["log", "-1", "--format=%s"])
        kept = None
        if not busy and isinstance(record, dict):
            try:
                kept = int(_git(repo, ["rev-list", "--count", f"origin/{record['base']}..HEAD~1"]))
            except (GitError, ValueError, KeyError):
                kept = None
    except GitError as e:
        print(str(e), file=sys.stderr)
        return 2

    rc, message = restore_verdict(record, busy, head_subject, kept, args.allow_dropped)
    if rc != 0:
        print(message, file=sys.stderr)
        return rc

    try:
        _git(repo, ["reset", "HEAD~1"])
    except GitError as e:
        print(str(e), file=sys.stderr)
        return 2
    # Past this point the work IS back in the tree; nothing below may return non-zero.
    # A hand-edited record can put any JSON value here -- `staged` is in RECORD_KEYS but not
    # in RECORD_TYPES, so `read_record` checks that it is present and never what it holds --
    # and this runs past the reset above, where nothing may fail. Two levels have to be
    # checked, not one. (`read_record` does type other fields; the reason it leaves this one
    # alone is the note on RECORD_TYPES, and it is why this guard is not redundant.)
    #
    # The container: int, float and bool are not iterable and come out of `_restage`'s
    # `for path in paths` as an uncaught TypeError, while a str iterates into single
    # characters that each become a `git add` pathspec, so what gets staged depends on the
    # repo rather than the string -- `.` and pathspec-magic characters like `*` match the
    # WHOLE worktree, a one-character filename matches itself, the rest just fail. The worst
    # case there is silent and total and needs nothing rarer than a filename with a dot.
    #
    # The elements: a well-formed list holding a non-str reaches `git add -- <value>` and
    # raises `TypeError: expected str, bytes or os.PathLike object`. That set is LARGER than
    # the container one -- int, float, bool, None, list and dict all raise, where None and
    # dict were harmless as containers -- and a mixed list crashes partway, after staging the
    # good paths ahead of it. `_restage` has no per-entry net (unlike `_rewind_times`), and
    # adding one there would loosen a helper that `cmd_park` calls with a list `_snapshot`
    # guarantees, so the check belongs here at the untrusted call site. All measured
    # 2026-08-22.
    #
    # isinstance, not try/except: the str container case raises nothing to catch, and
    # catching TypeError would also hide a real one raised inside _restage.
    staged = record.get("staged")
    staged_ok = isinstance(staged, list) and all(isinstance(p, str) for p in staged)
    if staged_ok and staged:
        # `add -A` collapsed the index/worktree split and the mixed reset cannot rebuild it.
        # Re-staging keeps these paths inside the set step 4 stages from -- a staged-but-never
        # -committed path would otherwise come back untracked and drop out of it.
        # One path at a time: over a pathspec list a single rc says nothing usable -- a path
        # the rebase deleted stages none of them, an ignored one stages all the others.
        done, failed = _restage(repo, staged)
        if done:
            print(f"re-staged (content merged, staged/worktree split not preserved): "
                  f"{_printable(done)}", file=sys.stderr)
        if failed:
            print(f"NOT re-staged -- ACT ON THIS: {_printable(failed)}. A path the rebase deleted is "
                  "gone on purpose; a path the new base's .gitignore now matches is still on disk and "
                  "is invisible to both `git status` and step 4's file list, so it drops out of the "
                  "commit unless you `git add -f` it.", file=sys.stderr)
    elif not staged_ok:
        # Skipping is the safe half; skipping quietly is not: the module's contract is that a
        # path it could not put back in the index gets named on stderr. Not every path given up
        # on here leaves step 4's set -- an already-tracked one returns as an unstaged
        # modification and step 4 still finds it; only a staged-but-never-committed path really
        # falls out. The message below draws that line, so keep the two in step.
        # Name the usable entries as well as the bad ones. This branch is the only place the
        # module holds path strings it has decided not to re-stage, and the record is unlinked
        # a few lines below, so not printing them destroys the only copy -- worse than the
        # crash this guard replaced, which at least aborted above that unlink. Refusing the
        # list is still right; discarding the evidence is not part of refusing it.
        if isinstance(staged, list):
            bad = "a list holding " + ", ".join(sorted(
                {type(p).__name__ for p in staged if not isinstance(p, str)}))
            usable = [p for p in staged if isinstance(p, str)]
        else:
            bad = type(staged).__name__
            usable = []
        print(f"the park record's `staged` field is {bad}, not a list of path strings -- the "
              "index was NOT rebuilt (most likely a hand-edited record). No content is lost: a "
              "path that was already tracked comes back as an unstaged modification, which step "
              "4 still picks up. What to check is any path that was staged but never committed "
              "-- that one is untracked now and drops out of step 4's file list unless you "
              "`git add` it.", file=sys.stderr)
        if usable:
            # Deliberately avoids the substring "re-staged": the tests assert its absence to
            # mean nothing reached the index, and this line reports the opposite of that.
            print(f"the record did name these paths, none of them put back in the index -- this "
                  f"is the only copy, the record is deleted next: {_printable(usable)}",
                  file=sys.stderr)
    # isinstance, not truthiness: a hand-edited record holding a non-list here escapes both
    # `for entry in parked_times` and the len() below as an uncaught TypeError, past the reset
    # where nothing may fail (measured: int, bool and float all raise, dict and str do not).
    parked_times = record.get("parked_times")
    if isinstance(parked_times, list) and parked_times:
        restored, changed, unreadable = _rewind_times(repo, parked_times)
        print(f"mtime restored on {restored} of {len(parked_times)} parked paths (content "
              f"identical); {changed} changed since the park; {unreadable} skipped (gone, no "
              "longer a plain file, unreadable, unusable as a record entry, or the mtime "
              "could not be set)",
              file=sys.stderr)
    try:
        (git_dir(repo) / RECORD_NAME).unlink(missing_ok=True)
    except (GitError, OSError) as e:
        print(f"warning: restored, but the park record could not be removed ({e}) -- delete "
              f"{RECORD_NAME} from this worktree's git dir before the next park.", file=sys.stderr)
    print("restored to the working tree. Re-run the guarded block at the top of commit step 0.5 "
          "(not a bare check_base_freshness.py -- that drops the [ -f ] test), then continue into step 1.",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("park")
    p.add_argument("--repo", default=".")
    p.add_argument("--base")
    p.set_defaults(func=cmd_park)
    r = sub.add_parser("restore")
    r.add_argument("--repo", default=".")
    r.add_argument("--allow-dropped", action="store_true",
                   help="proceed when the branch lost a commit and the reflog says the loss was "
                        "legitimate (already upstream, became empty, or a flattened merge)")
    r.set_defaults(func=cmd_restore)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
