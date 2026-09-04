"""Tests for lib/afk_state.py (per-(repo+branch) AFK liveness state)."""
import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "afk_state.py"

spec = importlib.util.spec_from_file_location("afk_state", LIB)
afk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(afk)

NOW = 1765370000


def test_parse_valid_active_entry():
    assert afk._parse("gitlab.example/team/repo:feat/x:active:1765370000") == (
        "gitlab.example/team/repo", "feat/x", "active", 1765370000)


def test_parse_valid_stopped_entry():
    assert afk._parse("gitlab.example/team/repo:master:stopped:1765370000") == (
        "gitlab.example/team/repo", "master", "stopped", 1765370000)


def test_parse_entry_with_colon_in_repo_id():
    assert afk._parse("host:8080/team/repo:master:active:1765370000") == (
        "host:8080/team/repo", "master", "active", 1765370000)


def test_parse_unknown_state_returns_none():
    assert afk._parse("repo:branch:running:1765370000") is None


def test_parse_non_numeric_epoch_returns_none():
    assert afk._parse("repo:branch:active:notanumber") is None


def test_parse_legacy_three_field_entry_returns_none():
    # a legacy auto-publish-style 3-field line must never be read as AFK state
    assert afk._parse("gitlab.example/team/repo:master:1765370000") is None


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Isolated state file, frozen clock, stubbed git context (repo-a / master)."""
    f = tmp_path / "afk"
    monkeypatch.setenv("AFK_STATE_FILE", str(f))
    monkeypatch.setattr(afk, "_now", lambda: float(NOW))
    monkeypatch.setattr(afk, "repo_id", lambda: "gitlab.example/team/repo-a")
    monkeypatch.setattr(afk, "current_branch", lambda: "master")
    return f


def test_arm_writes_active_entry(state):
    assert afk.arm() == 0
    assert state.read_text() == f"gitlab.example/team/repo-a:master:active:{NOW}\n"


def test_stop_writes_stopped_entry(state):
    assert afk.stop() == 0
    assert state.read_text() == f"gitlab.example/team/repo-a:master:stopped:{NOW}\n"


def test_arm_then_stop_replaces_own_entry(state):
    afk.arm()
    afk.stop()
    assert state.read_text() == f"gitlab.example/team/repo-a:master:stopped:{NOW}\n"


def test_arm_refreshes_epoch(state):
    state.write_text(f"gitlab.example/team/repo-a:master:active:{NOW - 100}\n")
    afk.arm()
    assert state.read_text() == f"gitlab.example/team/repo-a:master:active:{NOW}\n"


def test_status_active(state):
    afk.arm()
    assert afk.status() == 0  # prints "active"


def test_is_stopped_true_when_stopped(state):
    afk.stop()
    assert afk.is_stopped() == 0


def test_is_stopped_false_when_active(state):
    afk.arm()
    assert afk.is_stopped() == 1


def test_is_stopped_false_when_absent(state):
    assert afk.is_stopped() == 1


def test_is_active_true_when_active(state):
    afk.arm()
    assert afk.is_active() == 0


def test_is_active_false_when_stopped(state):
    afk.stop()
    assert afk.is_active() == 1


def test_other_repo_stop_does_not_stop_this_repo(state):
    # THE bug this helper fixes: repo-b parking ('stopped') must NOT make repo-a
    # report stopped — repo-a simply has no entry, so is-stopped exits 1.
    state.write_text(f"gitlab.example/team/repo-b:master:stopped:{NOW}\n")
    assert afk.is_stopped() == 1
    assert afk.is_active() == 1


def test_other_branch_stop_does_not_stop_this_branch(state, monkeypatch):
    state.write_text(f"gitlab.example/team/repo-a:feat/other:stopped:{NOW}\n")
    assert afk.is_stopped() == 1  # current branch is 'master', untouched


def test_stop_preserves_other_repo_entry(state):
    state.write_text(f"gitlab.example/team/repo-b:master:active:{NOW}\n")
    afk.stop()
    assert state.read_text() == (
        f"gitlab.example/team/repo-b:master:active:{NOW}\n"
        f"gitlab.example/team/repo-a:master:stopped:{NOW}\n"
    )


def test_expired_stopped_entry_is_absent(state):
    state.write_text(f"gitlab.example/team/repo-a:master:stopped:{NOW - afk.TTL_SECONDS - 1}\n")
    assert afk.is_stopped() == 1  # expired → absent


def test_arm_purges_expired_and_malformed_entries(state):
    state.write_text(
        "garbage-line\n"
        "gitlab.example/team/repo-a:master:1765370000\n"  # legacy 3-field, dropped
        f"gitlab.example/team/repo-b:old:active:{NOW - afk.TTL_SECONDS - 1}\n"  # expired
        f"gitlab.example/team/repo-b:live:active:{NOW - 100}\n"  # kept
    )
    afk.arm()
    assert state.read_text() == (
        f"gitlab.example/team/repo-b:live:active:{NOW - 100}\n"
        f"gitlab.example/team/repo-a:master:active:{NOW}\n"
    )


def test_status_missing_state_file_prints_absent(state):
    assert not state.exists()
    assert afk.status() == 0  # prints "absent", no crash


def test_main_dispatch(state):
    assert afk.main(["arm"]) == 0
    assert afk.main(["is-active"]) == 0
    assert afk.main(["is-stopped"]) == 1
    assert afk.main(["stop"]) == 0
    assert afk.main(["is-stopped"]) == 0
    assert afk.main(["status"]) == 0
