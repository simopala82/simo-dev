"""Tests for lib/build_review_ctx.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).parent.parent / "build_review_ctx.py"
FAKE = Path(__file__).parent / "fixtures" / "fake_jira.py"
FIX = Path(__file__).parent / "fixtures"
JIRA_SCOPE_FETCH = Path(__file__).parent.parent.parent / "private" / "jira_scope_fetch.py"
TRACKER_CONFIG = Path(__file__).parent.parent / "tracker_config.py"


def _cache_safe_path(tmp_path, src):
    # copies src into tmp_path so its path never trips tracker_config.py's cache-path guard
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    return dst


def run_build(meta_name, tracker_config_path=None):
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["git", "init", tmp], check=True, capture_output=True)
        env = dict(os.environ)
        env["DEV_REVIEW_JIRA_BIN"] = f"{sys.executable} {FAKE}"
        if tracker_config_path:
            env["SIMO_DEV_TRACKER_CONFIG"] = str(tracker_config_path)
        result = subprocess.run(
            [sys.executable, str(LIB), str(FIX / meta_name)],
            capture_output=True, text=True, env=env, cwd=tmp,
        )
        return result.returncode, result.stdout, result.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _configured_tracker(tmp_path):
    path = tmp_path / "tracker.json"
    subprocess.run(
        [sys.executable, str(TRACKER_CONFIG), "set", "PROJ", "scope_fetch_cmd",
         f"{sys.executable} {_cache_safe_path(tmp_path, JIRA_SCOPE_FETCH)}", "--config", str(path)],
        check=True,
    )
    return path


def test_extracts_key_and_fetches_light_scope(tmp_path):
    rc, out, err = run_build("review_meta_gitlab.json", _configured_tracker(tmp_path))
    assert rc == 0, err
    ctx = json.loads(out)
    assert ctx["jira_key"] == "PROJ-1234"
    assert "PROJ-1234: Canned summary" in ctx["jira_scope"]
    assert "comments_shown=0" in ctx["jira_scope"]
    assert ctx["branch_meta"]["target"] == "master"
    assert ctx["ticket_summary"].startswith("Add rate limit")


def test_no_key_yields_empty_jira_fields():
    rc, out, err = run_build("review_meta_nokey.json")
    assert rc == 0, err
    ctx = json.loads(out)
    assert ctx["jira_key"] == ""
    assert ctx["jira_scope"] == ""


def test_github_meta_keys_and_body(tmp_path):
    rc, out, err = run_build("review_meta_github.json", _configured_tracker(tmp_path))
    assert rc == 0, err
    ctx = json.loads(out)
    assert ctx["jira_key"] == "PROJ-5678"
    assert ctx["branch_meta"]["source"] == "feat/PROJ-5678-webhook-retry"
    assert ctx["branch_meta"]["target"] == "main"
    assert ctx["branch_meta"]["head_sha"] == "abc123def456"
    assert "Add retry to webhook" in ctx["ticket_summary"]
    assert "retry on 5xx" in ctx["ticket_summary"]


def _init_repo_with_no_tracker_marker(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / ".claude").mkdir()
    (path / ".claude" / "no-tracker").touch()
    return path


def test_repo_flag_uses_repo_path_not_cwd(tmp_path):
    # cwd (repo_a) has NO .claude/no-tracker marker -- if the script used ambient
    # cwd for the marker walk, jira_key would wrongly resolve to PROJ-1234.
    repo_a = tmp_path / "repo-a"
    subprocess.run(["git", "init", "-q", str(repo_a)], check=True)
    repo_b = _init_repo_with_no_tracker_marker(tmp_path / "repo-b")

    env = dict(os.environ)
    env["DEV_REVIEW_JIRA_BIN"] = f"{sys.executable} {FAKE}"
    result = subprocess.run(
        [sys.executable, str(LIB), str(FIX / "review_meta_gitlab.json"), "--repo", str(repo_b)],
        capture_output=True, text=True, env=env, cwd=str(repo_a),
    )
    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)
    assert ctx["jira_key"] == ""
    assert ctx["jira_scope"] == ""
