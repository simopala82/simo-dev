"""Tests for lib/repo_identity.py (single-sourced repo/branch identity)."""
import importlib.util
import subprocess
from pathlib import Path

LIB = Path(__file__).parent.parent / "repo_identity.py"

spec = importlib.util.spec_from_file_location("repo_identity", LIB)
ri = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ri)


def test_normalize_ssh_url():
    assert ri.normalize_url("git@gitlab.example:group/repo.git") == "gitlab.example/group/repo"


def test_normalize_https_url():
    assert ri.normalize_url("https://gitlab.example/group/repo.git") == "gitlab.example/group/repo"


def test_normalize_https_and_ssh_variants_match():
    assert ri.normalize_url("https://GitLab.example/Group/Repo") == ri.normalize_url("git@gitlab.example:Group/Repo.git")


def test_normalize_url_with_port():
    assert ri.normalize_url("ssh://git@host.example:2222/team/repo.git") == "host.example/2222/team/repo"


def test_normalize_https_url_with_port():
    assert ri.normalize_url("https://host.example:8080/team/repo.git") == "host.example/8080/team/repo"


def test_normalize_strips_credentials():
    # userinfo (user:password@) must never leak into the persisted repo-id
    assert ri.normalize_url("https://user:s3cret@gitlab.example/team/repo.git") == "gitlab.example/team/repo"
    assert ri.normalize_url("https://user:p@ss@gitlab.example/team/repo.git") == "gitlab.example/team/repo"
    assert ri.normalize_url("https://user:s3cret@gitlab.example/team/repo.git") == ri.normalize_url("git@gitlab.example:team/repo.git")


def _init_repo(path, branch, origin=None):
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    if origin:
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=str(path), check=True)
    return path


def test_repo_id_uses_repo_path_not_cwd(tmp_path, monkeypatch):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_repo(repo_a, "master", origin="git@gitlab.example:team/repo-a.git")
    _init_repo(repo_b, "master", origin="git@gitlab.example:team/repo-b.git")
    monkeypatch.chdir(repo_a)
    assert ri.repo_id(repo_path=str(repo_b)) == "gitlab.example/team/repo-b"


def test_current_branch_uses_repo_path_not_cwd(tmp_path, monkeypatch):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_repo(repo_a, "branch-a")
    _init_repo(repo_b, "branch-b")
    monkeypatch.chdir(repo_a)
    # This is the worktree bug: without repo_path, the process cwd (repo_a) would win.
    assert ri.current_branch(repo_path=str(repo_b)) == "branch-b"


def test_repo_id_without_repo_path_falls_back_to_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo, "master", origin="git@gitlab.example:team/repo-c.git")
    monkeypatch.chdir(repo)
    assert ri.repo_id() == "gitlab.example/team/repo-c"
