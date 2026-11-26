#!/usr/bin/env python3
"""Tests for lib/merge_brief.py."""
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import flow_telemetry as ft  # noqa: E402
import merge_brief as mb  # noqa: E402

SCRIPT = LIB / "merge_brief.py"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(tmp_path / "dev-flow"))
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(tmp_path / "active"))
    monkeypatch.delenv("DEV_FLOW_NO_TRACKER", raising=False)
    monkeypatch.delenv("DEV_FLOW_JIRA_PROJECTS", raising=False)


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", "-b", "master", str(main)], check=True)
    subprocess.run([*git, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    wt = main / ".worktrees" / "proj-12"
    subprocess.run([*git, "-C", str(main), "worktree", "add", "-q", "-b", "feat/proj-12-thing", str(wt)], check=True)
    return main, wt


def args(wt, **extra):
    base = {"repo": str(wt), "mr": "https://git.example.com/team/app/-/merge_requests/7", "branch": "feat/proj-12-thing",
            "base": "master", "head": "abc1234", "qa_gate": "", "session": "parked-session-1"}
    return {**base, **extra}


def test_brief_carries_every_value(dirs, repo):
    main, wt = repo
    tid = ft.start("parked-session-1", repo=str(wt))
    text = mb.brief(**args(wt))
    lines = dict(line.split(": ", 1) for line in text.splitlines()[1:])
    assert lines == {
        "task id": tid, "mr": "https://git.example.com/team/app/-/merge_requests/7", "branch": "feat/proj-12-thing",
        "base": "master", "head": "abc1234", "worktree": str(wt), "main checkout": str(main.resolve()),
        "ticket": "PROJ-12", "qa gate": "not run", "parked session": "parked-session-1",
    }


def test_task_id_line_is_read_back_by_adopt(dirs, repo, tmp_path):
    _, wt = repo
    tid = ft.start("parked-session-1", repo=str(wt))
    prompt = "[SPAWNED TASK — marker]\n\n" + mb.brief(**args(wt)) + "\n\nRun `simo-dev:mr-followup merge`."
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"user","timestamp":"2026-11-15T10:00:00Z","message":{"content":%s}}\n'
                          % __import__("json").dumps(prompt), encoding="utf-8")
    assert ft.prompt_ids(str(transcript)) == (tid, "")
    assert ft.adopt("fresh-session-2", "simo-dev:mr-followup", str(transcript)) == tid
    assert ft.current_task("fresh-session-2", "") == tid


def test_no_task_no_key_and_qa_gate(dirs, repo, monkeypatch):
    _, wt = repo
    monkeypatch.setenv("DEV_FLOW_NO_TRACKER", "1")
    text = mb.brief(**args(wt, qa_gate="2026-11-15", session=""))
    assert "task id:" not in text
    assert "ticket: none" in text
    assert "qa gate: passed 2026-11-15" in text
    assert "parked session: unknown" in text


def test_cli_refuses_a_path_that_is_not_a_checkout(dirs, tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--mr", "u", "--branch", "b",
                          "--base", "master", "--head", "h"], capture_output=True, text=True)
    assert out.returncode == 2
    assert "not a git checkout" in out.stderr


def test_cli_prints_the_brief(dirs, repo):
    _, wt = repo
    out = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(wt), "--mr", "u", "--branch", "feat/proj-12-thing",
                          "--base", "master", "--head", "h", "--session", "s"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith(mb.HEADER + "\n")


def test_task_state_open_then_closed(dirs):
    tid = ft.start("parked-session-1", task_id="20261115T1000-parked00")
    assert mb.task_state(tid) == "open"
    ft.record("outcome", "fresh-session-2", "", {"result": "merged"}, task=tid)
    assert mb.task_state(tid) == "closed"


def test_task_state_refuses_a_bad_id(dirs):
    out = subprocess.run([sys.executable, str(SCRIPT), "task-state", "--task", "../x"], capture_output=True, text=True)
    assert out.returncode == 2 and "not a task id" in out.stderr
