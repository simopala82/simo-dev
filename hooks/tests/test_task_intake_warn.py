#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_task_intake_warn.sh."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "task_intake_warn.py"


def _mkhome(tmp_path, name, log_line=None):
    home = tmp_path / name
    (home / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    if log_line:
        (home / ".claude" / "state" / "dev-flow-skills.log").write_text(log_line + "\n")
    return home


def _warns(home, state, session, file_path):
    payload = {"session_id": session, "tool_input": {"file_path": str(file_path)}}
    env = {**os.environ, "HOME": str(home), "TASK_INTAKE_GATE_HOOK_STATE_FILE": str(state)}
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                            text=True, env=env, capture_output=True)
    return "additionalContext" in result.stdout


def test_task_intake_logged_no_warning(tmp_path):
    home = _mkhome(tmp_path, "h1", "2026-08-25T00:00:00Z\tsess-1\trepo\tbranch\tsimo-dev:task-intake")
    state = tmp_path / "state1" / "task-intake-gate-hook"
    assert not _warns(home, state, "sess-1", "/repo/real.py")


def test_no_task_intake_real_file_warns_then_armed_silent(tmp_path):
    home = _mkhome(tmp_path, "h2", "2026-08-25T00:00:00Z\tsess-2\trepo\tbranch\tsimo-dev:commit")
    state = tmp_path / "state2" / "task-intake-gate-hook"
    assert _warns(home, state, "sess-2", "/repo/real.py")
    assert not _warns(home, state, "sess-2", "/repo/other.py")


def test_exempt_paths_stay_silent_without_consuming_the_arm(tmp_path):
    home = _mkhome(tmp_path, "h3")
    (home / ".claude" / "skills" / "foo").mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state3" / "task-intake-gate-hook"
    for fp in [
        "/repo/README.md", "/repo/notes.txt", "/repo/docs/plans/p.py",
        "/repo/.claude/scheduled-tasks/standup/run.sh", "/repo/.scratch/feature/data.json",
        str(home / ".claude" / "skills" / "foo" / "lib.py"),
    ]:
        assert not _warns(home, state, "sess-3", fp), f"expected exempt: {fp}"
    assert _warns(home, state, "sess-3", "/repo/real.py")


def test_personal_skill_via_symlinked_home_resolved_path_stays_exempt(tmp_path):
    real_home_raw = tmp_path / "real_home_raw"
    (real_home_raw / ".claude" / "skills" / "foo").mkdir(parents=True, exist_ok=True)
    real_home = real_home_raw.resolve()
    symlink_home = tmp_path / "home-symlink"
    symlink_home.symlink_to(real_home)
    state = tmp_path / "state3b" / "task-intake-gate-hook"
    assert not _warns(symlink_home, state, "sess-3b", real_home / ".claude" / "skills" / "foo" / "lib.py")


def test_missing_session_id_never_warns(tmp_path):
    home = _mkhome(tmp_path, "h4")
    state = tmp_path / "state4" / "task-intake-gate-hook"
    payload = {"tool_input": {"file_path": "/repo/real.py"}}
    env = {**os.environ, "HOME": str(home), "TASK_INTAKE_GATE_HOOK_STATE_FILE": str(state)}
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                            text=True, env=env, capture_output=True)
    assert "additionalContext" not in result.stdout


def test_no_skill_log_at_all_warns(tmp_path):
    home = tmp_path / "h5"   # no .claude/state at all -- log file does not exist
    home.mkdir()
    state = tmp_path / "state5" / "task-intake-gate-hook"
    assert _warns(home, state, "sess-5", "/repo/real.py")
