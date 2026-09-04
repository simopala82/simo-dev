#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_mr_followup_remind.sh."""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "mr_followup_remind.py"


def _reminds(cmd: str) -> bool:
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"session_id": "sess-x", "cwd": "/tmp", "tool_input": {"command": cmd}}),
        text=True, capture_output=True, check=True,
    )
    return "additionalContext" in result.stdout


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
