"""Tests for lib/afk_state.py (per-(repo+branch) AFK liveness state)."""
import importlib.util
import subprocess
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
    monkeypatch.setattr(afk, "repo_id", lambda repo=None: "gitlab.example/team/repo-a")
    monkeypatch.setattr(afk, "current_branch", lambda repo=None: "master")
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


def test_arm_with_explicit_repo_threads_path_to_identity_calls(state, monkeypatch):
    seen = {}

    def fake_repo_id(repo=None):
        seen["repo_id_arg"] = repo
        return "gitlab.example/team/repo-a"

    def fake_current_branch(repo=None):
        seen["current_branch_arg"] = repo
        return "master"

    monkeypatch.setattr(afk, "repo_id", fake_repo_id)
    monkeypatch.setattr(afk, "current_branch", fake_current_branch)
    assert afk.arm(repo="/some/worktree") == 0
    assert seen["repo_id_arg"] == "/some/worktree"
    assert seen["current_branch_arg"] == "/some/worktree"


def test_arm_without_repo_arg_passes_none_to_identity_calls(state, monkeypatch):
    seen = {}

    def fake_repo_id(repo=None):
        seen["repo_id_arg"] = repo
        return "gitlab.example/team/repo-a"

    monkeypatch.setattr(afk, "repo_id", fake_repo_id)
    assert afk.arm() == 0
    assert seen["repo_id_arg"] is None


def test_main_dispatch_forwards_repo_flag(state, monkeypatch, tmp_path):
    seen = {}

    def fake_repo_id(repo=None):
        seen["repo_id_arg"] = repo
        return "gitlab.example/team/repo-a"

    monkeypatch.setattr(afk, "repo_id", fake_repo_id)
    repo_dir = str(tmp_path)
    assert afk.main(["arm", "--repo", repo_dir]) == 0
    assert seen["repo_id_arg"] == repo_dir


def test_main_dispatch_without_repo_flag_passes_none(state, monkeypatch):
    seen = {}

    def fake_repo_id(repo=None):
        seen["repo_id_arg"] = repo
        return "gitlab.example/team/repo-a"

    monkeypatch.setattr(afk, "repo_id", fake_repo_id)
    assert afk.main(["arm"]) == 0
    assert seen["repo_id_arg"] is None


def test_main_rejects_nonexistent_repo_path(state):
    with pytest.raises(SystemExit) as exc_info:
        afk.main(["is-stopped", "--repo", "/definitely/not/a/real/path"])
    assert exc_info.value.code == 2


def test_main_rejects_repo_that_is_not_a_git_repository(tmp_path, capsys):
    # a real, existing directory that is not a git repo — repo_identity's own
    # sys.exit("not inside a git repository") must be remapped to rc=2, not left
    # at rc=1 (indistinguishable from is-stopped's legitimate "absent" answer).
    # Also pins the forwarded message text (via stderr — argparse's parser.error
    # prints the message and exits with a bare int code, not a message-bearing
    # SystemExit), not just the exit code — a regression to a fixed message
    # here would silently mask the real cause.
    with pytest.raises(SystemExit) as exc_info:
        afk.main(["is-stopped", "--repo", str(tmp_path)])
    assert exc_info.value.code == 2
    assert "not inside a git repository" in capsys.readouterr().err


def test_main_reports_detached_head_as_the_real_cause_not_missing_repo(tmp_path, capsys):
    # a real, valid git repo on a detached HEAD raises a DIFFERENT
    # repo_identity SystemExit ("no current branch") than the not-a-git-repo
    # case above — the forwarded message must say so, not the fixed
    # "not a git repository" string a prior iteration used.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=tmp_path, check=True)
    with pytest.raises(SystemExit) as exc_info:
        afk.main(["is-stopped", "--repo", str(tmp_path)])
    assert exc_info.value.code == 2
    assert "no current branch" in capsys.readouterr().err
