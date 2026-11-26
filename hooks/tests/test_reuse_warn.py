#!/usr/bin/env python3
"""Tests for hooks/reuse_warn.py."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS.parent / "lib"))
import flow_telemetry as ft  # noqa: E402

HOOK = HOOKS / "reuse_warn.py"


def iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def transcript(tmp_path, minutes_ago, prompt=400_000):
    path = tmp_path / "t.jsonl"
    usage = {"input_tokens": 10, "cache_read_input_tokens": prompt, "cache_creation_input_tokens": 0}
    records = [
        {"type": "user", "timestamp": iso(minutes_ago + 1), "message": {"content": "hi"}},
        {"type": "assistant", "timestamp": iso(minutes_ago), "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}},
        {"type": "assistant", "timestamp": iso(1), "isSidechain": True,
         "message": {"id": "m2", "model": "claude-opus-5-5", "usage": usage}},
        {"type": "user", "timestamp": iso(0), "message": {"content": "new work"}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    tel, act = tmp_path / "dev-flow", tmp_path / "active"
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(tel))
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(act))
    return {**os.environ, "SIMO_DEV_TELEMETRY_DIR": str(tel), "SIMO_DEV_FLOW_ACTIVE_DIR": str(act)}


def run(env, session, path):
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({
        "session_id": session, "transcript_path": str(path), "cwd": "/tmp", "hook_event_name": "UserPromptSubmit",
        "prompt": "new work"}), text=True, capture_output=True, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else None


def finished_task(session):
    tid = ft.start(session)
    ft.record("outcome", session, "", {"result": "merged"}, task=tid)
    return tid


def test_warns_once_on_an_idle_session_whose_task_ended(env, tmp_path):
    finished_task("s1")
    path = transcript(tmp_path, 90)
    got = run(env, "s1", path)
    assert "new session" in got["systemMessage"]
    assert "400k" in got["systemMessage"]
    assert got["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert run(env, "s1", path) is None


def test_a_released_task_counts_as_ended(env, tmp_path):
    ft.start("s1")
    ft.main(["close", "--session", "s1"])
    assert run(env, "s1", transcript(tmp_path, 90)) is not None


def test_silent_within_the_hour(env, tmp_path):
    finished_task("s1")
    assert run(env, "s1", transcript(tmp_path, 50)) is None


def test_silent_while_the_session_holds_an_open_task(env, tmp_path):
    finished_task("s1")
    ft.start("s1", task_id="20261115T0000-s1")
    assert run(env, "s1", transcript(tmp_path, 90)) is None


def test_silent_for_a_session_outside_the_flow(env, tmp_path):
    finished_task("other")
    assert run(env, "s1", transcript(tmp_path, 90)) is None


def test_never_fails_on_bad_input(env, tmp_path):
    out = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True, capture_output=True, env=env)
    assert out.returncode == 0 and not out.stdout
    assert run(env, "s1", tmp_path / "missing.jsonl") is None


def test_the_marker_is_pruned_with_the_adopted_ids(env, tmp_path):
    marker = Path(env["SIMO_DEV_TELEMETRY_DIR"]) / "reuse-warned" / "s1"
    marker.parent.mkdir(parents=True)
    marker.write_text("")
    old = marker.stat().st_mtime - ft.PRUNE_AFTER_S - 10
    os.utime(marker, (old, old))
    ft.prune(Path(env["SIMO_DEV_TELEMETRY_DIR"]))
    assert not marker.exists()
