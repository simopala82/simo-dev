#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Tests for hooks/flow_session_start.py."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "flow_session_start.py"


def _run(cwd):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"session_id": "s1", "cwd": str(cwd), "source": "resume"}),
        text=True, capture_output=True,
        env={**os.environ, "SIMO_DEV_FLOW_ACTIVE_DIR": str(Path(str(cwd)).parent / "no-active")},
    )


def test_marked_worktree_gets_one_line(git_repo):
    (git_repo / ".git" / "simo-dev-flow-active").write_text("1\n")
    result = _run(git_repo)
    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart"
    assert ctx["additionalContext"].startswith("simo-dev flow active in this worktree")
    assert "\n" not in ctx["additionalContext"]


def test_session_flag_alone_gets_the_line(git_repo, tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    (active / "s1").write_text("1\n")
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"session_id": "s1", "cwd": str(git_repo), "source": "resume"}),
        text=True, capture_output=True, env={**os.environ, "SIMO_DEV_FLOW_ACTIVE_DIR": str(active)},
    )
    assert "simo-dev flow active" in result.stdout


def test_unmarked_repo_prints_nothing(git_repo):
    result = _run(git_repo)
    assert result.returncode == 0 and result.stdout == ""


def test_non_repo_and_bad_payload_print_nothing(tmp_path):
    assert _run(tmp_path).stdout == ""
    bad = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True, capture_output=True)
    assert bad.returncode == 0 and bad.stdout == ""
