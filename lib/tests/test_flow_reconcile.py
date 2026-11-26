#!/usr/bin/env python3
"""Tests for flow_telemetry.reconcile: closing tasks whose MR/PR was merged outside the post-merge step."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import flow_telemetry as ft  # noqa: E402

GL = "https://gitlab.example.com/group/sub/app/-/merge_requests/12"
GL_OTHER = "https://gitlab.example.com/group/sub/app/-/merge_requests/99"
GH = "https://github.com/owner/repo/pull/7"
SID = "sess0001-aaaa"
TID = "20261110T0900-sess0001"
NOW = datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp()
MERGED = '{"state": "merged", "merged_at": "2026-11-11T08:30:00.123+01:00", "source_branch": "feat/x", "title": "a\tb"}'


@pytest.fixture
def env(tmp_path, monkeypatch):
    tel = tmp_path / "dev-flow"
    monkeypatch.setenv("SIMO_DEV_TELEMETRY_DIR", str(tel))
    monkeypatch.setenv("SIMO_DEV_FLOW_ACTIVE_DIR", str(tmp_path / "active"))
    monkeypatch.setenv("SIMO_DEV_JOURNAL", str(tmp_path / "journal.md"))
    monkeypatch.setenv("HOME", str(tmp_path))
    tel.mkdir()
    return tmp_path


def _task(env, tid, events):
    with open(env / "dev-flow" / f"{tid}.jsonl", "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps({"task": tid, **e}) + "\n")


def _transcript(env, sid, links):
    d = env / ".claude" / "projects" / "-proj"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{sid}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "timestamp": "2026-11-10T09:00:00Z", "message": {"content": "hi"}}) + "\n")
        for ts, url in links:
            f.write(json.dumps({"type": "pr-link", "sessionId": sid, "prUrl": url, "timestamp": ts}) + "\n")


def _open_task(env, tid=TID, sid=SID, links=(("2026-11-10T10:00:00.5Z", GL),), branch="feat/x"):
    _task(env, tid, [
        {"ts": "2026-11-10T09:00:00Z", "type": "step_start", "session": sid, "skill": "simo-dev:task-intake"},
        {"ts": "2026-11-10T09:10:00Z", "type": "task_bound", "session": sid, "branch": branch, "repo": "app"},
        {"ts": "2026-11-10T10:00:00Z", "type": "gate", "session": sid, "skill": "simo-dev:commit", "gate": "mr_created",
         "answer": "created"},
    ])
    _transcript(env, sid, links)
    return tid


class Forge:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = self.replies.get(cmd[0])
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(cmd, 0 if out is not None else 1, out or "", "")


def _outcomes(env, tid=TID):
    return [e for e in ft.read_events(env / "dev-flow" / f"{tid}.jsonl") if e.get("type") == "outcome"]


def test_merged_gitlab_mr_gets_an_outcome_dated_at_the_merge(env):
    _open_task(env)
    forge = Forge({"glab": MERGED})  # a raw tab inside a string: glab can emit it, json.loads needs strict=False
    assert ft.reconcile(run=forge, now=NOW) == [(TID, "merged", GL)]
    assert forge.calls[0] == ["glab", "api", "--hostname", "gitlab.example.com",
                              "projects/group%2Fsub%2Fapp/merge_requests/12"]
    [out] = _outcomes(env)
    assert out["ts"] == "2026-11-11T07:30:00Z" and out["result"] == "merged" and out["mr"] == GL
    assert out["session"] == "" and "reconcile" in out["notes"]


def test_merged_github_pr_gets_an_outcome(env):
    _open_task(env, links=(("2026-11-10T10:00:01Z", GH),))
    forge = Forge({"gh": '{"state": "MERGED", "mergedAt": "2026-11-11T10:00:00Z", "headRefName": "feat/x"}'})
    assert ft.reconcile(run=forge, now=NOW) == [(TID, "merged", GH)]
    assert forge.calls[0] == ["gh", "pr", "view", GH, "--json", "state,mergedAt,headRefName"]
    assert _outcomes(env)[0]["ts"] == "2026-11-11T10:00:00Z"


@pytest.mark.parametrize("reply", [
    '{"state": "opened"}', '{"state": "closed", "closed_at": "2026-11-11T00:00:00Z"}', None, "not json",
    '{"state": "merged"}', '{"state": "merged", "merged_at": null}',
    '{"state": "merged", "merged_at": "2026-11-11T00:00:00Z", "source_branch": "other"}',
    '{"state": "merged", "merged_at": "2026-11-11T23:00:00Z", "source_branch": "feat/x"}',  # inside the grace period
    FileNotFoundError("glab"), subprocess.TimeoutExpired("glab", 1), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
])
def test_task_stays_open_unless_merged_from_its_branch_past_the_grace_period(env, reply):
    _open_task(env)
    assert ft.reconcile(run=Forge({"glab": reply}), now=NOW) == []
    assert _outcomes(env) == []


def test_skips_closed_tasks_and_tasks_without_mr_gate(env):
    _open_task(env)
    _task(env, TID, [{"ts": "2026-11-11T00:00:00Z", "type": "outcome", "session": "x", "result": "merged"}])
    _task(env, "20261110T0800-other001", [{"ts": "2026-11-10T08:00:00Z", "type": "step_start", "session": SID,
                                           "skill": "simo-dev:task-intake"}])
    forge = Forge({})
    assert ft.reconcile(run=forge, now=NOW) == [] and forge.calls == []


def test_picks_the_link_nearest_to_the_gate_not_the_last_one(env):
    _open_task(env, links=(("2026-11-10T08:00:00Z", GL_OTHER), ("2026-11-10T10:00:03Z", GL),
                           ("2026-11-10T12:00:00Z", GL_OTHER), ("2026-11-10T13:00:00Z", GL_OTHER)))
    forge = Forge({"glab": MERGED})
    assert ft.reconcile(run=forge, now=NOW) == [(TID, "merged", GL)]
    assert forge.calls[0][4].endswith("/merge_requests/12")


def test_reads_the_session_of_the_last_mr_gate(env):
    _open_task(env, links=(("2026-11-10T10:00:00Z", GL_OTHER),))
    _task(env, TID, [{"ts": "2026-11-11T01:00:00Z", "type": "gate", "session": "sess0002-bbbb", "gate": "mr_created",
                      "answer": "created"}])
    _transcript(env, "sess0002-bbbb", [("2026-11-11T01:00:02Z", GL)])
    assert ft.task_mr_url(ft.read_events(env / "dev-flow" / f"{TID}.jsonl")) == GL


@pytest.mark.parametrize("links", [(), (("2026-11-10T10:11:00Z", GL),), (("2026-11-10T09:49:00Z", GL),)])
def test_no_link_near_the_gate_means_no_forge_call(env, links):
    _open_task(env, links=links)
    forge = Forge({})
    assert ft.reconcile(run=forge, now=NOW) == [] and forge.calls == []


def test_no_open_task_writes_nothing(env):
    _task(env, TID, [{"ts": "2026-11-10T09:00:00Z", "type": "outcome", "session": SID, "result": "merged"}])
    assert ft.reconcile(run=Forge({}), now=NOW) == []
    assert sorted(p.name for p in (env / "dev-flow").iterdir()) == [f"{TID}.jsonl"]


@pytest.mark.skipif(ft.fcntl is None, reason="no flock on this platform")
def test_a_run_finding_the_lock_held_skips(env):
    _open_task(env)
    forge = Forge({"glab": MERGED})
    with open(env / "dev-flow" / f"{ft.RECONCILE_CACHE}.lock", "a") as held:
        ft.fcntl.flock(held, ft.fcntl.LOCK_EX)
        assert ft.reconcile(run=forge, now=NOW) == [] and forge.calls == []
    assert ft.reconcile(run=forge, now=NOW) == [(TID, "merged", GL)]


@pytest.mark.parametrize("url,ok", [(GL + "/diffs", True), (GL + "#note_1", True), (GH + "/files", True),
                                    ("https://gitlab.example.com/a/-/issues/3", False),
                                    ("https://ghe.example.com/o/r/pull/7", False)])
def test_url_shapes(url, ok):
    forge = Forge({"glab": '{"state": "opened"}', "gh": '{"state": "OPEN"}'})
    assert (ft.forge_state(url, forge) is not None) is ok


def test_a_task_checked_recently_is_not_queried_again(env):
    _open_task(env)
    forge = Forge({"glab": '{"state": "opened"}'})
    ft.reconcile(run=forge, now=NOW)
    ft.reconcile(run=forge, now=NOW + ft.RECONCILE_EVERY_S - 1)
    assert len(forge.calls) == 1
    forge.replies["glab"] = MERGED
    assert ft.reconcile(run=forge, now=NOW + ft.RECONCILE_EVERY_S + 1) == [(TID, "merged", GL)]
    assert json.loads((env / "dev-flow" / ft.RECONCILE_CACHE).read_text()) == {}


def test_a_task_closed_meanwhile_gets_no_second_outcome(env, monkeypatch):
    _open_task(env)
    real = ft.read_events

    def late_outcome(path):
        if Path(path).stem == TID and not getattr(late_outcome, "done", False):
            late_outcome.done = True  # the first read is the scan; the post-merge step lands right after it
            events = real(path)
            _task(env, TID, [{"ts": "2026-11-11T07:31:00Z", "type": "outcome", "session": SID, "result": "merged"}])
            return events
        return real(path)

    monkeypatch.setattr(ft, "read_events", late_outcome)
    assert ft.reconcile(run=Forge({"glab": MERGED}), now=NOW) == []
    assert len(_outcomes(env)) == 1


def test_a_failing_task_neither_stops_the_others_nor_loses_the_cache(env, capsys):
    long_url = "https://gitlab.example.com/" + "g/" * 60 + "app/-/merge_requests/12"
    _open_task(env, tid="20261110T0800-sess0003", sid="sess0003-cccc", links=(("2026-11-10T10:00:00Z", long_url),))
    _open_task(env)
    (env / "dev-flow" / f"{TID}.jsonl").chmod(0o400)  # appending the outcome fails
    got = ft.reconcile(run=Forge({"glab": MERGED}), now=NOW)
    (env / "dev-flow" / f"{TID}.jsonl").chmod(0o600)
    assert got == [("20261110T0800-sess0003", "merged", long_url)]
    assert "mr" not in _outcomes(env, "20261110T0800-sess0003")[0]  # over SHORT_MAX: left out, not refused
    assert f"reconcile {TID}" in capsys.readouterr().err
    assert TID in json.loads((env / "dev-flow" / ft.RECONCILE_CACHE).read_text())


def test_cli_reconcile_reports_and_renders_the_journal(env, monkeypatch, capsys):
    _open_task(env)
    monkeypatch.setattr(ft.subprocess, "run", Forge({"glab": MERGED}))
    assert ft.main(["reconcile"]) == 0
    out = capsys.readouterr().out
    assert f"reconciled: {TID} merged {GL}" in out and "reconcile: 1 task(s) closed" in out
    assert "result: merged" in (env / "journal.md").read_text()


@pytest.mark.parametrize("flag,closed", [([], True), (["--no-reconcile"], False), (["--task", TID], False)])
def test_dashboard_reconciles_before_a_full_build(env, monkeypatch, flag, closed):
    import flow_dashboard as fd
    _open_task(env)
    monkeypatch.setattr(ft.subprocess, "run", Forge({"glab": MERGED}))
    out = env / "dash.html"
    assert fd.main(["--out", str(out), *flag]) == 0
    assert bool(_outcomes(env)) is closed
    assert ("done: outcome merged" in out.read_text()) is closed
    assert (env / "journal.md").exists() is closed
