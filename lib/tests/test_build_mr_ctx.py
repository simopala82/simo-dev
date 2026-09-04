"""Tests for lib/build_mr_ctx.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).parent.parent / "build_mr_ctx.py"
JIRA_SCOPE_FETCH = Path(__file__).parent.parent.parent / "private" / "jira_scope_fetch.py"
TRACKER_CONFIG = Path(__file__).parent.parent / "tracker_config.py"
FAKE = Path(__file__).parent / "fixtures" / "fake_jira.py"


def _cache_safe_path(tmp_path, src):
    # copies src into tmp_path so its path never trips tracker_config.py's cache-path guard
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    return dst


def _init_repo(path, branch):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / ".claude").mkdir()
    (path / ".claude" / "no-tracker").touch()
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    return path


def run_build(cwd, args):
    result = subprocess.run(
        [sys.executable, str(LIB), *args], capture_output=True, text=True, cwd=str(cwd),
    )
    return result.returncode, result.stdout, result.stderr


def test_repo_flag_uses_repo_path_not_cwd(tmp_path):
    repo_a = _init_repo(tmp_path / "repo-a", "other-branch")
    repo_b = _init_repo(tmp_path / "repo-b", "feat/target-branch")

    rc, out, err = run_build(repo_a, ["--repo", str(repo_b)])
    assert rc == 0, err
    ctx = json.loads(out)
    head_b = subprocess.check_output(["git", "-C", str(repo_b), "rev-parse", "HEAD"], text=True).strip()
    assert ctx["branch_meta"]["source"] == "feat/target-branch"
    assert ctx["branch_meta"]["head_sha"] == head_b
    assert ctx["branch_meta"]["jira_key"] == ""  # .claude/no-tracker marker in repo_b, not repo_a


def test_without_repo_flag_falls_back_to_cwd():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _init_repo(Path(tmp) / "repo", "feat/cwd-branch")
        rc, out, err = run_build(repo, [])
        assert rc == 0, err
        ctx = json.loads(out)
        assert ctx["branch_meta"]["source"] == "feat/cwd-branch"


def _init_repo_with_key(path, branch):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    return path


def test_ticket_summary_fetched_via_jira_scope_light_mode(tmp_path):
    repo = _init_repo_with_key(tmp_path / "repo", "feat/proj-1234-thing")
    tracker_cfg = tmp_path / "tracker.json"
    subprocess.run(
        [sys.executable, str(TRACKER_CONFIG), "set", "PROJ", "scope_fetch_cmd",
         f"{sys.executable} {_cache_safe_path(tmp_path, JIRA_SCOPE_FETCH)}", "--config", str(tracker_cfg)],
        check=True,
    )
    env = dict(os.environ)
    env["DEV_REVIEW_JIRA_BIN"] = f"{sys.executable} {FAKE}"
    env["SIMO_DEV_TRACKER_CONFIG"] = str(tracker_cfg)
    result = subprocess.run(
        [sys.executable, str(LIB), "--repo", str(repo)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)
    assert ctx["branch_meta"]["jira_key"] == "PROJ-1234"
    assert "PROJ-1234: Canned summary" in ctx["ticket_summary"]
    assert "comments_shown=0" in ctx["ticket_summary"]  # light mode -> comments forced to 0
