"""Tests for lib/repo_config.py (global per-repo config)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "repo_config.py"

spec = importlib.util.spec_from_file_location("repo_config", LIB)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _git_repo(path: Path, origin: str | None) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


def test_get_unconfigured_repo_returns_empty(tmp_path):
    repo = _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    assert rc.get_value(str(repo), "release_skill", tmp_path / "repo.json") == ""


def test_set_literal_id_then_get_from_checkout(tmp_path):
    cfg = tmp_path / "repo.json"
    repo = _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    rc.set_value("https://Git.Example.com/team/app.git", "release_skill", "my-plugin:my-release", cfg)
    assert rc.get_value(str(repo), "release_skill", cfg) == "my-plugin:my-release"


def test_set_from_checkout_path_keys_by_repo_id(tmp_path):
    cfg = tmp_path / "repo.json"
    repo = _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    rc.set_value(str(repo), "release_skill", "my-plugin:my-release", cfg)
    assert rc._load(cfg) == {"git.example.com/team/app": {"release_skill": "my-plugin:my-release"}}


@pytest.mark.parametrize("origin", ["git@git.example.com:team/app.git", None])
def test_linked_worktree_resolves_same_repo(tmp_path, origin):
    cfg = tmp_path / "repo.json"
    repo = _git_repo(tmp_path / "r", origin)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e",
                    "commit", "-q", "--allow-empty", "--no-verify", "-m", "x"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
    rc.set_value(str(repo), "release_skill", "my-plugin:my-release", cfg)
    assert rc.get_value(str(wt), "release_skill", cfg) == "my-plugin:my-release"


def test_other_repo_not_matched(tmp_path):
    cfg = tmp_path / "repo.json"
    other = _git_repo(tmp_path / "o", "git@git.example.com:team/other.git")
    rc.set_value("git.example.com/team/app", "release_skill", "my-plugin:my-release", cfg)
    assert rc.get_value(str(other), "release_skill", cfg) == ""


def test_set_rejects_unknown_field(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        rc.set_value("git.example.com/team/app", "deploy_cmd", "x", tmp_path / "repo.json")
    assert exc.value.code == 2
    assert "unknown field" in capsys.readouterr().err


def test_get_invalid_json_exits_1(tmp_path):
    cfg = tmp_path / "repo.json"
    cfg.write_text("{not json")
    repo = _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    with pytest.raises(SystemExit) as exc:
        rc.get_value(str(repo), "release_skill", cfg)
    assert exc.value.code == 1


def test_set_rejects_missing_absolute_path(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        rc.set_value(str(tmp_path / "nope"), "release_skill", "x", tmp_path / "repo.json")
    assert exc.value.code == 2
    assert "not a directory" in capsys.readouterr().err


def test_set_expands_tilde_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "repo.json"
    _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    rc.set_value("~/r", "release_skill", "my-plugin:my-release", cfg)
    assert rc._load(cfg) == {"git.example.com/team/app": {"release_skill": "my-plugin:my-release"}}


def test_set_rejects_tilde_path(tmp_path):
    with pytest.raises(SystemExit) as exc:
        rc.set_value("~/nope-xyz", "release_skill", "x", tmp_path / "repo.json")
    assert exc.value.code == 2


def test_get_rejects_unknown_field(tmp_path):
    repo = _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    with pytest.raises(SystemExit) as exc:
        rc.get_value(str(repo), "release_skil", tmp_path / "repo.json")
    assert exc.value.code == 2


def test_set_rejects_bare_name(tmp_path, capsys):
    cfg = tmp_path / "repo.json"
    with pytest.raises(SystemExit) as exc:
        rc.set_value("app", "release_skill", "x", cfg)
    assert exc.value.code == 2
    assert "repo-id" in capsys.readouterr().err
    assert not cfg.exists()


@pytest.mark.parametrize("content", ["[]", '{"git.example.com/team/app": "x"}'])
def test_get_non_object_json_exits_1(tmp_path, capsys, content):
    cfg = tmp_path / "repo.json"
    cfg.write_text(content)
    repo = _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    with pytest.raises(SystemExit) as exc:
        rc.get_value(str(repo), "release_skill", cfg)
    assert exc.value.code == 1
    assert "object of objects" in capsys.readouterr().err


def _plain_dir_outside_git(tmp_path: Path) -> Path:
    plain = tmp_path / "plain"
    plain.mkdir()
    probe = subprocess.run(["git", "-C", str(plain), "rev-parse", "--git-dir"], capture_output=True)
    if probe.returncode == 0:
        pytest.skip("tmp_path sits inside a git repo")
    return plain


def test_cli_get_non_git_dir_exits_1(tmp_path):
    plain = _plain_dir_outside_git(tmp_path)
    out = subprocess.run([sys.executable, str(LIB), "get", "--repo", str(plain), "release_skill",
                          "--config", str(tmp_path / "repo.json")], capture_output=True, text=True)
    assert out.returncode == 1
    assert "not inside a git repository" in out.stderr


def test_cli_set_non_git_dir_exits_1(tmp_path):
    plain = _plain_dir_outside_git(tmp_path)
    cfg = tmp_path / "repo.json"
    out = subprocess.run([sys.executable, str(LIB), "set", str(plain), "release_skill", "x",
                          "--config", str(cfg)], capture_output=True, text=True)
    assert out.returncode == 1
    assert not cfg.exists()


def test_env_override_used(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMO_DEV_REPO_CONFIG", str(tmp_path / "env.json"))
    assert rc.config_path() == tmp_path / "env.json"


def test_cli_set_then_get(tmp_path):
    cfg = tmp_path / "repo.json"
    repo = _git_repo(tmp_path / "r", "git@git.example.com:team/app.git")
    s = subprocess.run([sys.executable, str(LIB), "set", "git.example.com/team/app", "release_skill",
                        "my-plugin:my-release", "--config", str(cfg)], capture_output=True, text=True)
    assert s.returncode == 0, s.stderr
    g = subprocess.run([sys.executable, str(LIB), "get", "--repo", str(repo), "release_skill",
                        "--config", str(cfg)], capture_output=True, text=True)
    assert g.returncode == 0
    assert g.stdout == "my-plugin:my-release"


def test_cli_get_unconfigured_prints_nothing(tmp_path):
    repo = _git_repo(tmp_path / "r", None)
    out = subprocess.run([sys.executable, str(LIB), "get", "--repo", str(repo), "release_skill",
                          "--config", str(tmp_path / "repo.json")], capture_output=True, text=True)
    assert out.returncode == 0
    assert out.stdout == ""
    assert out.stderr == ""
