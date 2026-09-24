"""Tests for lib/build_mr_ctx.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).parent.parent / "build_mr_ctx.py"
FAKE_SCOPE_CMD = Path(__file__).parent / "fixtures" / "fake_scope_cmd.py"
TRACKER_CONFIG = Path(__file__).parent.parent / "tracker_config.py"


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
         f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}", "--config", str(tracker_cfg)],
        check=True,
    )
    env = dict(os.environ)
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


def _init_repo_with_base_and_feature(path):
    """Bare 'origin' + a work checkout: one base commit on main (pushed, HEAD set),
    then a feature branch adding one file -- so build_mr_ctx.py can resolve a real
    base/target and diff against it (existing helpers in this file have no remote,
    so target/base always resolve empty and neither commit_log/file_stat/changed_files
    is exercised against a real diff anywhere in this suite)."""
    origin = path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    (work / ".claude").mkdir()
    (work / ".claude" / "no-tracker").touch()
    (work / "base.txt").write_text("base")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(work), "symbolic-ref",
                    "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], check=True)
    subprocess.run(["git", "-C", str(work), "switch", "-q", "-c", "feat/thing"], check=True)
    (work / "new_file.py").write_text("print('x')\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "add new_file"], check=True)
    return work


def test_changed_files_lists_full_untruncated_paths(tmp_path):
    repo = _init_repo_with_base_and_feature(tmp_path)
    rc, out, err = run_build(repo, [])
    assert rc == 0, err
    ctx = json.loads(out)
    assert ctx["changed_files"].strip() == "new_file.py"


def test_changed_files_preserves_paths_file_stat_truncates(tmp_path):
    """This is the feature's actual reason to exist (see the plan's Architecture note):
    `git diff --stat` abbreviates a long/deeply-nested path with '...', `--name-only` does not."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    (work / ".claude").mkdir()
    (work / ".claude" / "no-tracker").touch()
    (work / "base.txt").write_text("base")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(work), "symbolic-ref",
                    "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], check=True)
    subprocess.run(["git", "-C", str(work), "switch", "-q", "-c", "feat/thing"], check=True)
    deep_dir = work / "a/very/deeply/nested/directory/structure/that/is/quite/long/to/trigger/truncation/in/diff/stat/output"
    deep_dir.mkdir(parents=True)
    long_path = "a/very/deeply/nested/directory/structure/that/is/quite/long/to/trigger/truncation/in/diff/stat/output/module.py"
    (deep_dir / "module.py").write_text("print('x')\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "add deep file"], check=True)
    rc, out, err = run_build(work, [])
    assert rc == 0, err
    ctx = json.loads(out)
    assert long_path in ctx["changed_files"]
    assert long_path not in ctx["file_stat"]  # confirms file_stat really does truncate this path
    assert "..." in ctx["file_stat"]
