#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Tests for lib/changed_files.py, the read-only candidate listing shared by
hooks/review_loop_warn.py and skills/commit/SKILL.md step 1."""
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

CLI = Path(__file__).parent.parent / "changed_files.py"
_spec = importlib.util.spec_from_file_location("changed_files", CLI)
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)


def _mkrepo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    (r / "a.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(r), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "base"], check=True)
    (r / "a.txt").write_text("changed\n")   # left UNSTAGED -- PreToolUse sees an empty index
    return r


def _cli(repo):
    return subprocess.run([sys.executable, str(CLI), "--repo", str(repo)],
                          capture_output=True, check=True).stdout


def test_cli_leaves_git_index_untouched(tmp_path):
    """Porcelain `git diff` refreshes a stat-dirty entry and writes .git/index back through
    index.lock, even under GIT_OPTIONAL_LOCKS=0 -- the commit pre-flight must only read."""
    repo = _mkrepo(tmp_path)
    (repo / "b.txt").write_text("b\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "b"], check=True)
    later = time.time() + 100
    os.utime(repo / "b.txt", (later, later))   # stat-dirty, content unchanged
    idx = repo / ".git" / "index"
    before = (idx.stat().st_ino, idx.stat().st_mtime_ns)
    out = _cli(repo)
    assert out == b"a.txt\0"                     # the CLI really listed something
    assert (idx.stat().st_ino, idx.stat().st_mtime_ns) == before


def test_cli_lists_tracked_and_untracked_nul_terminated(tmp_path):
    repo = _mkrepo(tmp_path)
    (repo / "we\rird").write_text("u\n")         # untracked, a name git C-quotes without -z
    (repo / "ignored").write_text("i\n")
    (repo / ".gitignore").write_text("ignored\n")
    assert _cli(repo) == b".gitignore\0a.txt\0we\rird\0"


def test_cli_fails_loudly_outside_a_repo(tmp_path):
    """An empty listing at rc 0 would make review_marker check() certify nothing as covered."""
    r = subprocess.run([sys.executable, str(CLI), "--repo", str(tmp_path)], capture_output=True,
                       env={**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path)})   # basetemp may sit inside a repo
    assert r.returncode == 2
    assert r.stdout == b""


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS rejects non-UTF-8 names (Errno 92)")
def test_candidates_round_trips_invalid_utf8(tmp_path):
    repo = _mkrepo(tmp_path)
    raw = b"bad\xffname"
    (repo / os.fsdecode(raw)).write_bytes(b"x\n")
    got = cf.candidates(str(repo))
    assert raw.decode("utf-8", "surrogateescape") in got
    assert _cli(repo).split(b"\0")[:-1] == [p.encode("utf-8", "surrogateescape") for p in got]


def test_changed_tracked_matches_diff_head_domain(tmp_path):
    """Same domain the porcelain `diff --name-only HEAD` had: tracked files differing from
    HEAD, staged or not; a rename yields only its new path (the source path is a separate
    -z token that must be skipped -- here it starts with "2 " so a missed skip raises IndexError),
    or only its source when the new path is gone from disk; a path added then deleted (in
    neither HEAD nor the worktree), a touched-only file and an untracked file are not listed.
    A path listed that exists in neither makes review_marker check() call the list unusable."""
    repo = _mkrepo(tmp_path)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "config", "status.renames", "true"], check=True)   # pin against user gitconfig
    seeded = ["staged", "del", "rmd", "2 src", "rsrc", "touch", "we\rird"]
    for name in seeded:
        (repo / name).write_text(name + "\n")
    subprocess.run([*git, "add", "--", *seeded], check=True)   # not -A: a.txt stays unstaged-modified
    subprocess.run([*git, "commit", "-qm", "seed"], check=True)
    (repo / "staged").write_text("y\n")
    subprocess.run([*git, "add", "staged"], check=True)
    (repo / "del").unlink()
    subprocess.run([*git, "rm", "-q", "rmd"], check=True)
    subprocess.run([*git, "mv", "2 src", "dst"], check=True)
    subprocess.run([*git, "mv", "rsrc", "rdst"], check=True)
    (repo / "rdst").unlink()                     # RD: renamed, then dst removed from disk
    (repo / "added").write_text("n\n")
    subprocess.run([*git, "add", "added"], check=True)
    (repo / "added").unlink()                    # AD: in neither HEAD nor worktree
    (repo / "ita").write_text("i\n")
    subprocess.run([*git, "add", "-N", "ita"], check=True)
    (repo / "ita").unlink()                      # intent-to-add, then removed: non-zero mH, not in HEAD
    (repo / "new").write_text("n\n")
    subprocess.run([*git, "add", "new"], check=True)   # A.: not in HEAD but on disk -- kept
    (repo / "we\rird").write_text("z\n")
    later = time.time() + 100
    os.utime(repo / "touch", (later, later))
    (repo / "untracked").write_text("u\n")
    got = sorted(cf.changed_tracked(str(repo)))
    assert got == sorted([b"a.txt", b"staged", b"del", b"rmd", b"dst", b"rsrc", b"new", b"we\rird"])


def _modify_delete_conflict(tmp_path, delete_on_ours):
    """Repo left mid-merge with a modify/delete conflict on `f`: DU when our side deleted it,
    UD when theirs did."""
    repo = _mkrepo(tmp_path)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "commit", "-qam", "a"], check=True)
    (repo / "f").write_text("f\n")
    subprocess.run([*git, "add", "f"], check=True)
    subprocess.run([*git, "commit", "-qm", "f"], check=True)
    main = subprocess.run([*git, "symbolic-ref", "--short", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()

    def delete():
        subprocess.run([*git, "rm", "-q", "f"], check=True)
        subprocess.run([*git, "commit", "-qm", "del f"], check=True)

    def modify():
        (repo / "f").write_text("changed f\n")
        subprocess.run([*git, "commit", "-qam", "modify f"], check=True)

    subprocess.run([*git, "checkout", "-qb", "side"], check=True)
    (modify if delete_on_ours else delete)()
    subprocess.run([*git, "checkout", "-q", main], check=True)
    (delete if delete_on_ours else modify)()
    subprocess.run([*git, "merge", "-q", "side"], capture_output=True)
    return repo


def _status_v2(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v2", "-z"],
                          capture_output=True).stdout


def test_changed_tracked_drops_unmerged_path_absent_from_head_and_disk(tmp_path):
    """Deleted on our side of a merge (so HEAD lacks it), modified on theirs, then removed
    from disk while resolving: the path is in neither HEAD nor the worktree. Listing it
    makes review_marker check() call the list unusable."""
    repo = _modify_delete_conflict(tmp_path, delete_on_ours=True)
    assert cf.changed_tracked(str(repo)) == [b"f"]   # still on disk: kept
    (repo / "f").unlink()
    assert b"u DU " in _status_v2(repo)   # guard: the scenario really is an unmerged DU record
    assert cf.changed_tracked(str(repo)) == []


def test_changed_tracked_keeps_unmerged_path_in_head_removed_from_disk(tmp_path):
    """Ours still has the path (UD: theirs deleted it), so removing it from disk is a real
    deletion vs HEAD -- `diff HEAD` listed it, and dropping it would be false coverage."""
    repo = _modify_delete_conflict(tmp_path, delete_on_ours=False)
    (repo / "f").unlink()
    assert b"u UD " in _status_v2(repo)   # guard: the scenario really is an unmerged UD record
    assert cf.changed_tracked(str(repo)) == [b"f"]


def test_changed_tracked_keeps_head_path_after_stash_apply_conflict(tmp_path):
    """After `git stash apply` a `u` record's stage 2 is the index, not HEAD: a staged
    `git rm x`, a DU conflict and `rm x` report ours mode 000000 while HEAD still has x.
    That is a real deletion -- dropping it would be false coverage."""
    repo = _mkrepo(tmp_path)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "commit", "-qam", "a"], check=True)
    (repo / "x").write_text("x\n")
    subprocess.run([*git, "add", "x"], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    (repo / "x").write_text("x\nmod\n")
    subprocess.run([*git, "stash", "-q"], check=True)
    subprocess.run([*git, "rm", "-q", "x"], check=True)
    subprocess.run([*git, "stash", "apply"], capture_output=True)
    (repo / "x").unlink(missing_ok=True)
    assert b"u DU " in _status_v2(repo)   # guard: ours mode 000000 although HEAD has x
    assert cf.changed_tracked(str(repo)) == [b"x"]
