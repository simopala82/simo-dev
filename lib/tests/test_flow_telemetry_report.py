#!/usr/bin/env python3
"""Tests for flow_telemetry attribute and render."""
import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import flow_telemetry as ft  # noqa: E402


def _rec(mid, ts, skill="", retry=None, model="", **usage):
    rec = {"type": "assistant", "timestamp": ts, "message": {"id": mid, "usage": usage}}
    if model:
        rec["message"]["model"] = model
    if skill:
        rec["attributionSkill"] = skill
    if retry is not None:
        rec["retryAttempt"] = retry
    return json.dumps(rec)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(tmp_path / "dev-flow"))
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(tmp_path / "active"))
    monkeypatch.setenv("SIMO_DEV_JOURNAL", str(tmp_path / "journal.md"))
    return tmp_path


def _events(task, rows):
    path = ft.task_file(task)
    for ts, etype, fields in rows:
        ft.append_event(path, {"ts": ts, "type": etype, "task": task, "session": "sid1", **fields})
    return path


def _transcripts(home):
    proj = home / ".claude" / "projects" / "-x"
    (proj / "sid1" / "subagents").mkdir(parents=True)
    (proj / "sid1.jsonl").write_text("\n".join([
        _rec("before", "2026-11-09T09:00:00Z", output_tokens=100),
        _rec("m1", "2026-11-09T10:01:00Z", skill="simo-dev:task-intake", model="claude-sonnet-5-5", output_tokens=2),
        _rec("m1", "2026-11-09T10:01:00Z", skill="simo-dev:task-intake", model="claude-sonnet-5-5", output_tokens=5, input_tokens=1),
        _rec("m2", "2026-11-09T10:30:00Z", skill="simo-dev:commit", retry=1, model="claude-sonnet-5-5", cache_read_input_tokens=7),
        _rec("after", "2026-11-09T12:00:00Z", output_tokens=100),
    ]) + "\n")
    (proj / "sid1" / "subagents" / "agent-a.jsonl").write_text(
        _rec("s1", "2026-11-09T10:10:00Z", model="claude-haiku-5-5", output_tokens=3) + "\n")


def test_attribute_dedupes_windows_and_splits_by_step(env):
    _transcripts(env)
    _events("T1", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
                   ("2026-11-09T11:00:00Z", "outcome", {"result": "merged"})])
    got = ft.attribute(task_id="T1")
    assert got["total"] == {"input_tokens": 1, "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 7, "output_tokens": 8}
    assert [(r["skill"], r["tokens"]["output_tokens"]) for r in got["by_step"]] == [("simo-dev:task-intake", 8)]
    assert got["by_step"][0]["agents"] == {"agent": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                                     "cache_read_input_tokens": 0, "output_tokens": 3}}
    sonnet = {"input_tokens": 1 * 2.0, "cache_creation_input_tokens": 0.0,
              "cache_read_input_tokens": 7 * 0.10, "output_tokens": 5 * 10.0}
    haiku_out = 3 * 0.50
    assert ft.tu.dollars(got["cost"]) == pytest.approx((sum(sonnet.values()) + haiku_out) / 1e6)
    assert got["by_step"][0]["agent_costs"]["agent"]["output_tokens"] == pytest.approx(haiku_out / 1e6)
    assert got["unpriced"] == []
    assert "by_skill" not in got
    assert got["api_retries"] == 1 and got["sessions"] == 1
    assert "2 transcripts" in got["note"]


def test_attribute_cli_appends_an_attribution_event(env, capsys):
    _transcripts(env)
    path = _events("T1", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
                          ("2026-11-09T11:00:00Z", "outcome", {"result": "merged"})])
    assert ft.main(["attribute", "--task", "T1", "--session", "sid1"]) == 0
    assert capsys.readouterr().out.startswith("attribution: total=16 cost=$0.0001 (")
    last = ft.read_events(path)[-1]
    assert last["type"] == "attribution" and last["total"]["output_tokens"] == 8
    assert last["unpriced"] == [] and ft.tu.dollars(last["cost"]) == pytest.approx(54.2e-6)


def test_attribute_lists_an_unknown_model_in_the_note(env):
    proj = env / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    (proj / "sid1.jsonl").write_text(_rec("u1", "2026-11-09T10:01:00Z", model="future-model", output_tokens=1_000_000) + "\n")
    _events("T1", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
                   ("2026-11-09T11:00:00Z", "outcome", {"result": "merged"})])
    got = ft.attribute(task_id="T1")
    assert got["unpriced"] == ["future-model"]
    assert got["note"].endswith(", 1 model not priced exactly")
    assert ft.tu.dollars(got["cost"]) == pytest.approx(20.0)


def test_attribute_cli_survives_many_unknown_models(env):
    proj = env / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    (proj / "sid1.jsonl").write_text("\n".join(
        _rec(f"u{i}", "2026-11-09T10:01:00Z", model=f"some-long-future-model-name-number-{i}", output_tokens=1)
        for i in range(5)) + "\n")
    _events("T1", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
                   ("2026-11-09T11:00:00Z", "outcome", {"result": "merged"})])
    assert ft.main(["attribute", "--task", "T1", "--session", "sid1"]) == 0
    assert len(ft.read_events(ft.task_file("T1"))[-1]["unpriced"]) == 5


def test_attribute_unknown_task_is_refused(env):
    assert ft.main(["attribute", "--task", "NOPE"]) == 2


def test_render_builds_the_journal_and_archives_the_old_one_once(env):
    journal = env / "journal.md"
    journal.write_text("## old hand-written entry\n")
    _events("T2", [("2026-11-09T12:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
                   ("2026-11-09T12:30:00Z", "attribution", {
                       "total": {"input_tokens": 4, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0},
                       "cost": {"input_tokens": 1.5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0.25},
                       "sessions": 1, "api_retries": 0, "note": "1 transcripts"})])
    _events("T1", [
        ("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
        ("2026-11-09T10:05:00Z", "task_bound", {"ticket": "", "branch": "feat/x", "repo": "simo-scripts"}),
        ("2026-11-09T10:06:00Z", "gate", {"gate": "plan", "answer": "proceed"}),
        ("2026-11-09T10:20:00Z", "step_start", {"skill": "simo-dev:review-loop"}),
        ("2026-11-09T10:25:00Z", "retry", {"skill": "simo-dev:review-loop", "iteration": 2}),
        ("2026-11-09T11:20:00Z", "outcome", {"result": "merged", "mr": "https://x/-/merge_requests/1",
                                            "task": "feat, small", "friction": "none", "notes": "ok"}),
        ("2026-11-09T11:21:00Z", "attribution", {"total": {"input_tokens": 1, "cache_creation_input_tokens": 2,
                                                           "cache_read_input_tokens": 3, "output_tokens": 4},
                                                "by_skill": {}, "sessions": 1, "api_retries": 0, "note": "2 transcripts"}),
    ])
    ft.append_event(ft.session_file("sid1"), {"ts": "2026-11-09T09:00:00Z", "type": "step_start",
                                              "task": "", "session": "sid1", "skill": "simo-dev:commit"})
    assert ft.render() == 2
    text = journal.read_text()
    assert text.startswith(ft.GENERATED)
    assert text.index("task T1") < text.index("task T2")
    assert "## 2026-11-09 · simo-scripts · feat/x · task T1" in text
    assert "- steps: simo-dev:task-intake 20m, simo-dev:review-loop 1h00m" in text
    assert "- gates: plan=proceed" in text
    assert "- retries: simo-dev:review-loop 1" in text
    assert ("- tokens: 10, ≈$0.0001 est. (no model data, priced as claude-opus-5-5) "
            "(in=1 cache_write=2 cache_read=3 out=4; 2 transcripts)") in text
    assert "- tokens: 4, $1.75 est. (in=4 " in text
    assert "- result: merged (https://x/-/merge_requests/1)" in text
    assert "- result: in progress" in text
    archive = env / "dev-flow-journal-archive.md"
    assert archive.read_text() == "## old hand-written entry\n"
    ft.render()
    assert archive.read_text() == "## old hand-written entry\n"


def test_attribute_dedupes_across_files_and_keeps_an_open_window(env):
    proj = env / ".claude" / "projects" / "-x"
    (proj / "sid1" / "subagents").mkdir(parents=True)
    (proj / "sid1.jsonl").write_text(_rec("dup", "2026-11-09T10:01:00Z", output_tokens=2) + "\n")
    (proj / "sid1" / "subagents" / "agent-a.jsonl").write_text(
        _rec("dup", "2026-11-09T10:01:00Z", output_tokens=2) + "\n"
        + _rec("late", "2026-11-09T23:00:00", output_tokens=5) + "\n")
    _events("T3", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"})])
    got = ft.attribute(task_id="T3")
    assert got["total"]["output_tokens"] == 7


def test_attribute_notes_a_session_without_transcript(env):
    _events("T4", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"})])
    assert "1 sessions without transcript" in ft.attribute(task_id="T4")["note"]


def test_a_step_after_the_outcome_never_gets_a_negative_duration(env):
    events = [
        {"ts": "2026-11-09T10:00:00Z", "type": "step_start", "session": "s1", "skill": "simo-dev:task-intake"},
        {"ts": "2026-11-09T11:00:00Z", "type": "outcome", "session": "s1", "result": "merged"},
        {"ts": "2026-11-09T11:40:00Z", "type": "step_start", "session": "s1", "skill": "simo-dev:commit"},
    ]
    text = ft.render_task("T5", events)
    assert "- steps: simo-dev:task-intake 1h40m, simo-dev:commit 0m" in text


def test_the_journal_steps_follow_the_step_model():
    events = [
        {"ts": "2026-11-09T10:00:00Z", "type": "step_start", "session": "s1", "skill": "simo-dev:plan-execute"},
        {"ts": "2026-11-09T10:10:00Z", "type": "step_start", "session": "s1", "skill": "example:helper"},
        {"ts": "2026-11-09T10:30:00Z", "type": "step_start", "session": "s1", "skill": "simo-dev:commit"},
        {"ts": "2026-11-09T10:31:00Z", "type": "step_start", "session": "s1", "skill": "simo-dev:review-loop"},
        {"ts": "2026-11-09T10:35:00Z", "type": "gate", "session": "s1", "skill": "simo-dev:review-loop",
         "gate": "review_exit", "answer": "clean"},
        {"ts": "2026-11-09T10:40:00Z", "type": "outcome", "session": "s1", "result": "merged"},
    ]
    assert ("- steps: simo-dev:plan-execute 30m, simo-dev:commit 1m, simo-dev:review-loop 4m, simo-dev:commit 5m"
            in ft.render_task("T6", events))


def test_a_handed_off_session_stops_counting_at_its_last_event(env):
    proj = env / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    (proj / "orch.jsonl").write_text("\n".join([
        _rec("o1", "2026-11-09T10:05:00Z", output_tokens=1),
        _rec("o2", "2026-11-09T10:45:00Z", output_tokens=100),
    ]) + "\n")
    (proj / "child.jsonl").write_text(_rec("c1", "2026-11-09T10:40:00Z", output_tokens=2) + "\n")
    path = ft.task_file("T6")
    for ts, sid, etype, fields in [
        ("2026-11-09T10:00:00Z", "orch", "step_start", {"skill": "simo-dev:task-intake"}),
        ("2026-11-09T10:10:00Z", "orch", "gate", {"gate": "plan", "answer": "proceed-in-a-new-session"}),
        ("2026-11-09T10:30:00Z", "child", "task_bound", {"branch": "feat/x"}),
        ("2026-11-09T11:00:00Z", "child", "outcome", {"result": "merged"}),
    ]:
        ft.append_event(path, {"ts": ts, "type": etype, "task": "T6", "session": sid, **fields})
    got = ft.attribute(task_id="T6")
    assert got["total"]["output_tokens"] == 3 and got["sessions"] == 2


def test_an_event_after_the_outcome_never_widens_the_window(env):
    proj = env / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    (proj / "sid1.jsonl").write_text(_rec("after", "2026-11-09T11:30:00Z", output_tokens=50) + "\n")
    _events("T7", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
                   ("2026-11-09T11:00:00Z", "outcome", {"result": "merged"}),
                   ("2026-11-09T12:00:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    assert ft.attribute(task_id="T7")["total"]["output_tokens"] == 0


def test_render_leaves_the_handoff_gate_out_of_the_journal(env):
    text = ft.render_task("T8", [
        {"ts": "2026-11-09T10:00:00Z", "type": "gate", "gate": "plan", "answer": "proceed"},
        {"ts": "2026-11-09T10:05:00Z", "type": "gate", "gate": "handoff", "answer": "released"},
    ])
    assert "- gates: plan=proceed\n" in text


def _sum(rows):
    out = dict.fromkeys(("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"), 0)
    for row in rows:
        for key in out:
            out[key] += row["tokens"][key]
    return out


def _marks(events):
    return {sid: [(t.strftime("%H:%M"), s) for t, s in rows] for sid, rows in ft.step_marks(events).items()}


def _ev(ts, etype, skill, session="s1", **fields):
    return {"ts": f"2026-11-09T{ts}:00Z", "type": etype, "session": session, "skill": skill, **fields}


def test_step_marks_open_on_a_flow_step_and_on_a_retry_of_another_one():
    events = [
        _ev("10:00", "step_start", "simo-dev:mr-followup"),
        _ev("10:05", "step_start", "simo-dev:review-loop"),
        _ev("10:06", "retry", "simo-dev:review-loop", iteration=2),
        _ev("10:10", "retry", "simo-dev:mr-followup", iteration=2),
        _ev("10:12", "gate", "simo-dev:commit", gate="g", answer="a"),
        _ev("10:20", "step_start", "simo-dev:commit", session="s2"),
        {"ts": 5, "type": "step_start", "session": "s1", "skill": "simo-dev:commit"},
        {"ts": "2026-11-09T10:30:00Z", "type": "step_start", "skill": "simo-dev:commit"},
    ]
    assert _marks(events) == {
        "s1": [("10:00", "simo-dev:mr-followup"), ("10:05", "simo-dev:review-loop"), ("10:10", "simo-dev:mr-followup")],
        "s2": [("10:20", "simo-dev:commit")]}


def test_a_skill_that_is_not_a_flow_step_opens_no_step_once_the_session_has_one():
    events = [
        _ev("10:00", "step_start", "simo-dev:plan-execute"),
        _ev("10:05", "step_start", "example:local-proxy"),
        _ev("10:30", "step_start", "simo-dev:review-loop"),
        _ev("10:31", "step_start", "simo-dev:code-review-protocol"),
        _ev("10:32", "retry", "simo-dev:code-review-protocol", iteration=2),
        _ev("10:40", "retry", "simo-dev:review-loop", iteration=2),
        _ev("10:45", "step_start", "simo-dev:mr-review", session="s2"),   # no step yet in s2: nothing to be a helper of
        _ev("10:50", "step_start", "simo-dev:debug", session="s2"),
        _ev("10:55", "retry", "example:helper", session="s3", iteration=2),
    ]
    assert _marks(events) == {"s1": [("10:00", "simo-dev:plan-execute"), ("10:30", "simo-dev:review-loop")],
                              "s2": [("10:45", "simo-dev:mr-review")], "s3": []}
    rows = ft.split_by_step(ft.step_marks(events), [
        {"sid": "s1", "ts": "2026-11-09T10:20:00Z", "usage": {"output_tokens": 1}},   # after the helper skill started
        {"sid": "s1", "ts": "2026-11-09T10:35:00Z", "usage": {"output_tokens": 2}, "agent": "example:reviewer",
         "launch": "2026-11-09T10:33:00Z"}])
    assert [(r["skill"], r["tokens"]["output_tokens"]) for r in rows] == [
        ("simo-dev:plan-execute", 1), ("simo-dev:review-loop", 2), ("simo-dev:mr-review", 0)]


PREFLIGHT = [
    _ev("10:00", "step_start", "simo-dev:review-loop"),
    _ev("10:10", "gate", "simo-dev:review-loop", gate="review_exit", answer="clean"),
    _ev("10:11", "step_start", "simo-dev:commit"),
    _ev("10:12", "step_start", "simo-dev:review-loop"),
    _ev("10:14", "retry", "simo-dev:review-loop", iteration=2),
    _ev("10:15", "gate", "simo-dev:review-loop", gate="review_exit", answer="clean"),
    _ev("10:18", "gate", "simo-dev:commit", gate="mr_created", answer="created"),
    _ev("10:20", "step_start", "simo-dev:mr-followup"),
]


def test_the_commit_preflight_returns_to_commit_at_its_review_exit():
    marks = ft.step_marks(PREFLIGHT)
    assert _marks(PREFLIGHT) == {"s1": [
        ("10:00", "simo-dev:review-loop"), ("10:11", "simo-dev:commit"), ("10:12", "simo-dev:review-loop"),
        ("10:15", "simo-dev:commit"), ("10:20", "simo-dev:mr-followup")]}
    rows = ft.split_by_step(marks, [
        {"sid": "s1", "ts": "2026-11-09T10:05:00Z", "usage": {"output_tokens": 1}},   # the review
        {"sid": "s1", "ts": "2026-11-09T10:13:00Z", "usage": {"output_tokens": 2}},   # the pre-flight
        {"sid": "s1", "ts": "2026-11-09T10:16:00Z", "usage": {"output_tokens": 4}},   # push, MR body
        {"sid": "s1", "ts": "2026-11-09T10:17:00Z", "usage": {"output_tokens": 8}, "agent": "general-purpose",
         "launch": "2026-11-09T10:16:30Z"},
        {"sid": "s1", "ts": "2026-11-09T10:25:00Z", "usage": {"output_tokens": 16}}])
    assert [(r["skill"], r["tokens"]["output_tokens"]) for r in rows] == [
        ("simo-dev:review-loop", 1), ("simo-dev:commit", 0), ("simo-dev:review-loop", 2), ("simo-dev:commit", 12),
        ("simo-dev:mr-followup", 16)]
    assert rows[3]["agents"]["general-purpose"]["output_tokens"] == 8


def test_a_review_exit_outside_the_preflight_opens_no_step():
    events = [
        _ev("10:00", "step_start", "simo-dev:plan-execute"),
        _ev("10:10", "step_start", "simo-dev:review-loop"),                               # after plan-execute: not a pre-flight
        _ev("10:20", "gate", "simo-dev:review-loop", gate="review_exit", answer="clean"),
        _ev("10:30", "step_start", "simo-dev:commit"),
        _ev("10:35", "gate", "simo-dev:review-loop", gate="review_exit", answer="clean"),  # commit ran no review-loop step
        _ev("10:40", "step_start", "simo-dev:commit", session="s2"),
        _ev("10:41", "step_start", "simo-dev:review-loop", session="s2"),
        _ev("10:42", "step_start", "simo-dev:mr-followup", session="s2"),                 # the pre-flight never exited
        _ev("10:50", "gate", "simo-dev:review-loop", session="s2", gate="review_exit", answer="clean"),
        _ev("11:00", "step_start", "simo-dev:commit", session="s3"),
        _ev("11:01", "step_start", "simo-dev:review-loop", session="s3"),                # a pre-flight that never exited
        _ev("11:02", "step_start", "simo-dev:plan-execute", session="s3"),
        _ev("11:03", "step_start", "simo-dev:review-loop", session="s3"),                # a plain review after it
        _ev("11:04", "gate", "simo-dev:review-loop", session="s3", gate="review_exit", answer="clean"),
        _ev("11:10", "step_start", "simo-dev:commit", session="s4"),
        _ev("11:11", "step_start", "simo-dev:review-loop", session="s4"),
        _ev("11:12", "gate", "simo-dev:review-loop", session="s4", gate="review_exit", answer="unresolved"),
        _ev("11:13", "gate", "simo-dev:review-loop", session="s4", gate="review_exit", answer="clean"),  # one return only
    ]
    assert _marks(events) == {
        "s1": [("10:00", "simo-dev:plan-execute"), ("10:10", "simo-dev:review-loop"), ("10:30", "simo-dev:commit")],
        "s2": [("10:40", "simo-dev:commit"), ("10:41", "simo-dev:review-loop"), ("10:42", "simo-dev:mr-followup")],
        "s3": [("11:00", "simo-dev:commit"), ("11:01", "simo-dev:review-loop"), ("11:02", "simo-dev:plan-execute"),
               ("11:03", "simo-dev:review-loop")],
        "s4": [("11:10", "simo-dev:commit"), ("11:11", "simo-dev:review-loop"), ("11:12", "simo-dev:commit")]}


def test_a_retry_of_review_loop_under_commit_is_a_preflight_too():
    events = [
        _ev("10:00", "step_start", "simo-dev:commit"),
        _ev("10:02", "retry", "simo-dev:review-loop", iteration=2),
        _ev("10:05", "gate", "simo-dev:review-loop", gate="review_exit", answer="cap"),
    ]
    assert _marks(events) == {"s1": [("10:00", "simo-dev:commit"), ("10:02", "simo-dev:review-loop"), ("10:05", "simo-dev:commit")]}


def test_an_outcome_event_attributes_the_task(env, capsys):
    _transcripts(env)
    path = _events("20261109T1000-sid1", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"})])
    assert ft.main(["event", "--type", "outcome", "--task", "20261109T1000-sid1", "--session", "sid1",
                    "--field", "result=merged"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("event: outcome -> ") and out[1].startswith("attribution: total=")
    events = ft.read_events(path)
    assert [e["type"] for e in events] == ["step_start", "outcome", "attribution"]
    assert events[-1]["by_step"][0]["skill"] == "simo-dev:task-intake" and events[-1]["session"] == "sid1"
    assert ft.main(["event", "--type", "gate", "--task", "20261109T1000-sid1", "--session", "sid1",
                    "--field", "gate=g", "--field", "answer=a"]) == 0
    assert [e["type"] for e in ft.read_events(path)][-1] == "gate"  # only an outcome attributes


def test_a_failed_attribution_keeps_the_outcome_and_says_how_to_rerun(env, capsys, monkeypatch):
    path = _events("20261109T1000-sid1", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"})])

    def boom(**_):
        raise KeyError("usage")
    monkeypatch.setattr(ft, "attribute", boom)
    assert ft.main(["event", "--type", "outcome", "--task", "20261109T1000-sid1", "--session", "sid1",
                    "--field", "result=merged"]) == 0
    assert "re-run: flow_telemetry.py attribute --task 20261109T1000-sid1" in capsys.readouterr().err
    assert [e["type"] for e in ft.read_events(path)] == ["step_start", "outcome"]


def test_open_tasks_are_the_ones_the_session_holds_with_no_outcome(env):
    held = [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
            ("2026-11-09T10:05:00Z", "task_bound", {"branch": "feat/a", "repo": "r", "ticket": ""})]
    _events("20261109T1000-sid1", held)
    _events("20261109T1100-sid1", [("2026-11-09T11:00:00Z", "step_start", {"skill": "simo-dev:task-intake"})])
    _events("20261109T1200-sid1", held + [("2026-11-09T12:30:00Z", "outcome", {"result": "merged"})])
    _events("20261109T1300-sid1", held + [("2026-11-09T13:30:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    ft.append_event(ft.task_file("20261109T1400-other"), {"ts": "2026-11-09T14:00:00Z", "type": "step_start",
                                                          "task": "20261109T1400-other", "session": "sid2",
                                                          "skill": "simo-dev:task-intake"})
    ft.append_event(ft.session_file("sid1"), {"ts": "2026-11-09T09:00:00Z", "type": "step_start", "task": "",
                                              "session": "sid1", "skill": "simo-dev:tdd"})
    assert ft.open_tasks("sid1") == [("20261109T1000-sid1", "feat/a"), ("20261109T1100-sid1", "")]
    assert ft.open_tasks("nobody") == []


def test_split_by_step_keeps_a_subagent_on_its_launch_step_and_sums_to_total():
    marks = ft.step_marks([
        {"ts": "2026-11-09T10:00:00Z", "type": "step_start", "session": "s1", "skill": "simo-dev:plan-write"},
        {"ts": "2026-11-09T10:30:00Z", "type": "step_start", "session": "s1", "skill": "simo-dev:plan-execute"},
        {"ts": "2026-11-09T10:40:00Z", "type": "step_start", "session": "s2", "skill": "simo-dev:commit"},
    ])
    records = [
        {"sid": "s1", "ts": "2026-11-09T10:10:00Z", "usage": {"output_tokens": 1}},
        {"sid": "s1", "ts": "2026-11-09T10:35:00Z", "usage": {"output_tokens": 2}, "agent": "example:reviewer",
         "launch": "2026-11-09T10:20:00Z"},
        {"sid": "s1", "ts": "2026-11-09T10:31:00Z", "usage": {"cache_read_input_tokens": 50}},
        {"sid": "s2", "ts": "2026-11-09T10:35:00Z", "usage": {"output_tokens": 4}},
        {"sid": "s3", "ts": "2026-11-09T10:36:00Z", "usage": {"input_tokens": 7}},
        {"sid": "s2", "ts": "2026-11-09T09:00:00Z", "usage": {"output_tokens": 16}},
        {"sid": "s1", "ts": "2026-11-09T10:12:00Z", "usage": {"output_tokens": 32}, "agent": "example:reviewer", "launch": "junk"},
        {"sid": "s1", "ts": "2026-11-09T10:33:00Z", "usage": {"cache_creation_input_tokens": 64}, "agent": "example:explorer",
         "launch": "2026-11-09T09:30:00Z"},
    ]
    rows = ft.split_by_step(marks, records)
    assert [(r["skill"], r["session"], r["start"]) for r in rows] == [
        ("(none)", "s2", ""),
        ("simo-dev:plan-write", "s1", "2026-11-09T10:00:00Z"),
        ("simo-dev:plan-execute", "s1", "2026-11-09T10:30:00Z"),
        ("simo-dev:commit", "s2", "2026-11-09T10:40:00Z")]
    by = {(r["skill"], r["session"]): r for r in rows}
    assert by[("simo-dev:plan-write", "s1")]["tokens"]["output_tokens"] == 35
    assert by[("simo-dev:plan-write", "s1")]["agents"]["example:reviewer"]["output_tokens"] == 34
    assert by[("simo-dev:plan-execute", "s1")]["tokens"] == {"input_tokens": 7, "cache_creation_input_tokens": 64,
                                                             "cache_read_input_tokens": 50, "output_tokens": 4}
    assert by[("simo-dev:plan-execute", "s1")]["agents"]["example:explorer"]["cache_creation_input_tokens"] == 64
    assert by[("simo-dev:commit", "s2")]["tokens"]["output_tokens"] == 0
    assert by[("(none)", "s2")]["tokens"]["output_tokens"] == 16
    assert _sum(rows) == {"input_tokens": 7, "cache_creation_input_tokens": 64, "cache_read_input_tokens": 50, "output_tokens": 55}


def test_attribute_by_step_sums_to_total_and_names_the_subagent(env):
    proj = env / ".claude" / "projects" / "-x"
    (proj / "sid1" / "subagents").mkdir(parents=True)
    (proj / "sid1.jsonl").write_text("\n".join([
        _rec("m1", "2026-11-09T10:01:00Z", skill="simo-dev:task-intake", output_tokens=2),
        _rec("m2", "2026-11-09T10:20:00Z", output_tokens=5),
        _rec("m3", "2026-11-09T10:40:00Z", cache_creation_input_tokens=8),
    ]) + "\n")
    sub = proj / "sid1" / "subagents"
    (sub / "agent-r.jsonl").write_text("\n".join([_rec("r1", "2026-11-09T10:22:00Z", output_tokens=3),
                                                  _rec("r2", "2026-11-09T10:45:00Z", output_tokens=4)]) + "\n")
    (sub / "agent-r.meta.json").write_text(json.dumps({"agentType": "example:reviewer", "description": "d"}))
    _events("T9", [("2026-11-09T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
                   ("2026-11-09T10:15:00Z", "step_start", {"skill": "simo-dev:review-loop"}),
                   ("2026-11-09T10:30:00Z", "step_start", {"skill": "simo-dev:commit"}),
                   ("2026-11-09T11:00:00Z", "outcome", {"result": "merged"})])
    got = ft.attribute(task_id="T9")
    assert _sum(got["by_step"]) == got["total"]
    assert all(r["skill"] != ft.NONE for r in got["by_step"])
    by = {r["skill"]: r for r in got["by_step"]}
    assert by["simo-dev:review-loop"]["tokens"]["output_tokens"] == 12
    assert by["simo-dev:review-loop"]["agents"]["example:reviewer"]["output_tokens"] == 7
    assert by["simo-dev:commit"]["tokens"]["cache_creation_input_tokens"] == 8


def test_attribute_session_mode_prints_only_the_summary(env, capsys):
    _transcripts(env)
    assert ft.main(["attribute", "--session", "sid1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and out[0].startswith("attribution: total=")
