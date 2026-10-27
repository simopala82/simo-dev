"""Tests for lib/stack_record.py."""
import shlex
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "stack_record.py"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo, name):
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"add {name}")


def _run(repo, *args):
    r = subprocess.run([sys.executable, str(LIB), *args, "--repo", str(repo)], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _stack(tmp_path):
    """origin main <- parent (1 commit, pushed) <- child (1 commit, checked out)."""
    up, work = tmp_path / "up.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _commit(work, "base.txt")
    _git(work, "remote", "add", "origin", str(up))
    _git(work, "push", "-q", "origin", "main")
    _git(work, "remote", "set-head", "origin", "main")
    _git(work, "switch", "-qc", "parent")
    _commit(work, "parent.txt")
    _git(work, "push", "-q", "origin", "parent")
    _git(work, "switch", "-qc", "child")
    _commit(work, "child.txt")
    return work


def _squash_merge_parent(work):
    _git(work, "switch", "-q", "main")
    _git(work, "merge", "-q", "--squash", "origin/parent")
    _git(work, "commit", "-qm", "squash parent")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "push", "-q", "origin", "--delete", "parent")
    _git(work, "fetch", "-q", "--prune", "origin")
    _git(work, "switch", "-q", "child")


def test_record_stores_parent_and_fork_point(tmp_path):
    work = _stack(tmp_path)
    assert _run(work, "record", "--target", "parent")[0] == 0
    assert _git(work, "config", "branch.child.simoDevStackParent") == "parent"
    assert _git(work, "config", "branch.child.simoDevStackBase") == _git(work, "rev-parse", "origin/parent")


def test_record_default_target_unsets(tmp_path):
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    _git(work, "push", "-q", "origin", "--delete", "parent")
    _git(work, "fetch", "-q", "--prune", "origin")
    assert _run(work, "record", "--target", "main")[0] == 0
    r = subprocess.run(["git", "-C", str(work), "config", "branch.child.simoDevStackParent"], capture_output=True)
    assert r.returncode == 1


def test_default_target_keeps_record_while_parent_on_remote(tmp_path):
    """Parent force-pushed after a rebase: detection falls back to the default branch, so commit
    step 7 records `main`. The real fork must survive, or a later squash merge goes unnoticed."""
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    fork = _git(work, "rev-parse", "origin/parent")
    assert _run(work, "record", "--target", "main")[0] == 0
    assert _git(work, "config", "branch.child.simoDevStackBase") == fork
    _squash_merge_parent(work)
    rc, out, _ = _run(work, "check", "--base", "main")
    assert rc == 1 and out.splitlines()[0].endswith(fork)


def test_check_still_stacked_is_quiet(tmp_path):
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    assert _run(work, "check", "--base", "parent")[:2] == (0, "")


def test_check_without_record_is_quiet(tmp_path):
    assert _run(_stack(tmp_path), "check", "--base", "main")[:2] == (0, "")


def test_check_squash_merged_parent_suggests_rebase_onto(tmp_path):
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    fork = _git(work, "rev-parse", "origin/parent")
    _squash_merge_parent(work)
    rc, out, _ = _run(work, "check", "--base", "main")
    assert rc == 1
    lines = out.strip().splitlines()
    assert lines[0] == f"rebase: git -C {shlex.quote(str(work))} rebase --onto origin/main {fork}"
    assert lines[1:] == [f"drops: {_git(work, 'log', '--oneline', '-1', fork)}"]
    assert lines[1].endswith("add parent.txt")


def test_check_parent_merged_as_is_is_quiet(tmp_path):
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    _git(work, "push", "-q", "origin", "origin/parent:main")
    _git(work, "push", "-q", "origin", "--delete", "parent")
    _git(work, "fetch", "-q", "--prune", "origin")
    assert _run(work, "check", "--base", "main")[:2] == (0, "")


def test_check_after_rebase_onto_is_quiet(tmp_path):
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    fork = _git(work, "rev-parse", "origin/parent")
    _squash_merge_parent(work)
    _git(work, "rebase", "-q", "--onto", "origin/main", fork)
    assert _run(work, "check", "--base", "main")[:2] == (0, "")


def test_check_retargeted_while_parent_still_on_remote_is_quiet(tmp_path):
    """MR retargeted by hand (or a snapshot ref recorded as parent): no merge happened, never suggest."""
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    assert _run(work, "check", "--base", "main")[:2] == (0, "")


def test_refresh_keeps_fork_after_parent_force_push(tmp_path):
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    fork = _git(work, "rev-parse", "origin/parent")
    _git(work, "switch", "-q", "main")
    _commit(work, "main2.txt")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "switch", "-q", "-C", "parent", "origin/parent")
    _git(work, "rebase", "-q", "main")
    _git(work, "push", "-q", "-f", "origin", "parent")
    _git(work, "switch", "-q", "child")
    assert _run(work, "record", "--target", "parent")[0] == 0
    assert _git(work, "config", "branch.child.simoDevStackBase") == fork


def test_record_without_merge_base_is_error(tmp_path):
    work = _stack(tmp_path)
    _git(work, "switch", "-q", "--orphan", "lonely")
    _commit(work, "lonely.txt")
    _git(work, "push", "-q", "origin", "lonely")
    _git(work, "switch", "-q", "child")
    rc, _, err = _run(work, "record", "--target", "lonely")
    assert rc == 2 and "merge-base" in err


def test_record_without_origin_head_treats_main_as_default(tmp_path):
    work = _stack(tmp_path)
    _git(work, "remote", "set-head", "origin", "--delete")
    _run(work, "record", "--target", "parent")
    _git(work, "push", "-q", "origin", "--delete", "parent")
    _git(work, "fetch", "-q", "--prune", "origin")
    assert _run(work, "record", "--target", "main")[0] == 0
    r = subprocess.run(["git", "-C", str(work), "config", "branch.child.simoDevStackParent"], capture_output=True)
    assert r.returncode == 1


def test_check_unresolved_base_is_error(tmp_path):
    work = _stack(tmp_path)
    _run(work, "record", "--target", "parent")
    rc, _, err = _run(work, "check", "--base", "nope")
    assert rc == 2 and "does not resolve" in err
