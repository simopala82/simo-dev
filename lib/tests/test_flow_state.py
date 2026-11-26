#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Tests for lib/flow_state.py."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import flow_state  # noqa: E402

SCRIPT = LIB / "flow_state.py"


@pytest.fixture
def active(tmp_path, monkeypatch):
    d = tmp_path / "active"
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(d))
    return d


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    return r


def test_inactive_by_default(active, tmp_path):
    assert not flow_state.is_active("s1", str(_repo(tmp_path)))


def test_session_flag_on_off(active):
    assert flow_state.set_session("s1", True)
    assert flow_state.session_active("s1")
    assert flow_state.is_active("s1", "")
    assert flow_state.set_session("s1", False)
    assert not flow_state.session_active("s1")


def test_worktree_marker_on_off(active, tmp_path):
    repo = _repo(tmp_path)
    assert flow_state.set_worktree(str(repo), True)
    assert (repo / ".git" / flow_state.MARKER_NAME).is_file()
    assert flow_state.is_active("", str(repo))
    sub = repo / "sub"
    sub.mkdir()
    assert flow_state.is_active("other", str(sub))
    assert flow_state.set_worktree(str(repo), False)
    assert not flow_state.worktree_active(str(repo))


def test_linked_worktree_marker_is_per_worktree(active, tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "i"],
                   check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "feat/x"], check=True)
    assert flow_state.set_worktree(str(wt), True)
    assert flow_state.worktree_active(str(wt))
    assert not flow_state.worktree_active(str(repo))


def test_non_repo_path_is_not_active_and_cannot_be_marked(active, tmp_path):
    assert not flow_state.worktree_active(str(tmp_path))
    assert not flow_state.set_worktree(str(tmp_path / "nope"), True)


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "../x"])
def test_unsafe_session_ids_rejected(active, bad):
    assert not flow_state.set_session(bad, True)
    assert not flow_state.session_active(bad)
    assert not active.exists() or not any(active.iterdir())


def test_write_prunes_session_flags_older_than_7_days(active):
    flow_state.set_session("old", True)
    old = active / "old"
    eight_days = time.time() - 8 * 24 * 3600
    os.utime(old, (eight_days, eight_days))
    flow_state.set_session("new", True)
    assert not old.exists()
    assert (active / "new").exists()


def test_cli_is_active_rc(active, tmp_path):
    env = {**os.environ, "SIMO_DEV_FLOW_ACTIVE_DIR": str(active)}

    def run(*a):
        return subprocess.run([sys.executable, str(SCRIPT), *a], env=env, capture_output=True, text=True)

    assert run("is-active", "--session", "s9").returncode == 1
    on = run("on", "--session", "s9")
    assert on.returncode == 0 and on.stdout.strip() == "flow: on (session)"
    assert run("is-active", "--session", "s9").returncode == 0
    assert run("off", "--session", "s9").returncode == 0
    assert run("is-active", "--session", "s9").returncode == 1
    assert run("on").returncode == 2


def test_positive_check_refreshes_the_flag_against_pruning(active):
    flow_state.set_session("long", True)
    flag = active / "long"
    eight_days = time.time() - 8 * 24 * 3600
    os.utime(flag, (eight_days, eight_days))
    assert flow_state.session_active("long")
    flow_state.set_session("other", True)
    assert flag.exists()


def test_task_id_is_stored_and_kept_by_a_later_on(active):
    assert flow_state.set_session("s1", True, "20261109T1712-abcd1234")
    assert flow_state.session_task("s1") == "20261109T1712-abcd1234"
    assert flow_state.set_session("s1", True)
    assert flow_state.session_task("s1") == "20261109T1712-abcd1234"
    assert flow_state.session_active("s1")


def test_legacy_epoch_only_flag_is_active_without_a_task(active):
    active.mkdir(parents=True)
    (active / "old").write_text("123\n")
    assert flow_state.session_active("old")
    assert flow_state.session_task("old") == ""


def test_clear_session_task_keeps_the_flow_on(active):
    flow_state.set_session("s1", True, "20261109T1712-abcd1234")
    assert flow_state.clear_session_task("s1")
    assert flow_state.session_active("s1")
    assert flow_state.session_task("s1") == ""
    assert not flow_state.clear_session_task("missing")


def test_malformed_task_id_is_dropped_but_the_flow_still_starts(active, tmp_path, capsys):
    assert flow_state.set_session("s1", True, "a/b")
    assert flow_state.session_active("s1") and flow_state.session_task("s1") == ""
    repo = _repo(tmp_path)
    assert flow_state.set_worktree(str(repo), True, "20261109T1712-abc/")
    assert flow_state.worktree_active(str(repo)) and flow_state.worktree_task(str(repo)) == ""
    assert "ignoring malformed task id" in capsys.readouterr().err


def test_marker_inherits_the_session_task_id(active, tmp_path):
    repo = _repo(tmp_path)
    env = {**os.environ, "SIMO_DEV_FLOW_ACTIVE_DIR": str(active)}

    def run(*a):
        return subprocess.run([sys.executable, str(SCRIPT), *a], env=env, capture_output=True, text=True)

    assert run("on", "--session", "s2", "--task-id", "20261109T1712-s2").returncode == 0
    assert run("on", "--session", "s2", "--repo", str(repo)).returncode == 0
    assert flow_state.worktree_task(str(repo)) == "20261109T1712-s2"
    assert flow_state.session_task("s2") == "20261109T1712-s2"
