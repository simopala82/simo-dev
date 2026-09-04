#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_review_loop_warn.sh."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "review_loop_warn.py"
REVIEW_MARKER = Path(__file__).resolve().parent.parent.parent / "lib" / "review_marker.py"


def _mkrepo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    (r / "a.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(r), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "base"], check=True)
    (r / "a.txt").write_text("changed\n")   # left UNSTAGED -- PreToolUse sees an empty index
    return r


def _record(repo, state, session="sess-x"):
    subprocess.run(
        [sys.executable, str(REVIEW_MARKER), "record", "--repo", str(repo), "--session", session],
        env={"REVIEW_MARKER_STATE_FILE": str(state)}, check=True, capture_output=True,
    )


def _warns(cmd, cwd, state, session="sess-x", extra_env=None):
    env = {"REVIEW_MARKER_STATE_FILE": str(state)}
    if extra_env:
        env.update(extra_env)
    payload = {"cwd": str(cwd), "tool_input": {"command": cmd}}
    if session is not None:
        payload["session_id"] = session
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        text=True, env=env, capture_output=True,
    )
    return result.stdout


def test_plain_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns("git commit -m x", repo, tmp_path / "state")


def test_dash_c_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns(f"git -C {repo} commit -m x", repo, tmp_path / "state")


def test_rtk_wrapped_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns("rtk git commit -m x", repo, tmp_path / "state")


def test_dash_c_config_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" in _warns("git -c user.name=x commit -m y", repo, tmp_path / "state")


def test_git_status_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("git status", repo, tmp_path / "state")


def test_git_log_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("git log --grep commit", repo, tmp_path / "state")


def test_prose_mentioning_commit_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("echo run git commit later >> notes.txt", repo, tmp_path / "state")


def test_grep_for_commit_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    assert "additionalContext" not in _warns("rg git commit docs/", repo, tmp_path / "state")


def test_dash_c_inside_message_cannot_hijack(tmp_path):
    repo = _mkrepo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    out = _warns(f"git commit -m 'explain the -C {other} form'", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_chained_add_and_commit_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    out = _warns(f"git -C {repo} add a.txt && git -C {repo} commit -m x", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_untracked_only_work_triggers(tmp_path):
    repo = _mkrepo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "a.txt"], check=True)
    (repo / "b.txt").write_text("new\n")
    out = _warns(f"git -C {repo} add b.txt && git -C {repo} commit -m x", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_dash_c_overrides_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    out = _warns(f"git -C {repo} commit -m x", other, tmp_path / "state")
    assert "additionalContext" in out


def test_cd_overrides_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    out = _warns(f"cd {repo} && git commit -m x", other, tmp_path / "state")
    assert "additionalContext" in out


def test_covered_commit_stays_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt")], check=True)
    assert "additionalContext" not in _warns("git commit -m x", repo, state)


def test_post_review_edit_warns_again(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt")], check=True)
    (repo / "a.txt").write_text("changed again\n")
    assert "additionalContext" in _warns("git commit -m x", repo, state)


def test_missing_session_id_fails_closed(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt")], check=True)
    out = _warns("git commit -m x", repo, state, session=None)
    assert "review record could not be read" in out


def test_unresolvable_cd_falls_back_to_payload_cwd(tmp_path):
    repo = _mkrepo(tmp_path)
    out = _warns("cd /tmp/definitely-not-a-repo-xyz && git commit -m x", repo, tmp_path / "state")
    assert "additionalContext" in out


def test_every_stale_file_reaches_the_reminder(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    (repo / "b.txt").write_text("second\n")
    _record(repo, state)
    subprocess.run(["touch", str(repo / "a.txt"), str(repo / "b.txt")], check=True)
    out = _warns("git commit -m x", repo, state)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "a.txt" in ctx and "b.txt" in ctx
    assert "\n" not in ctx   # the reminder stays one line


def test_long_stale_list_is_capped_at_ten(tmp_path):
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    for i in range(1, 21):
        (repo / f"f{i}.txt").write_text("x\n")
    _record(repo, state)
    subprocess.run(["touch", *[str(repo / f"f{i}.txt") for i in range(1, 21)],
                    str(repo / "a.txt")], check=True)
    out = _warns("git commit -m x", repo, state)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "and 11 more" in ctx or "and 10 more" in ctx   # 21 stale, capped list -> "and N more"


def test_carriage_return_path_stays_silent_when_covered(tmp_path):
    """Without -z, git C-quotes a path holding a carriage return (e.g. "we\\rird.txt"), which
    matches nothing on disk -- check() would then call the list unusable and warn on every
    commit regardless of review coverage. -z lets the raw byte through instead."""
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    cr_name = "we\rird.txt"
    (repo / cr_name).write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", cr_name], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "carriage-return path"], check=True)
    (repo / cr_name).write_text("changed\n")
    _record(repo, state)
    # every candidate must predate the record, or an unrelated stale file supplies the warning
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt"), str(repo / cr_name)], check=True)
    assert "additionalContext" not in _warns("git commit -m x", repo, state)


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="APFS rejects a filename with an invalid UTF-8 byte outright (OSError EILSEQ) -- "
           "the scenario this test targets can only occur on a filesystem that allows it "
           "(most Linux filesystems); nothing to reproduce on macOS.",
)
def test_invalid_utf8_path_stays_silent_when_covered(tmp_path):
    """A filename with a genuinely invalid UTF-8 byte must decode the SAME way here and in
    lib/review_marker.py's _tracked_deletions (surrogateescape on both sides) -- decoding with
    errors="replace" instead collapses the byte to U+FFFD, a different string than the
    deletion set's, and check() would then call the file list unusable on every commit."""
    repo = _mkrepo(tmp_path)
    state = tmp_path / "state"
    raw_name = b"bad\xffname.txt"
    bad_path = repo / os.fsdecode(raw_name)
    bad_path.write_bytes(b"x\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", os.fsdecode(raw_name)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "invalid-utf8 path"], check=True)
    bad_path.write_bytes(b"changed\n")
    _record(repo, state)
    subprocess.run(["touch", "-t", "200001010000", str(repo / "a.txt"), str(bad_path)], check=True)
    assert "additionalContext" not in _warns("git commit -m x", repo, state)
