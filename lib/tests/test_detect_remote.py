#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Direct unit tests for detect_remote.py -- Python port of detect_remote.sh (OSS
migration step 7c), in the same direct-import style as hooks/tests/test_git_match.py.
Ported from the bash predecessor's own hand-rolled suite,
skills/mr-followup/lib/test_detect_remote.sh (never wired to pytest)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "skills" / "mr-followup" / "lib" / "detect_remote.py"
spec = importlib.util.spec_from_file_location("detect_remote", MODULE)
detect_remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect_remote)


@pytest.mark.parametrize("url,forge,slug", [
    ("git@github.com:owner/repo.git", "github", "owner/repo"),
    ("https://github.com/owner/repo", "github", "owner/repo"),
    ("git@gitlab.example.com:group/sub/repo.git", "gitlab", "group/sub/repo"),
    ("https://gitlab.example.com/group/repo.git", "gitlab", "group/repo"),
    ("ssh://git@gitlab.example.com/group/repo.git", "gitlab", "group/repo"),
    ("git@gitlab.example.com:group/repo.git", "gitlab", "group/repo"),
])
def test_known_hosts_classify_and_slug_correctly(monkeypatch, url, forge, slug):
    monkeypatch.setenv("DETECT_REMOTE_URL", url)
    result = detect_remote.detect(None)
    assert result is not None
    assert result[0] == forge and result[1] == slug


@pytest.mark.parametrize("url", [
    "git@bitbucket.org:owner/repo.git",
    "git@no-such-alias-xyz:owner/repo.git",
])
def test_unknown_host_is_rejected(monkeypatch, url):
    monkeypatch.setenv("DETECT_REMOTE_URL", url)
    assert detect_remote.detect(None) is None


def test_alias_containing_gitlab_is_not_shortcircuited(monkeypatch):
    """An SSH alias containing 'gitlab' in its raw name, but unresolved by
    ~/.ssh/config in this environment, must NOT match the anchored gitlab.* pattern
    on the raw alias -- it must fall through to ssh -G resolution and fail as
    unknown, not silently succeed with api_host=<the alias itself>."""
    monkeypatch.setenv("DETECT_REMOTE_URL", "git@gitlab-personal-test-alias:owner/repo.git")
    result = detect_remote.detect(None)
    if result is not None:
        assert result[2] != "gitlab-personal-test-alias"


def test_api_host_is_the_classified_hostname(monkeypatch):
    monkeypatch.setenv("DETECT_REMOTE_URL", "git@gitlab.example.com:group/sub/repo.git")
    result = detect_remote.detect(None)
    assert result == ("gitlab", "group/sub/repo", "gitlab.example.com")


def test_repo_arg_is_read_from_the_passed_path_not_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("DETECT_REMOTE_URL", raising=False)
    cwd_repo = tmp_path / "cwd_repo"
    target_repo = tmp_path / "target_repo"
    cwd_repo.mkdir()
    target_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=cwd_repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@bitbucket.org:owner/wrong.git"],
                   cwd=cwd_repo, check=True)
    subprocess.run(["git", "init", "-q"], cwd=target_repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:owner/right.git"],
                   cwd=target_repo, check=True)
    monkeypatch.chdir(cwd_repo)
    result = detect_remote.detect(str(target_repo))
    assert result == ("github", "owner/right", "github.com")


def test_missing_repo_arg_and_no_override_is_rejected(monkeypatch):
    monkeypatch.delenv("DETECT_REMOTE_URL", raising=False)
    assert detect_remote.detect(None) is None


def test_main_prints_the_key_value_line_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setenv("DETECT_REMOTE_URL", "git@github.com:owner/repo.git")
    rc = detect_remote.main(["/some/path"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "FORGE=github" in out and "SLUG=owner/repo" in out and "API_HOST=github.com" in out


def test_main_exits_one_on_unknown_forge(monkeypatch):
    monkeypatch.setenv("DETECT_REMOTE_URL", "git@bitbucket.org:owner/repo.git")
    assert detect_remote.main(["/some/path"]) == 1
