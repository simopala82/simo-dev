"""Tests for lib/check_local_base_ahead.py (local-base-ahead-of-origin guard)."""
import subprocess
import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "check_local_base_ahead.py"

spec = importlib.util.spec_from_file_location("check_local_base_ahead", LIB)
clba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clba)


def test_decide_not_ahead():
    assert clba.decide(False, []) == (0, "")


def test_decide_ahead_stops():
    rc, msg = clba.decide(True, ["abc1234 fix: something", "def5678 chore: another"], base="master")
    assert rc == 1
    assert "abc1234 fix: something" in msg
    assert "def5678 chore: another" in msg
    assert "master" in msg
    assert "ask" in msg.lower()
    assert "unasked" in msg.lower()


def test_analyze_not_ancestor(monkeypatch):
    monkeypatch.setattr(clba, "_has_local_branch", lambda repo, base: True)
    monkeypatch.setattr(clba, "_is_ancestor", lambda repo, ancestor, descendant: False)
    ahead, commits = clba.analyze("/x", "master")
    assert ahead is False
    assert commits == []


def test_analyze_ancestor_no_extra_commits(monkeypatch):
    monkeypatch.setattr(clba, "_has_local_branch", lambda repo, base: True)
    monkeypatch.setattr(clba, "_is_ancestor", lambda repo, ancestor, descendant: True)
    monkeypatch.setattr(clba, "_git", lambda repo, args: "")
    ahead, commits = clba.analyze("/x", "master")
    assert ahead is False
    assert commits == []


def test_analyze_ancestor_with_extra_commits(monkeypatch):
    monkeypatch.setattr(clba, "_has_local_branch", lambda repo, base: True)
    monkeypatch.setattr(clba, "_is_ancestor", lambda repo, ancestor, descendant: True)

    def fake_git(repo, args):
        if args[0] == "rev-list":
            return "abc1234\ndef5678\n"
        if args[0] == "log":
            return "abc1234 fix: something\ndef5678 chore: another\n"
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(clba, "_git", fake_git)
    ahead, commits = clba.analyze("/x", "master")
    assert ahead is True
    assert commits == ["abc1234 fix: something", "def5678 chore: another"]


def test_is_ancestor_true(monkeypatch):
    def fake_run(args, **kwargs):
        assert args[:4] == ["git", "-C", "/x", "merge-base"]
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(clba.subprocess, "run", fake_run)
    assert clba._is_ancestor("/x", "master", "HEAD") is True


def test_is_ancestor_false_on_rc1(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, output="", stderr="")

    monkeypatch.setattr(clba.subprocess, "run", fake_run)
    assert clba._is_ancestor("/x", "master", "HEAD") is False


def test_is_ancestor_raises_on_other_error(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(128, args, output="", stderr="fatal: not a valid object name master")

    monkeypatch.setattr(clba.subprocess, "run", fake_run)
    with pytest.raises(clba.GitError):
        clba._is_ancestor("/x", "master", "HEAD")


def test_main_safe(monkeypatch):
    monkeypatch.setattr(clba, "analyze", lambda repo, base: (False, []))
    assert clba.main(["--repo", "/x", "--base", "master"]) == 0


def test_main_stops(monkeypatch, capsys):
    monkeypatch.setattr(clba, "analyze", lambda repo, base: (True, ["abc1234 fix: x"]))
    rc = clba.main(["--repo", "/x", "--base", "master"])
    assert rc == 1
    assert "abc1234 fix: x" in capsys.readouterr().out


def test_main_git_error_returns_2(monkeypatch):
    def boom(repo, base):
        raise clba.GitError("git merge-base --is-ancestor: fatal: not a valid object name")

    monkeypatch.setattr(clba, "analyze", boom)
    assert clba.main(["--repo", "/x", "--base", "master"]) == 2


def test_main_requires_repo_and_base():
    with pytest.raises(SystemExit):
        clba.main([])


def _real_git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], check=True, capture_output=True)


def test_main_absent_local_base_is_safe(tmp_path):
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _real_git(tmp_path, "init", "-q", "--bare", "-b", "master", str(origin))
    _real_git(tmp_path, "clone", "-q", str(origin), str(work))
    _real_git(work, "commit", "-q", "--allow-empty", "-m", "init")
    _real_git(work, "push", "-q", "origin", "master")
    _real_git(work, "checkout", "-q", "-b", "feat")
    _real_git(work, "commit", "-q", "--allow-empty", "-m", "feat")
    _real_git(work, "branch", "-q", "-D", "master")
    assert clba.main(["--repo", str(work), "--base", "master"]) == 0
