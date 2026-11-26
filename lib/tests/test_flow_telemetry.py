#!/usr/bin/env python3
"""Tests for lib/flow_telemetry.py."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import flow_state  # noqa: E402
import flow_telemetry as ft  # noqa: E402

SCRIPT = LIB / "flow_telemetry.py"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    tel = tmp_path / "dev-flow"
    act = tmp_path / "active"
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(tel))
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(act))
    return tel, act


def test_new_task_id_shape():
    now = datetime(2026, 11, 9, 17, 12, tzinfo=timezone.utc)
    assert ft.new_task_id("b78436ac-4fce-4bc1", now) == "20261109T1712-b78436ac"
    assert ft.TASK_ID_RE.match(ft.new_task_id("s"))


@pytest.mark.parametrize("etype,fields,msg", [
    ("nope", {}, "unknown event type"),
    ("gate", {"gate": "plan", "answer": "a", "prompt": "x"}, "not allowed"),
    ("gate", {"gate": "plan"}, "missing"),
    ("gate", {"gate": "plan", "answer": "x" * 121}, "longer than"),
    ("gate", {"gate": "plan", "answer": "a\nb"}, "one line"),
    ("outcome", {"result": "merged", "notes": "y" * 601}, "longer than"),
])
def test_build_event_refuses_bad_fields(etype, fields, msg):
    with pytest.raises(ValueError, match=msg):
        ft.build_event(etype, "t", "s", fields)


def test_record_routes_by_active_task(dirs, tmp_path):
    tel, _ = dirs
    assert ft.record("step_start", "s1", str(tmp_path), {"skill": "simo-dev:commit"}) == tel / "session-s1.jsonl"
    assert ft.record("gate", "s1", str(tmp_path), {"gate": "g", "answer": "a"}) is None
    assert ft.record("step_start", "s1", str(tmp_path), {"skill": "other:x"}) == tel / "session-s1.jsonl"
    tid = ft.start("s1")
    path = ft.record("gate", "s1", str(tmp_path), {"gate": "plan", "answer": "proceed"})
    assert path == tel / f"{tid}.jsonl"
    events = ft.read_events(path)
    assert [e["type"] for e in events] == ["step_start", "gate"]
    assert events[0]["skill"] == "simo-dev:task-intake"
    assert events[1]["task"] == tid and events[1]["session"] == "s1"


def test_start_stores_the_id_and_reuses_a_given_one(dirs):
    tid = ft.start("s2")
    assert flow_state.session_task("s2") == tid
    assert ft.start("s3", task_id=tid) == tid
    assert flow_state.session_task("s3") == tid
    with pytest.raises(ValueError):
        ft.start("s4", task_id="../evil")


@pytest.mark.parametrize("name", ["", "../x", "a/b", ".hidden"])
def test_unsafe_file_names_are_refused(dirs, name):
    with pytest.raises(ValueError, match="unsafe file name"):
        ft.task_file(name)
    with pytest.raises(ValueError, match="unsafe file name"):
        ft.session_file(name)


def test_read_events_skips_bad_lines(dirs):
    tel, _ = dirs
    tel.mkdir(parents=True)
    f = tel / "x.jsonl"
    f.write_text('{"type": "gate"}\nnot json\n[1]\n')
    assert ft.read_events(f) == [{"type": "gate"}]


@pytest.mark.parametrize("sep", [chr(0x85), chr(0x2028), chr(0x2029)])
def test_read_events_keeps_an_event_with_a_raw_unicode_line_separator(dirs, sep):
    # append_event writes ensure_ascii=False, so these stay raw inside the JSON line.
    tel, _ = dirs
    f = tel / "t.jsonl"
    event = ft.build_event("outcome", "t", "s", {"result": "merged", "notes": f"a{sep}b"})
    ft.append_event(f, event)
    assert sep in f.read_text(encoding="utf-8")
    assert ft.read_events(f) == [event]


def test_prune_keeps_finished_tasks_and_recent_files(dirs):
    tel, _ = dirs
    tel.mkdir(parents=True)
    (tel / "active").mkdir()
    stale, done, sess, new = (tel / "stale.jsonl", tel / "done.jsonl",
                              tel / "session-s1.jsonl", tel / "new.jsonl")
    stale.write_text('{"type": "step_start"}\n')
    done.write_text('{"type": "outcome"}\n')
    sess.write_text('{"type": "outcome"}\n')
    new.write_text("{}\n")
    ninety_one = time.time() - 91 * 24 * 3600
    for path in (stale, done, sess):
        os.utime(path, (ninety_one, ninety_one))
    ft.prune(tel)
    assert not stale.exists() and not sess.exists()
    assert done.exists() and new.exists() and (tel / "active").is_dir()


def test_cli_event_current_and_errors(dirs, tmp_path):
    tel, act = dirs
    env = {**os.environ, "SIMO_DEV_TELEMETRY_DIR": str(tel), "SIMO_DEV_FLOW_ACTIVE_DIR": str(act)}

    def run(*a):
        return subprocess.run([sys.executable, str(SCRIPT), *a], env=env, capture_output=True, text=True)

    assert run("current", "--session", "s9").returncode == 1
    out = run("event", "--type", "gate", "--session", "s9", "--field", "gate=g", "--field", "answer=a")
    assert out.returncode == 0 and out.stdout.strip() == "event: skipped (no active task)"
    started = run("start", "--session", "s9")
    tid = started.stdout.strip().removeprefix("task: ")
    assert started.returncode == 0 and ft.TASK_ID_RE.match(tid)
    assert run("current", "--session", "s9").stdout.strip() == tid
    ok = run("event", "--type", "retry", "--session", "s9", "--field", "skill=simo-dev:review-loop",
             "--field", "iteration=2")
    assert ok.returncode == 0
    last = json.loads((tel / f"{tid}.jsonl").read_text().splitlines()[-1])
    assert last["iteration"] == 2
    bad = run("event", "--type", "gate", "--session", "s9", "--field", "gate=g", "--field", "prompt=x")
    assert bad.returncode == 2 and "not allowed" in bad.stderr
    assert run("close", "--session", "s9").stdout.strip() == "task: closed"
    assert json.loads((tel / f"{tid}.jsonl").read_text().splitlines()[-1])["gate"] == "handoff"
    assert run("current", "--session", "s9").returncode == 1
    assert run("close", "--session", "nope").returncode == 1


def test_cli_event_refuses_a_task_that_is_not_a_task_id(dirs):
    tel, act = dirs
    env = {**os.environ, "SIMO_DEV_TELEMETRY_DIR": str(tel), "SIMO_DEV_FLOW_ACTIVE_DIR": str(act)}
    r = subprocess.run([sys.executable, str(SCRIPT), "event", "--type", "gate", "--task", "session-s1",
                        "--field", "gate=g", "--field", "answer=a"], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "not a task id" in r.stderr
    assert not tel.exists() or not any(tel.glob("*.jsonl"))


def test_close_clears_the_task_even_when_the_event_cannot_be_written(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(tmp_path / "dev-flow"))
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(tmp_path / "active"))
    ft.start("s5")
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(blocker / "dev-flow"))
    assert ft.main(["close", "--session", "s5"]) == 0
    assert flow_state.session_task("s5") == ""


def test_start_refuses_a_task_id_with_a_trailing_newline(dirs):
    with pytest.raises(ValueError, match="unsafe task id"):
        ft.start("s1", task_id="20261110T1000-parent00\n")


def test_start_records_the_parent_task_link(dirs):
    tel, _ = dirs
    tid = ft.start("s1", task_id="20261110T1000-parent00", parent_task="20261109T0900-parentab")
    assert tid == "20261110T1000-parent00"
    events = ft.read_events(tel / f"{tid}.jsonl")
    assert [e["type"] for e in events] == ["step_start", "task_link"]
    assert events[1]["parent"] == "20261109T0900-parentab"


@pytest.mark.parametrize("parent", ["not a task id", "../x", "20261110T1000-parent00", "20261109T0900-parentab\n"])
def test_start_ignores_a_bad_or_self_parent(dirs, parent, capsys):
    tel, _ = dirs
    assert ft.start("s1", task_id="20261110T1000-parent00", parent_task=parent) == "20261110T1000-parent00"
    events = ft.read_events(tel / "20261110T1000-parent00.jsonl")
    assert [e["type"] for e in events] == ["step_start"]
    assert "parent task ignored" in capsys.readouterr().err


def test_start_cli_takes_parent_task(dirs):
    tel, _ = dirs
    out = subprocess.run([sys.executable, str(SCRIPT), "start", "--session", "s1", "--task-id",
                          "20261110T1000-parent00", "--parent-task", "20261109T0900-parentab"],
                         capture_output=True, text=True, env={**os.environ})
    assert out.returncode == 0, out.stderr
    events = ft.read_events(tel / "20261110T1000-parent00.jsonl")
    assert events[-1] == {**events[-1], "type": "task_link", "parent": "20261109T0900-parentab"}


def test_render_task_names_the_parent_task():
    events = [{"ts": "2026-01-05T10:00:00Z", "type": "step_start", "skill": "simo-dev:task-intake"},
              {"ts": "2026-01-05T10:00:00Z", "type": "task_link", "parent": "20260104T0900-parentab"}]
    assert "- parent task: 20260104T0900-parentab" in ft.render_task("T1", events).splitlines()
    assert not any(line.startswith("- parent task") for line in ft.render_task("T1", events[:1]).splitlines())


def _transcript(tmp_path, *texts):
    path = tmp_path / "t.jsonl"
    rows = [{"type": "user", "isMeta": True, "timestamp": "2026-11-11T08:00:00.250Z",
             "message": {"content": "task id: 20990101T0000-meta"}}]
    rows += [{"type": "user", "message": {"content": [{"type": "text", "text": t}]}} for t in texts]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


@pytest.mark.parametrize("text,want", [
    ("do x\nparent task: 20261111T0930-abcd1234\n", ("", "20261111T0930-abcd1234")),
    ("do x\n- `task id: 20261111T0930-abcd1234`\nparent task: `20261110T0800-ffff0000`", ("20261111T0930-abcd1234", "20261110T0800-ffff0000")),
    ("parent task: not-an-id\nmentions parent task: 20261111T0930-abcd1234 inline", ("", "")),
    ("no lines at all", ("", "")),
    ("**Task id:** 20261111T0930-abcd1234\nParent task: 20261111T0930-abcd1234 (merged)", ("20261111T0930-abcd1234", "")),
])
def test_prompt_ids_reads_only_the_first_prompt_lines(tmp_path, text, want):
    assert ft.prompt_ids(_transcript(tmp_path, text, "parent task: 20261231T2359-later000")) == want


def test_prompt_ids_with_no_transcript():
    assert ft.prompt_ids("") == ("", "")
    assert ft.prompt_ids("/nonexistent/t.jsonl") == ("", "")


def test_adopt_mints_a_telemetry_only_task_linked_to_the_parent(dirs, tmp_path):
    tel, act = dirs
    tr = _transcript(tmp_path, "follow-up\nparent task: 20261111T0930-abcd1234")
    assert ft.adopt("s9", "simo-dev:guide", tr) == ""
    tid = ft.adopt("s9", "simo-dev:commit", tr)
    assert ft.TASK_ID_RE.fullmatch(tid) and tid.endswith("-s9")
    assert ft.current_task("s9", "") == tid
    assert not flow_state.is_active("s9", str(tmp_path)) and not (act / "s9").exists()
    events = ft.read_events(tel / f"{tid}.jsonl")
    assert [(e["type"], e.get("skill"), e.get("gate"), e.get("parent")) for e in events] == [
        ("gate", "simo-dev:commit", "adopted", None),
        ("step_start", "simo-dev:commit", None, None),
        ("task_link", None, None, "20261111T0930-abcd1234"),
    ]
    assert events[0]["ts"] == "2026-11-11T08:00:00Z"  # the session's first record: earlier work joins the task
    assert events[1]["ts"] > events[0]["ts"]
    assert oct((tel / "adopted" / "s9").stat().st_mode & 0o777) == "0o600"


def test_a_session_that_had_a_task_is_never_adopted(dirs, tmp_path):
    tel, _ = dirs
    first = ft.start("s9")
    assert ft.main(["close", "--session", "s9"]) == 0
    before = sorted(p.name for p in tel.glob("*.jsonl"))
    assert ft.adopt("s9", "simo-dev:mr-followup", _transcript(tmp_path, f"task id: {first}")) == ""
    assert sorted(p.name for p in tel.glob("*.jsonl")) == before and ft.current_task("s9", "") == ""


def test_a_finished_task_named_by_the_prompt_is_not_joined(dirs, tmp_path):
    tel, _ = dirs
    ft.append_event(ft.task_file("20261111T0930-abcd1234"),
                    ft.build_event("outcome", "20261111T0930-abcd1234", "other", {"result": "merged"}))
    tid = ft.adopt("s9", "simo-dev:commit", _transcript(tmp_path, "task id: 20261111T0930-abcd1234"))
    assert tid and tid != "20261111T0930-abcd1234"
    assert [e["type"] for e in ft.read_events(tel / "20261111T0930-abcd1234.jsonl")] == ["outcome"]
    links = [e["parent"] for e in ft.read_events(tel / f"{tid}.jsonl") if e["type"] == "task_link"]
    assert links == ["20261111T0930-abcd1234"]  # with no parent task: line, the merged task is the parent


def test_adopt_moves_the_sessions_earlier_lines_into_the_task(dirs, tmp_path):
    tel, _ = dirs
    ft.record("step_start", "s9", "", {"skill": "simo-dev:tdd"})
    tid = ft.adopt("s9", "simo-dev:commit", _transcript(tmp_path, "work"))
    assert not (tel / "session-s9.jsonl").exists()
    events = ft.read_events(tel / f"{tid}.jsonl")
    assert [e.get("skill") for e in events if e["type"] == "step_start"] == ["simo-dev:tdd", "simo-dev:commit"]
    assert {e["task"] for e in events} == {tid}


def test_an_intake_after_an_adoption_releases_the_adopted_task(dirs, tmp_path):
    tel, _ = dirs
    adopted = ft.adopt("s9", "simo-dev:review-loop", _transcript(tmp_path, "task id: 20261111T0930-abcd1234"))
    tid = ft.start("s9")
    assert tid != adopted and ft.current_task("s9", "") == tid
    assert not (tel / "adopted" / "s9").exists()
    assert ft.read_events(tel / f"{adopted}.jsonl")[-1]["gate"] == "handoff"


def test_adopt_joins_the_task_named_by_a_handoff_prompt(dirs, tmp_path):
    tel, _ = dirs
    tr = _transcript(tmp_path, "task id: 20261111T0930-abcd1234\nparent task: 20261111T0930-abcd1234")
    assert ft.adopt("s9", "simo-dev:review-loop", tr) == "20261111T0930-abcd1234"
    types = [e["type"] for e in ft.read_events(tel / "20261111T0930-abcd1234.jsonl")]
    assert types == ["gate", "step_start"]  # its own ID as parent is no link


def test_close_clears_an_adopted_task_and_writes_the_handoff(dirs, tmp_path):
    tel, act = dirs
    tid = ft.adopt("s9", "simo-dev:commit")
    assert ft.main(["close", "--session", "s9"]) == 0
    assert ft.current_task("s9", "") == ""
    assert ft.read_events(tel / f"{tid}.jsonl")[-1]["gate"] == "handoff"
    assert not (act / "s9").exists()
    assert ft.main(["close", "--session", "s9"]) == 1


def test_a_flag_task_wins_over_an_adopted_one(dirs):
    ft.adopt("s9", "simo-dev:commit")
    tid = ft.start("s9")
    assert ft.current_task("s9", "") == tid


def test_prune_drops_old_adopted_ids(dirs):
    tel, _ = dirs
    ft.adopt("old", "simo-dev:commit")
    ft.adopt("new", "simo-dev:commit")
    past = time.time() - ft.PRUNE_AFTER_S - 10
    os.utime(tel / "adopted" / "old", (past, past))
    ft.prune(tel)
    assert sorted(p.name for p in (tel / "adopted").iterdir()) == ["new"]


def test_close_with_task_guard_leaves_a_session_that_moved_on(dirs, capsys):
    old = ft.start("s9", task_id="20261115T1000-s9s9s9s9")
    new = ft.start("s9", task_id="20261115T1100-s9s9s9s9")
    assert ft.main(["close", "--session", "s9", "--task", old]) == 1
    assert "not closed" in capsys.readouterr().out
    assert ft.current_task("s9", "") == new
    assert ft.main(["close", "--session", "s9", "--task", new]) == 0
    assert ft.current_task("s9", "") == ""
