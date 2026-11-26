#!/usr/bin/env python3
"""Tests for flow_dashboard: data model and HTML output, on synthetic fixtures."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import flow_dashboard as fd  # noqa: E402
import flow_telemetry as ft  # noqa: E402
import transcript_usage as tu  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(tmp_path / "dev-flow"))
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(tmp_path / "active"))
    monkeypatch.setenv("SIMO_DEV_DASHBOARD_CACHE", str(tmp_path / "dash-cache.json"))
    return tmp_path


def _usage(mid, ts, model="", **usage):
    rec = {"type": "assistant", "timestamp": ts, "message": {"id": mid, "usage": usage}}
    if model:
        rec["message"]["model"] = model
    return rec


def _user(ts, text="go on", tool_result=False, meta=False):
    content = [{"type": "tool_result", "content": "ok"}] if tool_result else text
    rec = {"type": "user", "timestamp": ts, "message": {"role": "user", "content": content}}
    if meta:
        rec["isMeta"] = True
    return rec


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _events(task, rows, session="sid1"):
    path = ft.task_file(task)
    for ts, etype, fields in rows:
        ft.append_event(path, {"ts": ts, "type": etype, "task": task, "session": session, **fields})


def _project(home):
    return home / ".claude" / "projects" / "-example"


TASK_ROWS = [
    ("2026-01-05T10:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
    ("2026-01-05T10:02:00Z", "task_bound", {"ticket": "", "branch": "feat/example", "repo": "example-repo"}),
    ("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:plan-execute"}),
    ("2026-01-05T10:40:00Z", "step_start", {"skill": "simo-dev:review-loop"}),
    ("2026-01-05T10:45:00Z", "retry", {"skill": "simo-dev:review-loop", "iteration": 2}),
    ("2026-01-05T10:50:00Z", "gate", {"skill": "simo-dev:review-loop", "gate": "review_exit", "answer": "clean"}),
    ("2026-01-05T11:00:00Z", "outcome", {"result": "merged", "mr": "https://example.invalid/mr/1"}),
    ("2026-01-05T11:00:30Z", "gate", {"gate": "handoff", "answer": "released"}),
]


def _transcripts(home):
    proj = _project(home)
    _write(proj / "sid1.jsonl", [
        _usage("early", "2026-01-05T09:00:00Z", output_tokens=100),
        _usage("m1", "2026-01-05T10:05:00Z", model="claude-sonnet-5", output_tokens=4),
        _usage("m1", "2026-01-05T10:05:00Z", model="claude-sonnet-5", output_tokens=10),
        _user("2026-01-05T10:10:00Z"),
        _usage("m2", "2026-01-05T10:25:00Z", model="claude-sonnet-5", cache_read_input_tokens=1000),
        {"type": "user", "timestamp": "2026-01-05T10:30:00Z", "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
        {"type": "user", "timestamp": "2026-01-05T10:32:00Z",
         "message": {"role": "user", "content": "<system-reminder>\nnote from another session\n</system-reminder>"}},
        {"type": "user", "timestamp": "2026-01-05T10:34:00Z", "isCompactSummary": True,
         "message": {"role": "user", "content": "This session is being continued"}},
        {"type": "user", "timestamp": "2026-01-05T10:38:00Z", "turnOrigin": "sdk",
         "message": {"role": "user", "content": "The app was quit while you were working."}},
        _user("2026-01-05T10:35:00Z", tool_result=True),
        _user("2026-01-05T10:36:00Z", meta=True),
    ])
    sub = proj / "sid1" / "subagents"
    _write(sub / "agent-a1.jsonl", [_usage("s1", "2026-01-05T10:42:00Z", model="claude-sonnet-5", output_tokens=3),
                                    {"type": "user", "timestamp": "2026-01-05T10:44:00Z", "isSidechain": True}])
    (sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "simo-dev:code-reviewer", "description": "review"}))


def test_orphans_skip_the_intake_line_written_before_start(env):
    _events("20260105T1000-sid1", TASK_ROWS)
    directory = ft.telemetry_dir()
    _write(directory / "session-sid1.jsonl", [
        {"ts": "2026-01-05T09:59:57Z", "type": "step_start", "task": "", "session": "sid1", "skill": "simo-dev:task-intake"}])
    _write(directory / "session-sid2.jsonl", [
        {"ts": "2026-01-05T08:00:00Z", "type": "step_start", "task": "", "session": "sid2", "skill": "simo-dev:commit"}])
    tasks = fd.load_tasks(directory)
    assert [t[0] for t in tasks] == ["20260105T1000-sid1"]
    assert [(o["session"], o["skill"]) for o in fd.load_orphans(directory, tasks)] == [("sid2", "simo-dev:commit")]


def test_build_task_buckets_tokens_per_step_and_counts_rounds(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    steps = {s["skill"]: s for s in model["steps"]}
    assert steps["simo-dev:task-intake"]["tokens"]["output_tokens"] == 10
    assert steps["simo-dev:plan-execute"]["tokens"]["cache_read_input_tokens"] == 1000
    assert steps["simo-dev:review-loop"]["tokens"]["output_tokens"] == 3
    assert model["total"]["output_tokens"] == 13
    assert model["cost"] == pytest.approx((13 * 10 + 1000 * 0.20) / 1e6)
    assert model["estimated"] is False and model["unpriced"] == []
    assert model["source"] == "transcripts"
    assert model["review_rounds"] == 2 and model["followup_rounds"] == 0
    assert (model["branch"], model["repo"], model["result"]) == ("feat/example", "example-repo", "merged")
    assert (model["end"] - model["start"]).total_seconds() == 3600
    kinds = sorted(m["kind"] for m in model["markers"])
    assert kinds == ["gate", "retry"]


def test_waits_count_only_real_user_turns(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in model["waits"]] == [("10:05", "10:10")]
    assert model["wait_s"] == 300


def test_subagents_come_from_transcripts_and_meta(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert [(s["type"], s["start"].strftime("%H:%M"), s["end"].strftime("%H:%M")) for s in model["subagents"]] == [
        ("simo-dev:code-reviewer", "10:42", "10:44")]


def test_attribution_is_the_fallback_without_transcripts(env):
    total = {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}
    _events("T1", TASK_ROWS + [("2026-01-05T11:01:00Z", "attribution",
                                {"total": total, "by_skill": {"simo-dev:commit": total}, "sessions": 1})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["source"] == "attribution"
    assert model["total"] == total
    assert model["by_skill"] == {"simo-dev:commit": total}
    assert model["estimated"] is True and model["cost"] == pytest.approx(tu.dollars(tu.estimate(total))) and model["cost"] > 0


def test_a_handed_off_session_stops_at_its_last_event(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
                                 _usage("b", "2026-01-05T10:30:00Z", output_tokens=100)])
    _write(proj / "sid2.jsonl", [_usage("c", "2026-01-05T10:25:00Z", output_tokens=2)])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:10:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    _events("T1", [("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:plan-execute"}),
                   ("2026-01-05T11:00:00Z", "outcome", {"result": "merged"})], session="sid2")
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["total"]["output_tokens"] == 3
    assert model["markers"] == []


def test_no_tokens_at_all_is_reported_as_none(env):
    _events("T1", TASK_ROWS)
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["source"] == "none" and model["cost"] == 0


def _snapshot(root):
    return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()}


def test_main_writes_one_escaped_offline_html_and_reads_only(env):
    _transcripts(env)
    _events("T1", [("2026-01-05T09:59:00Z", "task_bound", {"branch": "<b>feat/x</b>", "repo": "example-repo"})]
            + [row for row in TASK_ROWS if row[1] != "task_bound"])
    out = env / "out" / "dash.html"
    before = _snapshot(env / ".claude") | _snapshot(ft.telemetry_dir())
    assert fd.main(["--out", str(out)]) == 0
    assert _snapshot(env / ".claude") | _snapshot(ft.telemetry_dir()) == before
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert "&lt;b&gt;feat/x&lt;/b&gt;" in page and "<b>feat/x" not in page
    assert "<svg" in page and '<tr class="top"><td>2</td><td>simo-dev:plan-execute</td>' in page
    for external in ("<script src", "<link", "@import", "url(http"):
        assert external not in page


def test_main_task_filter_and_unknown_task(env, capsys):
    _events("T1", TASK_ROWS)
    _events("T2", [("2026-01-06T10:00:00Z", "step_start", {"skill": fd.INTAKE})])
    out = env / "dash.html"
    assert fd.main(["--out", str(out), "--task", "T2"]) == 0
    assert 'id="task-T2"' in out.read_text(encoding="utf-8") and 'id="task-T1"' not in out.read_text(encoding="utf-8")
    assert fd.main(["--out", str(out), "--task", "nope"]) == 2
    assert "no task nope" in capsys.readouterr().err


def test_an_out_of_task_invocation_makes_an_untracked_row(env):
    _write(ft.telemetry_dir() / "session-sid2.jsonl", [
        {"ts": "2026-01-05T08:00:00Z", "type": "step_start", "task": "", "session": "sid2", "skill": "simo-dev:commit"}])
    out = env / "dash.html"
    assert fd.main(["--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert 'data-id="session-sid2"' in page and '">untracked</span>' in page and "Out-of-task" not in page


def test_an_open_task_stops_where_the_next_task_of_its_session_starts(env):
    _write(_project(env) / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
                                          _usage("b", "2026-01-05T12:05:00Z", output_tokens=100)])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE})])
    _events("T2", [("2026-01-05T12:00:00Z", "step_start", {"skill": fd.INTAKE})])
    models = fd.build(ft.telemetry_dir())
    by_id = {m["id"]: m for m in models}
    assert by_id["T1"]["total"]["output_tokens"] == 1
    assert by_id["T1"]["end"].strftime("%H:%M") == "10:05"
    assert by_id["T2"]["total"]["output_tokens"] == 100


def test_next_task_is_by_time_inside_the_session_not_by_task_order(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1)])
    _write(proj / "sid2.jsonl", [_usage("tb", "2026-01-05T10:32:00Z", output_tokens=10),
                                 _usage("b", "2026-01-05T11:05:00Z", output_tokens=100)])
    _events("TA", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE})])
    _events("TB", [("2026-01-05T10:30:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:35:00Z", "gate", {"gate": "handoff", "answer": "released"})], session="sid2")
    _events("TA", [("2026-01-05T11:00:00Z", "step_start", {"skill": "simo-dev:plan-execute"})], session="sid2")
    models = fd.build(ft.telemetry_dir())
    by_id = {m["id"]: m for m in models}
    assert by_id["TA"]["total"]["output_tokens"] == 101
    assert by_id["TB"]["total"]["output_tokens"] == 10


def test_a_task_that_resumes_after_another_in_the_same_session_keeps_counting(env):
    _write(_project(env) / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
                                          _usage("tb", "2026-01-05T10:32:00Z", output_tokens=10),
                                          _usage("b", "2026-01-05T11:05:00Z", output_tokens=100)])
    _events("TA", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE})])
    _events("TB", [("2026-01-05T10:30:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:35:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    _events("TA", [("2026-01-05T11:00:00Z", "step_start", {"skill": "simo-dev:plan-execute"})])
    models = fd.build(ft.telemetry_dir())
    by_id = {m["id"]: m for m in models}
    assert by_id["TA"]["total"]["output_tokens"] == 101
    assert by_id["TB"]["total"]["output_tokens"] == 10


def test_a_wait_is_not_bridged_across_another_task(env):
    _write(_project(env) / "sid1.jsonl", [
        _usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
        _user("2026-01-05T10:50:00Z"),
        _usage("b", "2026-01-05T11:05:00Z", output_tokens=1)])
    _events("TA", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T11:00:00Z", "step_start", {"skill": "simo-dev:plan-execute"})])
    _events("TB", [("2026-01-05T10:30:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:35:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    models = fd.build(ft.telemetry_dir())
    by_id = {m["id"]: m for m in models}
    assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in by_id["TA"]["waits"]] == [("10:05", "10:30")]
    assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in by_id["TB"]["waits"]] == [("10:30", "10:35")]


def test_main_unwritable_output_returns_2(env):
    _events("T1", TASK_ROWS)
    (env / "blocker").write_text("not a directory")
    assert fd.main(["--out", str(env / "blocker" / "dash.html")]) == 2
    (env / "outdir.html").mkdir()
    assert fd.main(["--out", str(env / "outdir.html")]) == 2
    assert not list(env.rglob("*.tmp.*"))


def test_an_open_handed_off_task_stops_at_the_handoff(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
                                 _usage("b", "2026-01-05T10:30:00Z", output_tokens=100)])
    _write(proj / "sid2.jsonl", [_usage("c", "2026-01-05T10:25:00Z", output_tokens=2)])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:10:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    _events("T1", [("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:plan-execute"})], session="sid2")
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["total"]["output_tokens"] == 3


def test_a_session_that_rebinds_after_a_handoff_keeps_counting(env):
    _write(_project(env) / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
                                          _usage("b", "2026-01-05T10:45:00Z", output_tokens=5)])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:10:00Z", "gate", {"gate": "handoff", "answer": "released"}),
                   ("2026-01-05T10:40:00Z", "step_start", {"skill": "simo-dev:review-loop"})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["total"]["output_tokens"] == 6


def test_an_answered_question_is_a_wait_and_other_tool_results_are_not(env):
    def tool(kind, ts, **content):
        role = "assistant" if kind == "tool_use" else "user"
        rec = {"type": role, "timestamp": ts, "message": {"role": role, "content": [{"type": kind, **content}]}}
        if role == "assistant":
            rec["message"]["id"] = f"m-{ts}"
            rec["message"]["usage"] = {"output_tokens": 1}
        return rec

    _write(_project(env) / "sid1.jsonl", [
        tool("tool_use", "2026-01-05T10:01:00Z", id="q1", name="AskUserQuestion"),
        tool("tool_result", "2026-01-05T10:08:00Z", tool_use_id="q1"),
        tool("tool_use", "2026-01-05T10:09:00Z", id="b1", name="Bash"),
        tool("tool_result", "2026-01-05T10:15:00Z", tool_use_id="b1"),
    ])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:30:00Z", "outcome", {"result": "merged"})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in model["waits"]] == [("10:01", "10:08")]


def test_malformed_events_do_not_crash(env):
    path = ft.task_file("T1")
    ft.append_event(path, {"ts": 5, "type": "step_start", "session": "sid1", "skill": "x"})
    ft.append_event(path, {"ts": "2026-01-05T10:00:00Z", "type": "attribution", "session": "sid1",
                           "total": {"output_tokens": 4}, "by_skill": {"a": 5, "b": {"output_tokens": 4}}})
    ft.append_event(ft.task_file("T2"), {"ts": "2026-01-05T10:00:00Z", "type": "attribution", "session": "sid1",
                                         "total": {"output_tokens": 4}, "by_step": [5, {"skill": "x", "tokens": 3},
                                                                                    {"skill": "y", "start": "bad", "tokens": {}},
                                                                                    {"skill": "z", "start": "2026-01-05T10:00:00Z",
                                                                                     "tokens": {"output_tokens": None},
                                                                                     "agents": {"a": {"output_tokens": "3"}}}]})
    out = env / "dash.html"
    assert fd.main(["--out", str(out)]) == 0
    assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_overlapping_waits_count_once():
    t = fd._ts
    spans = [(t("2026-01-05T10:00:00Z"), t("2026-01-05T10:10:00Z")),
             (t("2026-01-05T10:05:00Z"), t("2026-01-05T10:20:00Z")),
             (t("2026-01-05T11:00:00Z"), t("2026-01-05T11:01:00Z"))]
    assert fd._union_s(spans) == 21 * 60


def test_page_lanes_mr_link_filter_and_global_orphans(env):
    _transcripts(env)
    _events("T1", [r for r in TASK_ROWS if r[1] != "outcome"]
            + [("2026-01-05T11:00:00Z", "outcome", {"result": "merged", "mr": "javascript:alert(1)"})])
    _events("T2", [("2026-01-06T10:00:00Z", "step_start", {"skill": fd.INTAKE})])
    _write(ft.telemetry_dir() / "session-sid9.jsonl", [
        {"ts": "2026-01-04T08:00:00Z", "type": "step_start", "task": "", "session": "sid9", "skill": "simo-dev:commit"}])
    out = env / "dash.html"
    assert fd.main(["--out", str(out), "--task", "T1"]) == 0
    page = out.read_text(encoding="utf-8")
    for cls in ('class="sub"', 'class="wait"', 'class="retry"', 'class="gate"', 'class="s0"'):
        assert cls in page
    assert 'href="javascript' not in page and ">MR</a>" not in page
    assert "sid9" not in page  # a --task page has no untracked rows


def test_build_reads_each_session_once(env, monkeypatch):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    _events("T2", [("2026-01-05T12:00:00Z", "step_start", {"skill": fd.INTAKE})])
    calls = []
    real = fd.read_session
    monkeypatch.setattr(fd, "read_session", lambda sid, disk=None: calls.append(sid) or real(sid, disk))
    fd.build(ft.telemetry_dir())
    assert calls == ["sid1"]


def _count_parses(monkeypatch):
    calls = []
    real = fd._lines
    monkeypatch.setattr(fd, "_lines", lambda path: calls.append(str(path)) or real(path))
    return calls


def test_an_unchanged_session_is_not_parsed_again_on_the_next_run(env, monkeypatch):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    first = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert oct(cache.stat().st_mode & 0o777) == "0o600"
    calls = _count_parses(monkeypatch)
    second = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert calls == []
    for key in ("total", "cost", "waits", "wait_s", "end"):
        assert second[0][key] == first[0][key]
    assert [(s["type"], s["start"]) for s in second[0]["subagents"]] == [(s["type"], s["start"]) for s in first[0]["subagents"]]


def test_a_changed_transcript_is_parsed_again(env, monkeypatch):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    with open(_project(env) / "sid1.jsonl", "a") as f:
        f.write(json.dumps(_usage("m3", "2026-01-05T10:55:00Z", output_tokens=7)) + "\n")
    calls = _count_parses(monkeypatch)
    models = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert calls and models[0]["total"]["output_tokens"] == 20


def test_a_broken_cache_file_is_ignored(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    cache.write_text("{not json")
    models = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert models[0]["total"]["output_tokens"] == 13
    assert json.loads(cache.read_text())["version"] == fd.CACHE_VERSION


def test_the_cache_keeps_no_message_text(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    text = cache.read_text()
    for needle in ("go on", "note from another session", "This session is being continued", "The app was quit"):
        assert needle not in text


def test_a_pruned_transcript_is_served_from_the_cache(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    first = fd.build(ft.telemetry_dir(), cache_path=cache)
    (_project(env) / "sid1.jsonl").unlink()
    second = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert second[0]["source"] == "transcripts"
    assert second[0]["total"] == first[0]["total"] and second[0]["waits"] == first[0]["waits"]
    assert "sid1" in json.loads(cache.read_text())["sessions"]


def test_an_all_hit_run_does_not_rewrite_the_cache(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    os.utime(cache, ns=(1, 1))
    fd.build(ft.telemetry_dir(), cache_path=cache)
    assert cache.stat().st_mtime_ns == 1


def test_a_damaged_cache_entry_is_parsed_again(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    data = json.loads(cache.read_text())
    data["sessions"]["sid1"]["data"]["marks"].append(5)
    cache.write_text(json.dumps(data))
    models = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert models[0]["total"]["output_tokens"] == 13


@pytest.mark.parametrize("field", ["agent", "launch", "model", "cache_1h", "speed"])
def test_a_cache_entry_with_a_bad_agent_field_is_parsed_again(env, field):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    data = json.loads(cache.read_text())
    data["sessions"]["sid1"]["data"]["records"]["m1"][field] = ["x"]
    cache.write_text(json.dumps(data))
    models = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert models[0]["steps"][0]["tokens"]["output_tokens"] == 10 and sum(models[0]["unattributed"].values()) == 0


def test_a_task_run_keeps_the_other_sessions_in_the_cache(env):
    _transcripts(env)
    _write(_project(env) / "sid2.jsonl", [_usage("x", "2026-01-06T10:05:00Z", output_tokens=1)])
    _events("T1", TASK_ROWS)
    _events("T2", [("2026-01-06T10:00:00Z", "step_start", {"skill": fd.INTAKE})], session="sid2")
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    fd.build(ft.telemetry_dir(), only="T2", cache_path=cache)
    assert set(json.loads(cache.read_text())["sessions"]) == {"sid1", "sid2"}


def test_the_task_title_is_the_last_custom_title_of_its_session(env):
    _transcripts(env)
    with open(_project(env) / "sid1.jsonl", "a") as f:
        f.write(json.dumps({"type": "custom-title", "customTitle": "first name", "sessionId": "sid1"}) + "\n")
        f.write(json.dumps({"type": "custom-title", "customTitle": "Add retry to export", "sessionId": "sid1"}) + "\n")
    _events("T1", TASK_ROWS)
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["title"] == "Add retry to export"


def test_no_custom_title_gives_an_empty_title(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    assert fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])["title"] == ""


def test_the_title_survives_a_pruned_transcript(env):
    _transcripts(env)
    with open(_project(env) / "sid1.jsonl", "a") as f:
        f.write(json.dumps({"type": "custom-title", "customTitle": "Add retry to export"}) + "\n")
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    (_project(env) / "sid1.jsonl").unlink()
    models = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert models[0]["title"] == "Add retry to export"


def test_a_cache_entry_with_a_bad_title_is_parsed_again(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    data = json.loads(cache.read_text())
    data["sessions"]["sid1"]["data"]["title"] = 5
    cache.write_text(json.dumps(data))
    models = fd.build(ft.telemetry_dir(), cache_path=cache)
    assert models[0]["title"] == "" and models[0]["total"]["output_tokens"] == 13


def test_a_non_string_ticket_does_not_crash_the_page(env):
    _events("T1", [("2026-01-05T10:00:00Z", "task_bound", {"ticket": 5, "branch": 7, "repo": None})])
    out = env / "dash.html"
    assert fd.main(["--out", str(out)]) == 0


def test_a_handed_off_task_takes_the_child_session_title(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
                                 {"type": "custom-title", "customTitle": "Intake of the export work"}])
    _write(proj / "sid2.jsonl", [_usage("c", "2026-01-05T10:25:00Z", output_tokens=2),
                                 {"type": "custom-title", "customTitle": "Add retry to export"}])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:10:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    _events("T1", [("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:plan-execute"}),
                   ("2026-01-05T11:00:00Z", "outcome", {"result": "merged"})], session="sid2")
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["title"] == "Add retry to export"


def test_cache_pct_is_cache_read_over_all_input():
    assert fd.cache_pct({"input_tokens": 10, "cache_creation_input_tokens": 30,
                         "cache_read_input_tokens": 60, "output_tokens": 999}) == pytest.approx(60.0)
    assert fd.cache_pct(fd._zero()) is None


@pytest.mark.parametrize("key, template, url", [
    ("PROJ-123", "https://tracker.example.invalid/browse/{key}", "https://tracker.example.invalid/browse/PROJ-123"),
    ("GH-5", "https://forge.example.invalid/issues/{number}", "https://forge.example.invalid/issues/5"),
    ("GH-5", "{repo_url}/issues/{number}", ""),
    ("PROJ-123", "javascript:alert({key})", ""),
    ("", "https://tracker.example.invalid/browse/{key}", ""),
    ("PROJ-123", "", ""),
])
def test_ticket_url(key, template, url):
    assert fd.ticket_url(key, template) == url


def test_build_reads_the_browse_template_read_only(env, monkeypatch):
    conf = env / "tracker.json"
    conf.write_text(json.dumps({"PROJ": {"browse_url": "https://tracker.example.invalid/browse/{key}"}}))
    monkeypatch.setenv("SIMO_DEV_TRACKER_CONFIG", str(conf))
    _events("T1", [("2026-01-05T10:00:00Z", "task_bound", {"ticket": "PROJ-7", "branch": "feat/x", "repo": "example-repo"})])
    before = conf.read_bytes(), conf.stat().st_mtime_ns
    models = fd.build(ft.telemetry_dir())
    assert models[0]["ticket_url"] == "https://tracker.example.invalid/browse/PROJ-7"
    assert (conf.read_bytes(), conf.stat().st_mtime_ns) == before


def test_a_broken_tracker_config_gives_no_link(env, monkeypatch):
    conf = env / "tracker.json"
    conf.write_text("{not json")
    monkeypatch.setenv("SIMO_DEV_TRACKER_CONFIG", str(conf))
    _events("T1", [("2026-01-05T10:00:00Z", "task_bound", {"ticket": "PROJ-7"})])
    models = fd.build(ft.telemetry_dir())
    assert models[0]["ticket_url"] == ""


def _model(task_id, title, out_tokens, start, source="transcripts"):
    t = fd._ts(start)
    total = {**fd._zero(), "output_tokens": out_tokens, "cache_read_input_tokens": 1000}
    return {"id": task_id, "title": title, "branch": "feat/x", "repo": "example-repo", "ticket": "", "mr": "",
            "result": "merged", "closed": True, "status": "done", "idle_s": 0.0, "start": t, "end": t, "steps": [], "subagents": [], "waits": [], "wait_s": 120.0,
            "markers": [], "total": total, "cost": tu.dollars(tu.estimate(total)), "cost_split": tu.estimate(total),
            "estimated": False, "unpriced": [], "source": source, "by_skill": {},
            "review_rounds": 1, "followup_rounds": 0}


def test_summary_cards_and_cost_bars_compare_tasks():
    models = [_model("T1", "Add retry", 10, "2026-01-05T10:00:00Z"), _model("T2", "", 30, "2026-01-06T10:00:00Z")]
    cards = fd.summary_cards(models)
    assert "<b>2</b>" in cards and "2 merged" in cards
    assert "<b>2,000</b>" in cards  # total cache read
    assert "<b>4m</b>" in cards  # total wait 2 × 120s, ft._duration format
    for m in models:
        m["status"] = "done"
    table = fd.overview(models)
    assert table.count('<span class="cbar">') == 2 and table.count('<i class="k-out"') == 2
    t1, t2 = (models[0]["cost_split"]["output_tokens"], models[1]["cost_split"]["output_tokens"])
    top = max(m["cost"] for m in models)
    assert f'style="width:{t2 / top * 100:.2f}%"' in table and f'style="width:{t1 / top * 100:.2f}%"' in table
    assert "one scale for every row" in table and '<i class="sw k-out"></i>output' in table


def test_overview_names_links_and_sort_keys(env, monkeypatch):
    conf = env / "tracker.json"
    conf.write_text(json.dumps({"PROJ": {"browse_url": "https://tracker.example.invalid/browse/{key}"}}))
    monkeypatch.setenv("SIMO_DEV_TRACKER_CONFIG", str(conf))
    _transcripts(env)
    with open(_project(env) / "sid1.jsonl", "a") as f:
        f.write(json.dumps({"type": "custom-title", "customTitle": "Add retry to export"}) + "\n")
    _events("T1", [r if r[1] != "task_bound" else (r[0], r[1], {**r[2], "ticket": "PROJ-7"}) for r in TASK_ROWS])
    out = env / "dash.html"
    assert fd.main(["--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert '<table id="overview">' in page and '<a href="#task-T1">Add retry to export</a>' in page
    assert '<a href="https://tracker.example.invalid/browse/PROJ-7">PROJ-7</a>' in page
    assert '<a href="https://example.invalid/mr/1">MR</a>' in page
    assert 'data-text="add retry to export' in page and 'data-v="' in page
    assert '<input type="search" id="filter"' in page and " hidden>" in page
    assert "<script>" in page and "<script src" not in page


def test_sections_collapse_and_details_are_visible_without_hover(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    _events("T2", [("2026-01-06T10:00:00Z", "step_start", {"skill": fd.INTAKE})], session="sid2")
    out = env / "dash.html"
    assert fd.main(["--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert '<details class="task" id="task-T2" ' in page and page.count('<details class="task"') == 2
    t1, t2 = page.index('id="task-T1"'), page.index('id="task-T2"')
    assert t1 > t2  # newest first
    assert page[t2:page.index(">", t2)].endswith(" open")
    assert not page[t1:page.index(">", t1)].endswith(" open")
    assert 'class="legend"' in page and 'width="100%"' in page and 'width="960"' not in page
    assert '<i class="sw s0">' in page and ".sw.s0{background:var(--step0)}" in page
    assert "simo-dev:code-reviewer · review · 2m</li>" in page  # subagent listed in clear
    assert "gate · review_exit=clean</li>" in page
    assert "Estimated cost per task" not in page and 'class="cards"' in page


def test_overview_can_group_by_repo_and_nest_child_sessions(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1),
                                 {"type": "custom-title", "customTitle": "Intake of the export work"}])
    _write(proj / "sid2.jsonl", [_usage("c", "2026-01-05T10:25:00Z", output_tokens=2, cache_read_input_tokens=50),
                                 {"type": "custom-title", "customTitle": "Add retry to export"}])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:02:00Z", "task_bound", {"ticket": "", "branch": "feat/a", "repo": "example-repo"}),
                   ("2026-01-05T10:10:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    _events("T1", [("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:plan-execute"}),
                   ("2026-01-05T11:00:00Z", "outcome", {"result": "merged"})], session="sid2")
    models = fd.build(ft.telemetry_dir())
    m = models[0]
    assert [s["id"] for s in m["sessions"]] == ["sid1", "sid2"] and m["root"] == "sid1"
    assert m["sessions"][1]["tokens"]["output_tokens"] == 2
    assert sum(s["tokens"]["output_tokens"] for s in m["sessions"]) == m["total"]["output_tokens"]
    html_ = fd.overview(models)
    assert 'class="task-row"' in html_ and 'data-repo="example-repo"' in html_ and 'data-root="sid1"' in html_
    assert 'data-root-title="Intake of the export work"' in html_
    assert html_.count('<tr class="sess" data-task="T1">') == 2
    assert "↳ Add retry to export" in html_
    assert '<select id="group" hidden>' in html_ and '<option value="session">' in html_
    assert "function render(" in fd.SCRIPT


def test_a_single_session_task_has_no_nested_rows(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    models = fd.build(ft.telemetry_dir())
    assert len(models[0]["sessions"]) == 1 and 'class="sess"' not in fd.overview(models)


def test_mean_and_median_differ_and_newest_first_is_by_time():
    models = [_model("T1", "a", 10, "2026-01-05T10:00:00Z"), _model("T2", "b", 20, "2026-01-05T12:15:00+02:00"),
              _model("T3", "c", 300, "2026-01-05T10:30:00Z")]
    costs = sorted(m["cost"] for m in models)
    cards = fd.summary_cards(models)
    assert f"<b>{fd._usd(sum(costs) / 3)}</b><span>mean $ est., tasks with tokens</span>" in cards
    assert f"<b>{fd._usd(costs[1])}</b><span>median $ est., tasks with tokens</span>" in cards
    assert [m["id"] for m in fd._newest_first(models)] == ["T3", "T2", "T1"]  # 12:15+02:00 is 10:15Z: between T1 and T3


def test_an_untitled_session_row_shows_its_id_once(env):
    proj = _project(env)
    _write(proj / "sid1aaaaaaaa.jsonl", [_usage("a", "2026-01-05T10:05:00Z", output_tokens=1)])
    _write(proj / "sid2bbbbbbbb.jsonl", [_usage("c", "2026-01-05T10:25:00Z", output_tokens=2),
                                         {"type": "custom-title", "customTitle": "Add retry to export"}])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:10:00Z", "gate", {"gate": "handoff", "answer": "released"})], session="sid1aaaaaaaa")
    _events("T1", [("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:plan-execute"}),
                   ("2026-01-05T11:00:00Z", "outcome", {"result": "merged"})], session="sid2bbbbbbbb")
    models = fd.build(ft.telemetry_dir())
    html_ = fd.overview(models)
    assert "<td>↳ sid1aaaa</td>" in html_
    assert '↳ Add retry to export <small class="muted">sid2bbbb</small>' in html_


def test_task_tree_nests_children_and_ignores_unknown_or_cyclic_parents():
    order = ["A", "B", "X", "C", "D", "Y"]
    parents = {"B": "A", "C": "B", "D": "A", "A": "", "X": "missing", "Y": "Y"}
    tree = fd.task_tree(parents, order)
    assert sorted(order, key=lambda t: tree[t]["order"]) == ["A", "B", "C", "D", "X", "Y"]
    assert {t: (tree[t]["root"], tree[t]["depth"]) for t in "ABCD"} == {
        "A": ("A", 0), "B": ("A", 1), "C": ("A", 2), "D": ("A", 1)}
    assert tree["A"]["children"] == ["B", "D"] and tree["B"]["parent"] == "A"
    assert tree["X"] == {**tree["X"], "root": "X", "depth": 0, "parent": ""}
    assert tree["Y"]["root"] == "Y" and tree["Y"]["parent"] == ""
    late = fd.task_tree({"C1": "P", "C2": "P", "G": "C1"}, ["P", "C1", "C2", "G"])
    assert sorted(late, key=lambda t: late[t]["order"]) == ["P", "C1", "G", "C2"]
    cyc = fd.task_tree({"P": "Q", "Q": "P"}, ["P", "Q"])
    assert cyc["P"]["depth"] == 0 and cyc["Q"]["depth"] == 0


def test_dashboard_groups_follow_up_tasks_under_their_parent(env):
    _events("20260105T1000-parent00", TASK_ROWS)
    _events("20260105T1200-child000", [
        ("2026-01-05T12:00:00Z", "step_start", {"skill": "simo-dev:task-intake"}),
        ("2026-01-05T12:00:00Z", "task_link", {"parent": "20260105T1000-parent00"})], session="sid2")
    models = fd.build(ft.telemetry_dir())
    by_id = {m["id"]: m for m in models}
    assert by_id["20260105T1200-child000"]["tree"]["root"] == "20260105T1000-parent00"
    assert by_id["20260105T1000-parent00"]["tree"]["children"] == ["20260105T1200-child000"]
    html = fd.render_html(models)
    assert '<option value="tree">group by task tree</option>' in html
    assert 'data-tree="20260105T1000-parent00"' in html and 'data-depth="1"' in html
    assert 'href="#task-20260105T1000-parent00"' in html.split('id="task-20260105T1200-child000"')[1]
    parent_section = html.split('id="task-20260105T1000-parent00"')[1]
    assert 'follow-ups: <a href="#task-20260105T1200-child000">' in parent_section
    assert "treeOrder" in fd.SCRIPT and "paddingLeft" in fd.SCRIPT and "dataset.tree" in fd.SCRIPT
    only = fd.build(ft.telemetry_dir(), only="20260105T1200-child000")
    assert only[0]["tree"]["parent"] == "20260105T1000-parent00"
    assert "parent task: 20260105T1000-parent00</p>" in fd.render_html(only)


def test_steps_reopen_on_a_retry_and_a_subagent_stays_on_its_launch_step(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [_usage("m1", "2026-01-05T10:02:00Z", output_tokens=1),
                                 _usage("m2", "2026-01-05T10:12:00Z", output_tokens=2),
                                 _usage("m3", "2026-01-05T10:25:00Z", output_tokens=4)])
    sub = proj / "sid1" / "subagents"
    _write(sub / "agent-r.jsonl", [_usage("r1", "2026-01-05T10:06:00Z", output_tokens=8),
                                   _usage("r2", "2026-01-05T10:22:00Z", output_tokens=16)])
    (sub / "agent-r.meta.json").write_text(json.dumps({"agentType": "example:reviewer", "description": "review"}))
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": "simo-dev:mr-followup"}),
                   ("2026-01-05T10:05:00Z", "step_start", {"skill": "simo-dev:review-loop"}),
                   ("2026-01-05T10:10:00Z", "retry", {"skill": "simo-dev:mr-followup", "iteration": 2}),
                   ("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:commit"}),
                   ("2026-01-05T10:30:00Z", "outcome", {"result": "merged"})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert [(s["skill"], s["tokens"]["output_tokens"]) for s in model["steps"]] == [
        ("simo-dev:mr-followup", 1), ("simo-dev:review-loop", 24), ("simo-dev:mr-followup", 2), ("simo-dev:commit", 4)]
    assert model["steps"][1]["agents"]["example:reviewer"]["output_tokens"] == 24
    assert model["total"]["output_tokens"] == 31 and sum(model["unattributed"].values()) == 0
    html = fd.tokens_table(model)
    assert "↳ example:reviewer" in html


def test_commit_and_its_preflight_review_loop_each_show_their_own_cost(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [
        _usage("c1", "2026-01-05T10:00:30Z", model="claude-sonnet-5-5", output_tokens=100_000),    # commit, before the pre-flight
        _usage("p1", "2026-01-05T10:02:00Z", model="claude-sonnet-5-5", output_tokens=200_000),    # the pre-flight review
        _usage("h1", "2026-01-05T10:04:00Z", model="claude-sonnet-5-5", output_tokens=300_000),    # a helper skill's turn
        _usage("c2", "2026-01-05T10:06:00Z", model="claude-sonnet-5-5", output_tokens=400_000)])   # push, MR body, MR
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": "simo-dev:commit"}),
                   ("2026-01-05T10:01:00Z", "step_start", {"skill": "simo-dev:review-loop"}),
                   ("2026-01-05T10:01:30Z", "step_start", {"skill": "simo-dev:code-review-protocol"}),
                   ("2026-01-05T10:03:00Z", "gate", {"skill": "simo-dev:review-loop", "gate": "review_exit", "answer": "clean"}),
                   ("2026-01-05T10:03:30Z", "step_start", {"skill": "example:helper"}),
                   ("2026-01-05T10:07:00Z", "gate", {"skill": "simo-dev:commit", "gate": "mr_created", "answer": "created"}),
                   ("2026-01-05T10:10:00Z", "outcome", {"result": "merged"})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert [(s["skill"], round(s["cost"], 2)) for s in model["steps"]] == [
        ("simo-dev:commit", 1.0), ("simo-dev:review-loop", 2.0), ("simo-dev:commit", 7.0)]
    rows, _, _ = fd.step_totals([model])
    assert {r["skill"]: round(r["cost"], 2) for r in rows} == {"simo-dev:commit": 8.0, "simo-dev:review-loop": 2.0}


def test_by_step_of_the_attribution_event_is_the_fallback_without_transcripts(env):
    total = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 9}
    by_step = [{"skill": "(none)", "session": "sid1", "start": "", "tokens": {"output_tokens": 1}, "agents": {}},
               {"skill": "simo-dev:commit", "session": "sid1", "start": "2026-01-05T10:20:00Z", "tokens": {"output_tokens": 8},
                "agents": {"example:reviewer": {"output_tokens": 3}}},
               "junk"]
    _events("T1", TASK_ROWS + [("2026-01-05T11:01:00Z", "attribution", {"total": total, "by_step": by_step, "sessions": 1})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["source"] == "attribution" and model["total"] == total
    assert [(s["skill"], s["tokens"]["output_tokens"]) for s in model["steps"]] == [("simo-dev:commit", 8)]
    assert model["unattributed"]["output_tokens"] == 1
    html = fd.tokens_table(model)
    assert "↳ example:reviewer" in html and "before the first step" in html
    assert model["estimated"] is True and model["cost"] == pytest.approx(tu.dollars(tu.estimate(total)))


def test_by_step_costs_of_the_attribution_event_are_used_as_is(env):
    total = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 9}
    cost = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    by_step = [{"skill": "(none)", "session": "sid1", "start": "", "tokens": {"output_tokens": 1}, "agents": {},
                "cost": {**cost, "output_tokens": 0.25}, "agent_costs": {}},
               {"skill": "simo-dev:commit", "session": "sid1", "start": "2026-01-05T10:20:00Z", "tokens": {"output_tokens": 8},
                "agents": {"example:reviewer": {"output_tokens": 3}}, "cost": {**cost, "output_tokens": 0.5}, "agent_costs": {}}]
    _events("T1", TASK_ROWS + [("2026-01-05T11:01:00Z", "attribution", {"total": total, "by_step": by_step, "sessions": 1})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["cost"] == pytest.approx(0.75) and model["estimated"] is False


def test_cache_entry_keeps_model_and_one_hour_share(env):
    _write(_project(env) / "sid1.jsonl", [{"type": "assistant", "timestamp": "2026-01-05T10:05:00Z",
                                           "message": {"id": "m1", "model": "claude-sonnet-5-5",
                                                       "usage": {"cache_creation_input_tokens": 4,
                                                                 "cache_creation": {"ephemeral_1h_input_tokens": 4}}}}])
    _events("T1", TASK_ROWS)
    cache = fd.default_cache()
    fd.build(ft.telemetry_dir(), cache_path=cache)
    rec = next(iter(json.loads(cache.read_text())["sessions"]["sid1"]["data"]["records"].values()))
    assert rec["model"] == "claude-sonnet-5-5" and rec["cache_1h"] == 4


def test_old_cache_version_is_ignored(tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({"version": 4, "sessions": {"sid1": {}}}))
    assert fd.load_disk_cache(cache) == {}


def test_unknown_model_is_marked_on_the_page(env):
    _write(_project(env) / "sid1.jsonl", [{"type": "assistant", "timestamp": "2026-01-05T10:05:00Z",
                                           "message": {"id": "m1", "model": "future-model",
                                                       "usage": {"output_tokens": 1_000_000}}}])
    _events("T1", TASK_ROWS)
    task = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert task["unpriced"] == ["future-model"]
    fd.set_status(task, task["end"])
    html = fd.render_html([task])
    assert "≈ not priced exactly (unknown model: as claude-opus-5-5; fast mode: at standard rates): future-model" in html
    assert 'data-est="1"' in html and "<b>≈" in fd.summary_cards([task])
    assert f">≈{fd._usd(task['cost'])}<" in html


def test_an_attribution_event_without_any_breakdown_says_so(env):
    total = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 9}
    _events("T1", TASK_ROWS + [("2026-01-05T11:01:00Z", "attribution", {"total": total, "by_skill": {"a": 5}, "sessions": 1})])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    html = fd.tokens_table(model)
    assert model["source"] == "attribution" and "no per-step breakdown" in html and "steps from the attribution event" not in html


NOW = fd._ts("2026-01-06T12:00:00Z")


def _three_states(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    _events("T2", [("2026-01-05T08:00:00Z", "step_start", {"skill": fd.INTAKE})], session="sid2")
    _write(_project(env) / "sid2.jsonl", [_usage("r1", "2026-01-06T10:00:00Z", output_tokens=1)])
    _events("T3", [("2026-01-04T10:00:00Z", "step_start", {"skill": fd.INTAKE})], session="sid3")
    return fd.build(ft.telemetry_dir(), now=NOW)


def test_status_comes_from_the_outcome_and_the_last_activity(env):
    by_id = {m["id"]: m for m in _three_states(env)}
    assert by_id["T1"]["status"] == "done" and by_id["T1"]["result"] == "merged"
    assert by_id["T2"]["status"] == "running"  # last event 28h ago, but transcript activity 2h ago
    assert by_id["T2"]["idle_s"] == 2 * 3600
    assert by_id["T3"]["status"] == "stale" and by_id["T3"]["idle_s"] == 50 * 3600


@pytest.mark.parametrize("idle_h, status", [(fd.STALE_AFTER_H, "running"), (fd.STALE_AFTER_H + 0.001, "stale")])
def test_stale_starts_past_the_threshold(idle_h, status):
    m = {"closed": False, "end": NOW - timedelta(hours=idle_h)}
    fd.set_status(m, NOW)
    assert m["status"] == status


def test_an_open_task_with_no_timed_event_is_stale():
    m = {"closed": False, "end": None}
    fd.set_status(m, NOW)
    assert m["status"] == "stale" and m["idle_s"] is None and fd._status_label(m) == "stale"


def test_overview_and_sections_put_running_then_stale_then_done(env):
    models = _three_states(env)
    page = fd.render_html(models, NOW)
    rows = [page.index(f'class="task-row" data-id="{t}"') for t in ("T2", "T3", "T1")]
    assert rows == sorted(rows)
    sections = [page.index(f'id="task-{t}"') for t in ("T2", "T3", "T1")]
    assert sections == sorted(sections)
    assert page[sections[0]:page.index(">", sections[0])].endswith(" open")
    assert '<span class="badge st-running" title="open: last activity 2026-01-06 10:00 UTC">running · 2h00m ago</span>' in page
    assert '<span class="badge st-stale"' in page and ">stale · 2d ago</span>" in page
    assert '<span class="badge st-done" title="done: outcome merged">merged</span>' in page
    assert 'data-status="running"' in page and 'data-status="done"' in page
    assert '<select id="status" hidden>' in page and '<option value="status">group by status</option>' in page
    assert "generated 2026-01-06 12:00 UTC" in page
    assert "<b>1</b><span>running · 1 stale (no activity for 24h)</span>" in fd.summary_cards(models)


def test_a_closed_task_that_is_not_merged_shows_its_result_and_mr(env):
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T11:00:00Z", "outcome",
                    {"result": "abandoned", "mr": "https://forge.example.invalid/group/repo/-/merge_requests/42"})])
    out = env / "dash.html"
    assert fd.main(["--out", str(out), "--task", "T1"]) == 0
    page = out.read_text(encoding="utf-8")
    assert '<span class="badge st-closed" title="done: outcome abandoned">abandoned</span>' in page
    section = page[page.index('id="task-T1"'):]
    assert '<a href="https://forge.example.invalid/group/repo/-/merge_requests/42">MR !42</a>' in section


@pytest.mark.parametrize("url, text", [
    ("https://forge.example.invalid/group/repo/-/merge_requests/7", "MR !7"),
    ("https://forge.example.invalid/owner/repo/pull/12", "PR #12"),
    ("https://example.invalid/mr/1", "MR"),
])
def test_mr_text(url, text):
    assert fd._mr_text(url) == text


def test_sessions_get_a_thin_bar_on_the_task_scale(env):
    proj = _project(env)
    _write(proj / "sid1.jsonl", [_usage("a", "2026-01-05T10:05:00Z", model="claude-sonnet-5", output_tokens=1000)])
    _write(proj / "sid2.jsonl", [_usage("c", "2026-01-05T10:25:00Z", model="claude-sonnet-5", output_tokens=3000)])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T10:10:00Z", "gate", {"gate": "handoff", "answer": "released"})])
    _events("T1", [("2026-01-05T10:20:00Z", "step_start", {"skill": "simo-dev:plan-execute"}),
                   ("2026-01-05T11:00:00Z", "outcome", {"result": "merged"})], session="sid2")
    table = fd.overview(fd.build(ft.telemetry_dir(), now=NOW))
    assert table.count('<span class="cbar thin">') == 2
    assert 'style="width:100.00%"' in table and 'style="width:75.00%"' in table and 'style="width:25.00%"' in table


def test_the_page_has_a_period_filter_and_a_pager(env):
    page = fd.render_html(_three_states(env), NOW)
    assert f'<body data-now="{NOW.timestamp():.0f}">' in page
    assert '<select id="period" hidden' in page and '<option value="30">active in the last 30 days</option>' in page
    assert f'<div class="pager" id="pager" data-size="{fd.PAGE_SIZE}" hidden>' in page
    last = fd._ts("2026-01-06T10:00:00Z").timestamp()  # T2's last activity: its transcript record
    assert f'data-id="T2" data-text=' in page and f'data-last="{last:.0f}"' in page
    assert page.count('data-last="') == 6  # a row and a section per task
    assert "per.addEventListener('change',reset)" in page and "ordered.slice(page*size" in page


def test_the_overview_wraps_long_cells_instead_of_scrolling(env):
    page = fd.render_html(_three_states(env), NOW)
    assert '<td class="wrapit">example-repo<br><small class="muted">feat/example</small></td>' in page
    assert ">2026-01-05<br>10:00</td>" in page  # date and time on two lines
    assert "#overview td.wrapit{overflow-wrap:anywhere" in page


def _orphan(session, ts, skill="simo-dev:commit"):
    path = ft.telemetry_dir() / f"session-{session}.jsonl"
    with open(path, "a") if path.exists() else _new(path) as f:
        f.write(json.dumps({"ts": ts, "type": "step_start", "task": "", "session": session, "skill": skill}) + "\n")


def _new(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "w")


def _outside_fixture(env):
    _transcripts(env)
    with open(_project(env) / "sid1.jsonl", "a") as f:
        f.write(json.dumps(_usage("after", "2026-01-05T12:05:00Z", model="claude-sonnet-5", output_tokens=7)) + "\n")
    _events("T1", TASK_ROWS)  # sid1 owns 10:00 to its handoff at 11:00:30
    _orphan("sid1", "2026-01-05T12:00:00Z")
    _orphan("sid1", "2026-01-05T12:01:00Z")
    _orphan("sid1", "2026-01-05T12:02:00Z", "simo-dev:review-loop")
    _write(_project(env) / "sid7.jsonl", [_usage("x1", "2026-01-04T08:01:00Z", model="claude-sonnet-5",
                                                 output_tokens=5, cache_read_input_tokens=95),
                                          {"type": "custom-title", "customTitle": "Quick fix outside the flow"}])
    _orphan("sid7", "2026-01-04T08:00:00Z")
    _orphan("sid8", "2026-01-03T08:00:00Z")  # transcript pruned, no cache entry


def test_untracked_runs_count_only_tokens_outside_every_task(env):
    _outside_fixture(env)
    models = fd.build(ft.telemetry_dir())
    by_id = {m["id"]: m for m in models if m.get("untracked")}
    assert list(by_id) == ["session-sid8", "session-sid7", "session-sid1"]  # in the order of the invocations
    run = by_id["session-sid1"]
    assert run["total"]["output_tokens"] == 100 + 7  # before the task and after its handoff
    assert run["start"] == fd._ts("2026-01-05T12:00:00Z") and run["end"] == fd._ts("2026-01-05T12:05:00Z")
    assert models[0]["total"]["output_tokens"] + 107 == sum(  # nothing counted twice, nothing lost
        r["usage"].get("output_tokens", 0) for r in fd.read_session("sid1")["records"].values())
    assert by_id["session-sid7"]["title"] == "Quick fix outside the flow" and by_id["session-sid7"]["cost"] > 0
    assert by_id["session-sid8"]["source"] == "none" and by_id["session-sid8"]["cost"] == 0


def test_a_closed_task_without_handoff_frees_its_session_after_its_last_event(env):
    _write(_project(env) / "sid1.jsonl", [_usage("in", "2026-01-05T10:05:00Z", output_tokens=1),
                                          _usage("out", "2026-01-05T13:00:00Z", output_tokens=20)])
    _events("T1", [("2026-01-05T10:00:00Z", "step_start", {"skill": fd.INTAKE}),
                   ("2026-01-05T11:00:00Z", "outcome", {"result": "merged"})])
    _orphan("sid1", "2026-01-05T12:00:00Z")
    run = next(m for m in fd.build(ft.telemetry_dir()) if m.get("untracked"))
    assert run["total"]["output_tokens"] == 20


def test_the_overview_card_totals_the_untracked_runs(env):
    _outside_fixture(env)
    models = fd.build(ft.telemetry_dir(), now=NOW)
    runs = [m for m in models if m.get("untracked")]
    cards = fd.summary_cards(models)
    assert f"<b>≈{fd._usd(sum(m['cost'] for m in runs))}</b><span>$ est. untracked runs, 3 sessions</span>" in cards
    assert "untracked runs" not in fd.summary_cards([m for m in models if not m.get("untracked")])


def test_the_page_offers_an_auto_refresh_that_keeps_the_view(env):
    page = fd.render_html(_three_states(env), NOW)
    assert '<label class="muted" id="refresh-box" hidden' in page
    assert '<option value="0">off</option>' in page and '<option value="5">every 5 min</option>' in page
    script = fd.SCRIPT
    assert "location.reload()" in script and "document.visibilityState!=='visible'" in script
    assert "sessionStorage.setItem(KEY" in script and "sessionStorage.removeItem(KEY)" in script
    assert "localStorage.getItem(PREF)||'5'" in script  # every 5 min unless the viewer chose otherwise
    assert "if(document.activeElement===f&&f.value)return;" in script  # never while typing a filter


def _step(skill, start, minutes, out_tokens, agents=None):
    t0 = fd._ts(start)
    tokens = {**fd._zero(), "output_tokens": out_tokens}
    split = tu.estimate(tokens)
    return {"skill": skill, "start": t0, "end": t0 + timedelta(minutes=minutes), "tokens": tokens, "cost_split": split,
            "cost": tu.dollars(split), "agents": {}, "agent_costs": agents or {}}


def test_step_totals_sum_every_task_by_skill_costliest_first():
    a = _model("T1", "a", 0, "2026-01-05T10:00:00Z")
    b = _model("T2", "b", 0, "2026-01-06T10:00:00Z")
    reviewer = tu.estimate({**fd._zero(), "output_tokens": 500})
    a["steps"] = [_step("simo-dev:plan-write", "2026-01-05T10:00:00Z", 30, 2000, {"simo-dev:plan-reviewer": reviewer}),
                  _step("simo-dev:review-loop", "2026-01-05T10:30:00Z", 10, 100),
                  _step("simo-dev:review-loop", "2026-01-05T10:40:00Z", 5, 100)]
    b["steps"] = [_step("simo-dev:review-loop", "2026-01-06T10:00:00Z", 15, 300)]
    a["unattributed"], a["unattributed_cost"] = {**fd._zero(), "output_tokens": 50}, tu.estimate({**fd._zero(), "output_tokens": 50})
    for m in (a, b):
        m["cost"] = sum(s["cost"] for s in m["steps"]) + tu.dollars(m.get("unattributed_cost") or tu.zero_cost())
    b["cost"] += 1.0  # no step holds this dollar: an old per-skill attribution
    rows, missing, short = fd.step_totals([a, b])
    by = {r["skill"]: r for r in rows}
    assert [r["skill"] for r in rows] == ["simo-dev:plan-write", "simo-dev:review-loop", fd.BEFORE]
    assert by["simo-dev:review-loop"]["runs"] == 3 and by["simo-dev:review-loop"]["tasks"] == 2
    assert by["simo-dev:review-loop"]["tokens"]["output_tokens"] == 500 and by["simo-dev:review-loop"]["secs"] == 30 * 60
    assert by["simo-dev:plan-write"]["agents"] == pytest.approx(tu.dollars(reviewer))
    assert by[fd.BEFORE]["tokens"]["output_tokens"] == 50 and by[fd.BEFORE]["tasks"] == 1
    assert short == 1 and missing == pytest.approx(1.0)
    html_ = fd.steps_overview([a, b])
    assert '<table id="by-step">' in html_ and "<td class=\"wrapit\">plan-write</td>" in html_
    assert html_.index("plan-write") < html_.index(">review-loop<") < html_.index(fd.BEFORE)
    assert "1 task(s) with no per-step breakdown" in html_


def test_the_page_shows_cost_by_step_before_the_task_table(env):
    _transcripts(env)
    _events("T1", TASK_ROWS)
    out = env / "dash.html"
    assert fd.main(["--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert page.index("Cost by step, all tasks") < page.index("<h2>Tasks</h2>")
    assert "no per-step breakdown" not in page


def test_no_steps_no_cost_by_step_section():
    assert fd.steps_overview([_model("T1", "a", 10, "2026-01-05T10:00:00Z")]) == ""


def test_the_page_is_split_into_views_with_tabs(env):
    _outside_fixture(env)
    out = env / "dash.html"
    assert fd.main(["--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert ('<nav class="tabs" hidden><a href="#view-overview">Overview</a><a href="#view-charts">Charts</a>'
            '<a href="#view-tasks">Tasks (1 + 3 untracked)</a></nav>') in page
    overview, charts, tasks = (page.index(f'<section class="view" id="view-{k}">') for k in ("overview", "charts", "tasks"))
    assert overview < page.index('class="cards"') < page.index("Cost by step, all tasks") < charts
    assert charts < page.index('<div class="charts" data-days="0">') < tasks
    assert tasks < page.index('<table id="overview">') < page.index('id="task-T1"')
    assert "view-outside" not in page and "Out-of-task" not in page
    assert '<section class="view" id="view-overview" hidden' not in page  # complete without the script
    assert "function show(id)" in fd.SCRIPT and "view:view" in fd.SCRIPT  # the auto-refresh keeps the tab


def test_free_spans_are_the_complement_of_the_task_spans():
    t = fd._ts
    lo = datetime.min.replace(tzinfo=timezone.utc)
    taken = [(t("2026-01-05T10:00:00Z"), t("2026-01-05T11:00:00Z"), None),
             (t("2026-01-05T12:00:00Z"), None, t("2026-01-05T13:00:00Z")),  # a closed task capped at its last event
             (t("2026-01-05T12:30:00Z"), t("2026-01-05T12:45:00Z"), None)]  # overlapping, inside the previous one
    assert fd.free_spans(taken) == [(lo, t("2026-01-05T10:00:00Z")), (t("2026-01-05T11:00:00Z"), t("2026-01-05T12:00:00Z")),
                                    (t("2026-01-05T13:00:00Z"), None)]
    assert fd.free_spans([(t("2026-01-05T10:00:00Z"), None, None)]) == [(lo, t("2026-01-05T10:00:00Z"))]
    assert fd.free_spans([]) == [(lo, None)]


def test_sessions_with_flow_steps_but_no_task_are_untracked_runs(env):
    _outside_fixture(env)
    models = fd.build(ft.telemetry_dir(), now=NOW)
    by_id = {m["id"]: m for m in models}
    run = by_id["session-sid1"]
    assert run["status"] == "untracked" and run["result"] == "untracked" and not run["closed"]
    assert [s["skill"] for s in run["steps"]] == ["simo-dev:commit", "simo-dev:commit", "simo-dev:review-loop"]
    assert run["total"]["output_tokens"] == 107  # the same tokens as its out-of-task session row, never the task's
    assert by_id["T1"]["status"] == "done" and by_id["session-sid7"]["title"] == "Quick fix outside the flow"
    assert [m["id"] for m in fd._by_status(models)][-3:] == ["session-sid1", "session-sid7", "session-sid8"]  # after done


def test_untracked_runs_count_in_cost_by_step_but_not_in_the_task_cards(env):
    _outside_fixture(env)
    models = fd.build(ft.telemetry_dir(), now=NOW)
    cards = fd.summary_cards(models)
    assert "<b>1</b><span>tasks · 1 merged</span>" in cards
    rows, _, _ = fd.step_totals(models)
    commit = next(r for r in rows if r["skill"] == "simo-dev:commit")
    assert commit["runs"] == 4 and commit["tasks"] == 3  # two in the untracked sid1 run, one each in sid7 and sid8
    page = fd.render_html(models, NOW)
    assert '<span class="badge st-untracked" title="flow steps with no task ID' in page and '">untracked</span>' in page
    assert '<option value="untracked">untracked</option>' in page and "untracked:3" in page


def test_a_task_run_has_no_untracked_rows(env):
    _outside_fixture(env)
    models = fd.build(ft.telemetry_dir(), only="T1", now=NOW)
    assert [m["id"] for m in models] == ["T1"]


def test_a_session_adopted_without_intake_is_a_task_under_its_parent(env):
    _events("20260105T1000-sid1", TASK_ROWS)
    prompt = _project(env) / "sid9.jsonl"
    _write(prompt, [_user("2026-01-05T11:10:00Z", "follow-up work\nparent task: 20260105T1000-sid1"),
                    _usage("pre", "2026-01-05T11:15:00Z", model="claude-sonnet-5", output_tokens=40)])
    ft.record("step_start", "sid9", "", {"skill": "simo-dev:tdd"})  # before the adoption: a session-*.jsonl line
    child = ft.adopt("sid9", "simo-dev:commit", str(prompt))
    ft.record("step_start", "sid9", "", {"skill": "simo-dev:mr-followup"})
    models = fd.build(ft.telemetry_dir(), now=datetime.now(timezone.utc))
    by_id = {m["id"]: m for m in models}
    assert set(by_id) == {"20260105T1000-sid1", child}  # no untracked run of sid9
    m = by_id[child]
    assert not m.get("untracked") and m["status"] == "running"
    assert m["tree"]["parent"] == "20260105T1000-sid1"
    assert [s["skill"] for s in m["steps"]] == ["simo-dev:tdd", "simo-dev:commit", "simo-dev:mr-followup"]
    assert m["total"]["output_tokens"] == 40  # the work before the adopted step is the task's, not lost


def test_intake_path_is_the_last_intake_path_gate(env):
    _events("T1", TASK_ROWS + [
        ("2026-01-05T10:00:10Z", "gate", {"skill": fd.INTAKE, "gate": "intake_path", "answer": "fast"}),
        ("2026-01-05T10:01:00Z", "gate", {"skill": fd.INTAKE, "gate": "intake_path", "answer": "fallback"}),
    ])
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["intake"] == "fallback"


def test_a_task_without_intake_path_shows_a_dash(env):
    _events("T1", TASK_ROWS)
    model = fd.build_task("T1", fd.load_tasks(ft.telemetry_dir())[0][1])
    assert model["intake"] == ""
    out = env / "dash.html"
    assert fd.main(["--out", str(out), "--task", "T1"]) == 0
    page = out.read_text(encoding="utf-8")
    assert ">intake</th>" in page
    assert "<td>—</td></tr>" in page
    assert 'value="intake"' in page and 'data-intake="—"' in page


def test_session_rows_keep_the_header_column_count(env):
    _events("T1", TASK_ROWS)
    _events("T1", [("2026-01-05T10:30:00Z", "step_start", {"skill": "simo-dev:plan-execute"})], session="sid2")
    out = env / "dash.html"
    assert fd.main(["--out", str(out), "--task", "T1"]) == 0
    page = out.read_text(encoding="utf-8")
    head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
    sess = [r for r in page.split("<tr ")[1:] if r.startswith('class="sess"')]
    assert sess
    for row in sess:
        assert row.split("</tr>", 1)[0].count("<td") == head.count("<th")


def test_nice_rounds_a_scale_top_up():
    assert [fd._nice(v) for v in (0, 0.7, 1, 1.2, 2.1, 3, 7, 45, 1300)] == [1.0, 0.8, 1, 1.5, 2.5, 3, 8, 50, 1500]
    assert fd._nice_dur(50 * 60) == 3600 and fd._nice_dur(5.2 * 3600) == 6 * 3600


def test_charts_average_tokens_and_time_per_task_and_per_step():
    a = _model("T1", "a", 1000, "2026-01-05T10:00:00Z")
    b = _model("T2", "b", 3000, "2026-01-06T10:00:00Z")
    c = _model("T3", "c", 500, "2026-02-20T10:00:00Z")
    for m in (a, b, c):
        m["end"] = m["start"] + timedelta(hours=1)
    a["steps"] = [_step("simo-dev:plan-write", "2026-01-05T10:00:00Z", 30, 2000),
                  _step("simo-dev:review-loop", "2026-01-05T10:30:00Z", 10, 100)]
    b["steps"] = [_step("simo-dev:review-loop", "2026-01-06T10:00:00Z", 20, 300)]
    untracked = {**_model("session-x", "", 99999, "2026-01-05T10:00:00Z"), "untracked": True, "status": "untracked"}
    page = fd.chart_grid([a, b, c, untracked])
    assert page.count('<figure class="chart">') == 9
    # two tasks in the week of 01-05 (mean output 2000), none in the week of 01-12, one in the week of 02-16 (over 28 days: weekly)
    assert "week of 2026-01-05 · 2 task(s) · mean 3.0k tokens: input 0 · cache write 0 · cache read 1.0k · output 2.0k" in page
    assert "week of 2026-01-12 · 0 task(s)" in page and "week of 2026-02-16 · 1 task(s)" in page
    assert "Mean tokens per task, by week" in page
    assert "99999" not in page and "100.0k" not in page  # untracked runs are left out
    assert "review-loop · 2 run(s) in 2 task(s) · mean 200 tokens" in page
    assert "review-loop · 2 run(s) · mean 15m" in page
    assert "week of 2026-01-05 · 2 done task(s) · mean 1h00m: active 58m · human wait 2m" in page
    assert "1 round(s): 3 merged · 0 other outcome" in page


def test_charts_with_no_tasks_say_so():
    page = fd.chart_grid([])
    assert page.count("no data yet") == 6 and "<svg" not in page


def test_a_short_span_is_charted_by_day():
    a = _model("T1", "a", 1000, "2026-01-05T10:00:00Z")
    b = _model("T2", "b", 3000, "2026-01-07T23:00:00Z")
    unit, buckets = fd.time_buckets([a, b])
    assert unit == "day" and [(t.day, len(ms)) for t, ms in buckets] == [(5, 1), (6, 0), (7, 1)]
    assert "Mean tokens per task, by day" in fd.chart_grid([a, b])


def test_charts_render_one_grid_per_period_of_task_start():
    now = fd._ts("2026-03-01T00:00:00Z")
    old = _model("T1", "a", 1000, "2026-01-05T10:00:00Z")
    recent = _model("T2", "b", 3000, "2026-02-25T10:00:00Z")
    page = fd.charts([old, recent], now)
    grids = dict(part.split('"', 1) for part in page.split('<div class="charts" data-days="')[1:])
    assert list(grids) == ["0", "7", "30", "90"]
    assert '<div class="charts" data-days="0">' in page and '<div class="charts" data-days="7" hidden>' in page
    assert "2026-01-05" in grids["0"] and "2026-01-05" in grids["90"]
    assert "2026-01-05" not in grids["7"] and "2026-01-05" not in grids["30"] and "2026-02-25" in grids["7"]
    assert '<select id="chart-period" hidden' in page and '<option value="30">started in the last 30 days</option>' in page
