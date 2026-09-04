"""Tests for lib/check_base_freshness.py (stale-base guard)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "check_base_freshness.py"

spec = importlib.util.spec_from_file_location("check_base_freshness", LIB)
cbf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cbf)


def test_decide_not_advanced():
    assert cbf.decide(False, []) == (0, "")


def test_decide_advanced_disjoint():
    rc, msg = cbf.decide(True, [])
    assert rc == 0
    assert "disjoint" in msg


def test_decide_advanced_overlap_stops():
    rc, msg = cbf.decide(True, ["lib/a.py", "lib/b.py"])
    assert rc == 1
    assert "lib/a.py" in msg and "lib/b.py" in msg
    assert "rebase" in msg.lower()


def test_main_safe(monkeypatch):
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (False, []))
    assert cbf.main(["--repo", "/x"]) == 0


def test_main_disjoint_advance(monkeypatch, capsys):
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (True, []))
    assert cbf.main(["--repo", "/x"]) == 0
    assert "disjoint" in capsys.readouterr().err


def test_main_overlap_stops(monkeypatch, capsys):
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (True, ["lib/x.py"]))
    rc = cbf.main(["--repo", "/x"])
    assert rc == 1
    assert "lib/x.py" in capsys.readouterr().out


def test_main_git_error_returns_2(monkeypatch):
    def boom(repo):
        raise cbf.GitError("origin/HEAD not set")

    monkeypatch.setattr(cbf, "default_base", boom)
    assert cbf.main(["--repo", "/x"]) == 2


def test_main_base_override_skips_default(monkeypatch):
    called = {"default": False}

    def default_base(repo):
        called["default"] = True
        return "master"

    monkeypatch.setattr(cbf, "default_base", default_base)
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (False, []))
    assert cbf.main(["--repo", "/x", "--base", "develop"]) == 0
    assert called["default"] is False


# --- integration tests for analyze() against real git repos ---


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path, advance, feat_commit=True):
    """Build origin + clone; feat forks at init; base advances per `advance`
    ('overlap' touches shared.py, 'disjoint' touches other.py, None: no advance).
    feat_commit=True → feat commits a shared.py change; False → feat has no commit
    (HEAD sits at the fork point, to isolate working-tree-only overlap).
    Returns the clone path with HEAD on feat."""
    up, clone = tmp_path / "up", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    (clone / "shared.py").write_text("a\n")
    (clone / "other.py").write_text("x\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "init")
    _git(clone, "remote", "add", "origin", str(up))
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    _git(clone, "checkout", "-q", "-b", "feat")
    if feat_commit:
        (clone / "shared.py").write_text("a\nfeat\n")
        _git(clone, "add", "-A")
        _git(clone, "commit", "-qm", "feat change")
    if advance:
        _git(clone, "checkout", "-q", "master")
        target = "shared.py" if advance == "overlap" else "other.py"
        (clone / target).write_text("advanced\n")
        _git(clone, "commit", "-qam", "base advance")
        _git(clone, "push", "-q", "origin", "master")
        _git(clone, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
        _git(clone, "checkout", "-q", "feat")
    return clone


def test_analyze_no_advance(tmp_path):
    clone = _make_repo(tmp_path, advance=None)
    assert cbf.analyze(str(clone), "master") == (False, [])


def test_analyze_overlap(tmp_path):
    clone = _make_repo(tmp_path, advance="overlap")
    assert cbf.analyze(str(clone), "master") == (True, ["shared.py"])


def test_analyze_disjoint(tmp_path):
    clone = _make_repo(tmp_path, advance="disjoint")
    assert cbf.analyze(str(clone), "master") == (True, [])


def test_analyze_catches_uncommitted_overlap(tmp_path):
    """feat has NO committed change to shared.py (HEAD sits at the fork point);
    it touches shared.py only in the working tree. The pre-fix logic
    (merge_base..HEAD) returns [] here; the fix (merge_base..worktree) must catch
    the overlap. Isolates the pre-commit blind spot."""
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    (clone / "shared.py").write_text("a\nuncommitted feat\n")  # unstaged, uncommitted
    assert cbf.analyze(str(clone), "master") == (True, ["shared.py"])


def test_default_base_falls_back_when_head_unset(tmp_path):
    clone = _make_repo(tmp_path, advance=None)
    _git(clone, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
    assert cbf.default_base(str(clone)) == "master"


# --- --files / --plan-files override ---


def test_files_from_plan_bare_path():
    text = "**Files:**\n- Create: `lib/foo.py`\n- Test: `tests/test_foo.py`\n"
    assert cbf.files_from_plan(text) == {"lib/foo.py", "tests/test_foo.py"}


def test_files_from_plan_strips_line_range():
    text = "- Modify: `lib/bar.py:12-34`\n"
    assert cbf.files_from_plan(text) == {"lib/bar.py"}


def test_files_from_plan_ignores_anchor_token():
    text = "- Modify: `docs/adr/0001-x.md` at `## Some Heading`\n"
    assert cbf.files_from_plan(text) == {"docs/adr/0001-x.md"}


def test_files_from_plan_dir_unions_multiple_plans(tmp_path):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "a.md").write_text("- Create: `lib/a.py`\n")
    (plans / "b.md").write_text("- Modify: `lib/b.py:1-2`\n")
    assert cbf.files_from_plan_dir(str(tmp_path), "master") == ["lib/a.py", "lib/b.py"]


def test_files_from_plan_dir_empty_when_no_plans(tmp_path):
    assert cbf.files_from_plan_dir(str(tmp_path), "master") == []


def test_analyze_files_override_replaces_working_tree_scan(tmp_path):
    """feat has no working-tree change to shared.py at all (feat_commit=False,
    no post-checkout edit) -- a bare analyze() call must report no overlap.
    Passing files=['shared.py'] must still catch it: at a task-intake gate the
    working tree holds only the plan, never the target files themselves."""
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    assert cbf.analyze(str(clone), "master") == (True, [])
    assert cbf.analyze(str(clone), "master", files=["shared.py"]) == (True, ["shared.py"])


def test_analyze_files_override_empty_list_means_no_overlap(tmp_path):
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    assert cbf.analyze(str(clone), "master", files=[]) == (True, [])


def test_main_files_flag_passes_override(monkeypatch):
    captured = {}

    def fake_analyze(repo, base, files=None):
        captured["files"] = files
        return (False, [])

    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", fake_analyze)
    assert cbf.main(["--repo", "/x", "--files", "a.py", "b.py"]) == 0
    assert captured["files"] == ["a.py", "b.py"]


def test_main_plan_files_flag_derives_from_disk(tmp_path, monkeypatch):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Create: `lib/x.py`\n")
    captured = {}

    def fake_analyze(repo, base, files=None):
        captured["files"] = files
        return (False, [])

    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", fake_analyze)
    assert cbf.main(["--repo", str(tmp_path), "--plan-files"]) == 0
    assert captured["files"] == ["lib/x.py"]


def test_main_files_and_plan_files_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cbf.main(["--repo", "/x", "--files", "a.py", "--plan-files"])


def test_main_plan_files_unreadable_plan_returns_2(tmp_path, monkeypatch):
    """A plan file that can't be decoded must map to rc 2 (error), never an
    uncaught exception that would otherwise surface as an unhandled crash
    instead of the documented 'never a false STOP' contract."""
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_bytes(b"\xff\xfe- Create: `lib/x.py`\n")
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    assert cbf.main(["--repo", str(tmp_path), "--plan-files"]) == 2


def test_files_from_plan_dir_excludes_merged_plans(tmp_path):
    """A plan file already present in origin/<base> (already merged) has its
    Files: bullets excluded; a plan file that does NOT yet exist there (the
    current task's own new plan) is still included. Isolates the bug: an
    unscoped union resurrects declared files from tasks long since merged
    and irrelevant to the branch under check."""
    up, clone = tmp_path / "up", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "old.md").write_text("- Create: `old_target.py`\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "old plan, merged")
    _git(clone, "remote", "add", "origin", str(up))
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    _git(clone, "checkout", "-q", "-b", "feat")
    (plans / "new.md").write_text("- Create: `new_target.py`\n")  # uncommitted -- current task's plan
    assert cbf.files_from_plan_dir(str(clone), "master") == ["new_target.py"]
