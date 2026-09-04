#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_skill_logger.sh — same 5 cases plus one new case
for the plugin-enabled gate, which this port inlines (previously covered only
by hooks/test_plugin_enabled.sh, against a different hook)."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "skill_logger.py"


def run_hook(payload: dict, tmp_path: Path, settings: dict | None = None) -> tuple[str, bool]:
    """Runs the real hook subprocess with HOME=tmp_path. Returns (log content, log exists)."""
    if settings is not None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(json.dumps(settings))
    env = {**os.environ, "HOME": str(tmp_path)}
    subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        env=env,
        check=True,
    )
    log = tmp_path / ".claude" / "state" / "dev-flow-skills.log"
    if log.exists():
        return log.read_text(), True
    return "", False


def test_happy_path(tmp_path, git_repo):
    want_branch = subprocess.run(
        ["git", "-C", str(git_repo), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    out, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
    )
    assert exists
    fields = out.rstrip("\n").split("\t")
    assert len(fields) == 5
    ts, session, repo, branch, skill = fields
    assert session == "sess-42"
    assert repo == git_repo.name
    assert branch == want_branch
    assert skill == "simo-dev:commit"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts)


def test_worktree_yields_main_repo_name(tmp_path, git_repo):
    wt = git_repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(git_repo), "worktree", "add", str(wt), "-b", "side"],
        check=True, capture_output=True,
    )
    out, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(wt), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
    )
    assert exists
    repo = out.rstrip("\n").split("\t")[2]
    assert repo == git_repo.name
    assert repo != "wt"


def test_no_skill_writes_nothing(tmp_path, git_repo):
    # cwd is a real repo so this isolates the empty-skill guard from the
    # non-repo-cwd guard below — a "/" cwd would pass for either reason.
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {}}, tmp_path
    )
    assert not exists


def test_non_repo_cwd_writes_nothing(tmp_path):
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": "/", "tool_input": {"skill": "simo-dev:commit"}}, tmp_path
    )
    assert not exists


def test_detached_head_logs_with_empty_branch(tmp_path):
    repo = tmp_path / "detached-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "--allow-empty", "-m", "init"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", "--detach", "HEAD"], check=True)
    out, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
    )
    assert exists
    fields = out.rstrip("\n").split("\t")
    assert fields[1] == "sess-42"
    assert fields[3] == ""
    assert fields[4] == "simo-dev:commit"


def test_plugin_disabled_writes_nothing(tmp_path, git_repo):
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
        settings={"enabledPlugins": {"simo-dev@simo-local": False}},
    )
    assert not exists


def test_settings_json_without_the_key_still_logs(tmp_path, git_repo):
    # A settings.json that exists and parses, but never mentions this plugin,
    # must fail OPEN (log), same as a missing settings.json — only a literal
    # `false` disables. This is the only case that actually distinguishes
    # `is not False` from a naive `== True` on the enabled check: both read
    # identically when the stored value is a literal False.
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
        settings={"enabledPlugins": {}},
    )
    assert exists


def test_log_write_failure_does_not_crash_the_hook(tmp_path, git_repo):
    # ~/.claude/state exists as a plain FILE instead of a directory, so
    # mkdir(parents=True, exist_ok=True) raises FileExistsError. The hook must
    # still exit 0 (fail open), never propagate an uncaught exception.
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "state").write_text("not a directory")
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(
            {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}}
        ),
        text=True,
        env=env,
    )
    assert result.returncode == 0


def test_invalid_utf8_settings_json_does_not_crash_the_hook(tmp_path, git_repo):
    # settings.json containing a byte sequence that is not valid UTF-8 must
    # not crash _plugin_enabled()'s read — treated as fail-open (still logs),
    # same as a missing or malformed settings.json. Written as raw bytes, not
    # through the `settings=` dict helper, so this doesn't depend on the
    # process's default locale/encoding the way an LC_ALL trick would.
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_bytes(b'{"enabledPlugins": {"x": "\xff\xfe"}}')
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(
            {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}}
        ),
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert (tmp_path / ".claude" / "state" / "dev-flow-skills.log").exists()
