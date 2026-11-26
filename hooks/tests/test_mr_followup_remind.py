#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_mr_followup_remind.sh."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "mr_followup_remind.py"


def _reminds(cmd: str, active: bool = True) -> bool:
    with tempfile.TemporaryDirectory(prefix="flow-active-") as d:
        active_dir = Path(d)
        if active:
            (active_dir / "sess-x").write_text("1\n")
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"session_id": "sess-x", "cwd": "/tmp", "tool_input": {"command": cmd}}),
            text=True, capture_output=True, check=True,
            env={**os.environ, "SIMO_DEV_FLOW_ACTIVE_DIR": str(active_dir)},
        )
    return "additionalContext" in result.stdout


def test_push_outside_the_flow_stays_silent():
    assert not _reminds("git push -u origin feat/x", active=False)


def test_bare_push_reminds():
    assert _reminds("git push")


def test_push_with_upstream_reminds():
    assert _reminds("git push -u origin feat/x")


def test_dash_c_push_reminds():
    assert _reminds("git -C /tmp/wt push -u origin b")


def test_dash_c_config_push_reminds():
    assert _reminds("git -c user.name=x push")


def test_rtk_wrapped_push_reminds():
    assert _reminds("rtk git push")


def test_chained_push_reminds():
    assert _reminds("git add a.txt && git push")


def test_ls_stays_silent():
    assert not _reminds("ls")


def test_git_status_stays_silent():
    assert not _reminds("git status")


def test_git_rev_parse_stays_silent():
    assert not _reminds("git rev-parse --show-toplevel")


def test_git_commit_stays_silent():
    assert not _reminds("git commit -m x")


def test_glab_mr_view_stays_silent():
    assert not _reminds("glab mr view")


def test_prose_mentioning_push_stays_silent():
    assert not _reminds("echo run git push later >> notes.txt")


def test_grep_for_push_stays_silent():
    assert not _reminds("rg 'git push' docs/")


def test_subcommand_prefix_stays_silent():
    assert not _reminds("git pushed-nothing")


def test_newline_separated_push_reminds():
    assert _reminds("git add a.txt\ngit push -u origin b")


def _merge_reminder(cmd: str, events: list[dict], session: str = "sess-x") -> str:
    """The hook's additionalContext for `cmd`, with `events` as the only task file."""
    with tempfile.TemporaryDirectory(prefix="flow-tel-") as d:
        if events:
            (Path(d) / "20261109T1000-sessx.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"session_id": session, "cwd": "/tmp", "tool_input": {"command": cmd}}),
            text=True, capture_output=True, check=True,
            env={**os.environ, "SIMO_DEV_TELEMETRY_DIR": d, "SIMO_DEV_FLOW_ACTIVE_DIR": str(Path(d) / "active")},
        )
    if not result.stdout:
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


OPEN_TASK = [
    {"ts": "2026-11-09T10:00:00Z", "type": "step_start", "session": "sess-x", "skill": "simo-dev:commit"},
    {"ts": "2026-11-09T10:01:00Z", "type": "task_bound", "session": "sess-x", "branch": "feat/a", "repo": "r"},
]


def test_a_merge_with_an_open_task_reminds_to_close_it():
    for cmd in ("glab mr merge 12 --squash --yes", "cd /tmp/r && gh pr merge 249 --squash", "sleep 5\nrtk gh pr merge 3",
                "glab api -X PUT projects/:id/merge_requests/12/merge -f sha=abc",
                'V=$(cd "/tmp/r" && glab api -X PUT "projects/:id/merge_requests/12/merge" -f sha=abc -F squash=true) || exit 1',
                "glab api -X PUT projects/:id/merge_requests/12/merge -F auto_merge=true -f sha=abc",
                "glab api -X PUT \\\n  projects/:id/merge_requests/12/merge -f sha=abc"):
        got = _merge_reminder(cmd, OPEN_TASK)
        assert "step 8.5" in got and "20261109T1000-sessx (feat/a)" in got, cmd


def test_a_merge_with_no_open_task_stays_silent():
    closed = OPEN_TASK + [{"ts": "2026-11-09T11:00:00Z", "type": "outcome", "session": "sess-x", "result": "merged"}]
    assert _merge_reminder("glab mr merge 12", closed) == ""
    assert _merge_reminder("glab mr merge 12", []) == ""
    assert _merge_reminder("glab mr merge 12", OPEN_TASK, session="another") == ""


def test_a_merge_only_mentioned_stays_silent():
    for cmd in ("echo run glab mr merge later", "rg 'gh pr merge' docs/", "glab mr view 12", "gh pr merged-list",
                "glab api projects/:id/merge_requests/12", "glab api projects/:id/merge_requests/12/merge_ref",
                "glab api projects/:id/merge_requests/12/merge-status", "rg 'glab api .*merge_requests/1/merge' docs/"):
        assert _merge_reminder(cmd, OPEN_TASK) == "", cmd


def test_a_merge_path_inside_another_endpoints_argument_is_an_observed_false_positive():
    cmd = "glab api projects/:id/merge_requests/12/discussions -f body='see merge_requests/3/merge'"
    assert "step 8.5" in _merge_reminder(cmd, OPEN_TASK)


def test_a_push_and_a_merge_in_one_call_give_one_output_with_both():
    with tempfile.TemporaryDirectory(prefix="flow-tel-") as d:
        (Path(d) / "active").mkdir()
        (Path(d) / "active" / "sess-x").write_text("1\n")
        (Path(d) / "20261109T1000-sessx.jsonl").write_text("".join(json.dumps(e) + "\n" for e in OPEN_TASK))
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"session_id": "sess-x", "cwd": "/tmp",
                              "tool_input": {"command": "git push --force-with-lease origin b; sleep 5; glab mr merge 491"}}),
            text=True, capture_output=True, check=True,
            env={**os.environ, "SIMO_DEV_TELEMETRY_DIR": d, "SIMO_DEV_FLOW_ACTIVE_DIR": str(Path(d) / "active")},
        )
    lines = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"].split("\n")
    assert len(lines) == 2 and "Run simo-dev:mr-followup" in lines[0] and "step 8.5" in lines[1]


def test_more_than_five_open_tasks_show_the_newest_and_a_count():
    with tempfile.TemporaryDirectory(prefix="flow-tel-") as d:
        for i in range(7):
            (Path(d) / f"20261109T100{i}-sessx.jsonl").write_text(json.dumps(OPEN_TASK[0]) + "\n")
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"session_id": "sess-x", "cwd": "/tmp", "tool_input": {"command": "gh pr merge 1"}}),
            text=True, capture_output=True, check=True,
            env={**os.environ, "SIMO_DEV_TELEMETRY_DIR": d, "SIMO_DEV_FLOW_ACTIVE_DIR": str(Path(d) / "active")},
        )
    got = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "20261109T1006-sessx" in got and "20261109T1001-sessx" not in got and "(+2 older)" in got
