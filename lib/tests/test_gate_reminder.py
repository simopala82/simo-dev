#!/usr/bin/env python3
"""Tests for lib/gate_reminder.py."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import gate_reminder as gr  # noqa: E402

ARMED = datetime(2026, 11, 13, 18, 33, tzinfo=timezone.utc)
ROOT = LIB.parent


def at(minutes):
    return ARMED + timedelta(minutes=minutes)


def iso(minutes):
    return at(minutes).isoformat().replace("+00:00", "Z")


def call(mid, minutes, read=300_000, write=0, one_hour=None, model="claude-opus-5-5", inp=10, **extra):
    usage = {"input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": write}
    if one_hour is not None:
        usage["cache_creation"] = {"ephemeral_1h_input_tokens": write if one_hour else 0}
    return {"type": "assistant", "timestamp": iso(minutes), "message": {"id": mid, "model": model, "usage": usage}, **extra}


def user(minutes, kind="human", content="y", **extra):
    rec = {"type": "user", "timestamp": iso(minutes), "message": {"content": content}, **extra}
    if kind:
        rec["origin"] = {"kind": kind}
    return rec


def transcript(tmp_path, records):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\nnot json\n")
    return path


def test_scan_takes_the_first_record_of_the_last_call(tmp_path):
    path = transcript(tmp_path, [call("m1", -5), call("m2", 1, read=250_000, write=2_000), call("m2", 3, read=250_000, write=2_000)])
    snap = gr.scan(path, ARMED)
    assert snap["last_call"] == at(1)
    assert snap["prompt"] == 252_010
    assert snap["model"] == "claude-opus-5-5"
    assert snap["answered"] is False and snap["one_hour"] is True


def test_scan_skips_a_synthetic_record_with_no_usage(tmp_path):
    path = transcript(tmp_path, [call("m1", 1), call("e1", 9, read=0, inp=0, model="<synthetic>")])
    snap = gr.scan(path, ARMED)
    assert snap["last_call"] == at(1) and snap["prompt"] == 300_010 and snap["model"] == "claude-opus-5-5"


def test_scan_ignores_subagent_records(tmp_path):
    path = transcript(tmp_path, [call("m1", 1), call("s1", 30, isSidechain=True), user(31, isSidechain=True)])
    snap = gr.scan(path, ARMED)
    assert snap["last_call"] == at(1) and snap["answered"] is False


@pytest.mark.parametrize("record, answered", [
    (user(10), True),
    (user(-1), False),
    (user(10, kind="task-notification", content="<task-notification>"), False),
    (user(10, kind="peer", content="Another Claude session sent a message"), False),
    (user(10, kind=None, content=[{"type": "tool_result", "content": "ok"}]), False),
    (user(10, kind=None, content="This session is being continued from a previous conversation"), False),
])
def test_scan_counts_only_a_human_message_after_arm_as_an_answer(tmp_path, record, answered):
    assert gr.scan(transcript(tmp_path, [call("m1", 1), record]), ARMED)["answered"] is answered


def test_scan_reads_the_cache_lifetime_from_the_latest_write(tmp_path):
    hour = [call("m1", 0, write=5_000, one_hour=True), call("m2", 1)]
    five = [call("m1", 0, write=5_000, one_hour=True), call("m2", 1, write=5_000, one_hour=False), call("m3", 2)]
    assert gr.scan(transcript(tmp_path, hour), ARMED)["one_hour"] is True
    assert gr.scan(transcript(tmp_path, five), ARMED)["one_hour"] is False
    mixed = call("m1", 0, write=5_000, one_hour=True)
    mixed["message"]["usage"]["cache_creation"]["ephemeral_5m_input_tokens"] = 300
    assert gr.scan(transcript(tmp_path, [mixed]), ARMED)["one_hour"] is False


def test_scan_of_a_missing_transcript_reports_nothing(tmp_path):
    snap = gr.scan(tmp_path / "gone.jsonl", ARMED)
    assert snap["last_call"] is None and snap["answered"] is False


def snap(**over):
    return {"last_call": at(2), "prompt": 300_000, "model": "claude-opus-5-5", "one_hour": True, "answered": False, **over}


@pytest.mark.parametrize("now, over, expected", [
    (at(10), {}, ("sleep", 37 * 60.0)),
    (at(47), {}, ("notify", at(62))),
    (at(61), {}, ("notify", at(62))),
    (at(62), {}, ("exit", "expired")),
    (at(47), {"answered": True}, ("exit", "answered")),
    (at(47), {"one_hour": False}, ("exit", "no 1h cache")),
    (at(47), {"last_call": at(30)}, ("sleep", 28 * 60.0)),
    (at(50), {"last_call": None}, ("notify", at(60))),
    (at(181), {"last_call": at(170)}, ("exit", "stale")),
])
def test_decide(now, over, expected):
    assert gr.decide(now, ARMED, snap(**over)) == expected


def test_rewrite_cost_uses_the_1h_write_rate_of_the_model():
    assert gr.rewrite_cost(snap()) == pytest.approx(2.40)
    assert gr.rewrite_cost(snap(model="claude-fable-5-2[1m]")) == pytest.approx(4.80)
    assert gr.rewrite_cost(snap(model="unknown-model")) == pytest.approx(2.40)
    assert gr.rewrite_cost(snap(model="claude-haiku-5-5")) == pytest.approx(0.30)  # over 100k: the long-prompt rate


def test_watch_sleeps_until_due_then_notifies_once(tmp_path, monkeypatch):
    path = transcript(tmp_path, [call("m1", 2)])
    clock = [at(3)]
    slept, ran = [], []
    monkeypatch.setattr(gr, "notifier", lambda title, body: ["notify", title, body])

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    result = gr.watch(str(path), ARMED, "UP-1 plan gate", now=lambda: clock[0], sleep=sleep, run=lambda cmd, **kw: ran.append(cmd))
    assert result == "notified"
    assert slept == [44 * 60.0]
    assert len(ran) == 1 and ran[0][1] == "simo-dev"
    assert ran[0][2].startswith("UP-1 plan gate: still waiting. Answer by ") and "$2.40" in ran[0][2]


def test_watch_follows_a_cache_refresh_and_stops_on_the_answer(tmp_path):
    path = transcript(tmp_path, [call("m1", 2)])
    clock = [at(3)]
    ran = []

    def sleep(seconds):
        clock[0] += timedelta(seconds=seconds)
        if len(path.read_text().splitlines()) == 2:  # first wake-up: a notification turn refreshed the cache
            transcript(tmp_path, [call("m1", 2), user(40, kind="task-notification"), call("m2", 40)])
        else:  # second wake-up: the user answered
            transcript(tmp_path, [call("m1", 2), call("m2", 40), user(70), call("m3", 70)])

    assert gr.watch(str(path), ARMED, "x", now=lambda: clock[0], sleep=sleep, run=lambda cmd, **kw: ran.append(cmd)) == "answered"
    assert clock[0] == at(85) and ran == []


def _session(tmp_path, monkeypatch, sid="sid1"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SIMO_DEV_GATE_REMINDER", raising=False)
    proj = tmp_path / ".claude" / "projects" / "-slug"
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text(json.dumps(call("m1", 0)) + "\n")
    return proj / f"{sid}.jsonl"


def test_arm_starts_a_detached_watcher_and_prints_the_deadline(tmp_path, monkeypatch):
    path = _session(tmp_path, monkeypatch)
    monkeypatch.setattr(gr, "notifier", lambda title, body: ["notify"])
    spawned = []
    line = gr.arm("sid1", "UP-1 plan gate", spawn=lambda cmd, **kw: spawned.append((cmd, kw)))
    assert line.startswith("Gate reminder set: answer by ~")
    (cmd, kw), = spawned
    assert cmd[1:5] == [str(LIB / "gate_reminder.py"), "watch", "--transcript", str(path)]
    assert cmd[-2:] == ["--label", "UP-1 plan gate"]
    assert gr.transcript_usage.parse_ts(cmd[cmd.index("--armed") + 1]) is not None
    assert kw["start_new_session"] is True
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == gr.subprocess.DEVNULL


@pytest.mark.parametrize("case", ["off", "no session", "no transcript", "no notifier", "5m cache"])
def test_arm_is_silent_when_no_watcher_can_run(tmp_path, monkeypatch, case):
    path = _session(tmp_path, monkeypatch)
    monkeypatch.setattr(gr, "notifier", lambda title, body: None if case == "no notifier" else ["notify"])
    if case == "5m cache":
        path.write_text(json.dumps(call("m1", 0, write=5_000, one_hour=False)) + "\n")
    if case == "off":
        monkeypatch.setenv("SIMO_DEV_GATE_REMINDER", "off")
    session = {"no session": "", "no transcript": "other"}.get(case, "sid1")
    spawned = []
    assert gr.arm(session, "x", spawn=lambda cmd, **kw: spawned.append(cmd)) == ""
    assert spawned == []


def test_main_arm_prints_nothing_without_a_session(capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert gr.main(["arm", "--label", "x"]) == 0
    assert capsys.readouterr().out == ""


def test_notifier_passes_the_text_as_arguments(monkeypatch):
    monkeypatch.setattr(gr.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    cmd = gr.notifier("simo-dev", 'a "quoted" label')
    assert cmd[0] == "osascript" and cmd[-2:] == ['a "quoted" label', "simo-dev"]
    monkeypatch.setattr(gr.shutil, "which", lambda name: "/usr/bin/notify-send" if name == "notify-send" else None)
    assert gr.notifier("simo-dev", "body") == ["notify-send", "--", "simo-dev", "body"]
    monkeypatch.setattr(gr.shutil, "which", lambda name: None)
    assert gr.notifier("simo-dev", "body") is None


def test_the_plan_gate_arms_the_reminder_before_it_is_shown():
    text = (ROOT / "skills/task-intake/SKILL.md").read_text()
    step = text[text.index("### 7.5."):text.index("### 8. Hand-off")]
    call_site = '/lib/gate_reminder.py" arm --label'
    assert step.count(call_site) == 1
    assert step.index("**Pre-gate eligibility check") < step.index(call_site) < step.index("\nThen show:")
    assert "Skipped under `simo-dev:afk`" in step[step.index("**Gate reminder"):step.index("\nThen show:")]
