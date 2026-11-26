#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of the now-deleted hooks/test_review_record.sh (removed in c0d0234f,
the same commit that added this file); cases 1-13 are ported here. Case 14, the
settings.json hook-registration check, has no pytest counterpart in this file. The
SubagentHandback cases at the bottom are new, not part of that port."""
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "review_record.py"
AGENT_MARKER = Path(__file__).resolve().parent.parent.parent / "lib" / "agent_marker.py"
REVIEW_MARKER = Path(__file__).resolve().parent.parent.parent / "lib" / "review_marker.py"


def _mkrepo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True)
    (path / "f.txt").write_text("v1\n")
    subprocess.run(["git", "-C", str(path), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)


def _tree_id(path):
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _mk_transcript(tmp_path, *role_text_pairs, counter=[0]):
    counter[0] += 1
    f = tmp_path / f"agent-{counter[0]}.jsonl"
    lines = []
    it = iter(role_text_pairs)
    for role, text in zip(it, it):
        lines.append(json.dumps({"type": role, "message": {"role": role, "content": text}}))
    f.write_text("\n".join(lines) + "\n")
    return f


def _env(state):
    """HOME points at a dir next to the state file, so _plugin_enabled() never reads the real
    ~/.claude/settings.json (expanduser falls back to the pwd entry without it). With no
    settings there it takes the missing-file default, enabled; the two plugin tests below
    write one."""
    home = state.parent / "home"
    home.mkdir(exist_ok=True)
    return {"REVIEW_MARKER_STATE_FILE": str(state), "HOME": str(home)}


def _recorded(state):
    return state.read_text() if state.exists() else ""


def _fire_path(transcript, agent_type, session, cwd, last="mergeable", state=None):
    payload = {"hook_event_name": "SubagentStop", "session_id": session, "cwd": str(cwd),
               "agent_id": "a0test", "agent_type": agent_type,
               "agent_transcript_path": str(transcript)}
    if last != "":
        payload["last_assistant_message"] = last
    env = _env(state)
    subprocess.run([sys.executable, str(HOOK), "--worker", json.dumps(payload)],
                   text=True, env=env, check=True, capture_output=True)


def _fire(tmp_path, prompt, agent_type, session, cwd, state):
    _fire_path(_mk_transcript(tmp_path, "user", prompt), agent_type, session, cwd, state=state)


def test_marker_repo_is_recorded_not_payload_cwd(tmp_path):
    cwd_repo, marked_repo = tmp_path / "cwd-repo", tmp_path / "marked-repo"
    _mkrepo(cwd_repo); _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {marked_repo}\nReview the diff.", "simo-dev:code-reviewer",
          "sess-1", cwd_repo, state)
    recorded = _recorded(state)
    assert recorded.startswith(_tree_id(marked_repo) + ":sess-1:")
    assert _tree_id(cwd_repo) not in recorded


def test_absent_marker_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    state = tmp_path / "state"
    _fire(tmp_path, "Review the diff at origin/master...HEAD.", "simo-dev:code-reviewer",
          "sess-2", cwd_repo, state)
    assert not state.exists() or state.read_text() == ""


def test_nonexistent_marker_path_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {tmp_path / 'not-a-repo'}\nReview.", "simo-dev:code-reviewer",
          "sess-3", cwd_repo, state)
    assert not state.exists() or state.read_text() == ""


def test_non_git_directory_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {tmp_path}\nReview.", "simo-dev:code-reviewer", "sess-3b", cwd_repo, state)
    assert not state.exists() or state.read_text() == ""


def test_mid_prose_marker_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"The hook reads REPO: {marked_repo} from the prompt.",
          "simo-dev:code-reviewer", "sess-4", cwd_repo, state)
    assert not state.exists() or state.read_text() == ""


def test_other_agent_type_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {marked_repo}\nReview.", "general-purpose", "sess-5", cwd_repo, state)
    assert not state.exists() or state.read_text() == ""


def test_empty_session_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {marked_repo}\nReview.", "simo-dev:code-reviewer", "", cwd_repo, state)
    assert not state.exists() or state.read_text() == ""


def test_bare_code_reviewer_agent_type_still_records(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {marked_repo}\nReview.", "code-reviewer", "sess-7", cwd_repo, state)
    assert _recorded(state).startswith(_tree_id(marked_repo) + ":sess-7:")


def test_line_one_marker_wins_over_a_later_one(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {marked_repo}\nReview the diff.\nREPO: {cwd_repo}",
          "simo-dev:code-reviewer", "sess-8", cwd_repo, state)
    recorded = _recorded(state)
    assert recorded.startswith(_tree_id(marked_repo) + ":sess-8:")
    assert _tree_id(cwd_repo) not in recorded


def test_marker_below_line_one_is_ignored_entirely(tmp_path):
    """mr-review pastes third-party text at column 0 with no marker of its own -- a
    REPO: line below line 1 must never register coverage for this session's work."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"Review the diff at origin/master...HEAD.\nREPO: {marked_repo}",
          "simo-dev:code-reviewer", "sess-8b", cwd_repo, state)
    assert not state.exists() or state.read_text() == ""


def test_resume_cannot_supply_the_marker(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_transcript(
        tmp_path,
        "user", "Review the diff at origin/master...HEAD.",
        "assistant", "no findings",
        "user", f"REPO: {marked_repo}\nRe-review.",
    )
    _fire_path(transcript, "simo-dev:code-reviewer", "sess-10", cwd_repo, state=state)
    assert not state.exists() or state.read_text() == ""


def test_resume_cannot_repoint_an_existing_marker(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_transcript(
        tmp_path,
        "user", f"REPO: {marked_repo}\nReview.",
        "assistant", "no findings",
        "user", f"REPO: {cwd_repo}\nRe-review.",
    )
    _fire_path(transcript, "simo-dev:code-reviewer", "sess-11", cwd_repo, state=state)
    recorded = _recorded(state)
    assert recorded.startswith(_tree_id(marked_repo) + ":sess-11:")
    assert _tree_id(cwd_repo) not in recorded


def test_missing_transcript_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    state = tmp_path / "state"
    _fire_path(tmp_path / "no-such-agent.jsonl", "simo-dev:code-reviewer", "sess-12",
              cwd_repo, state=state)
    assert not state.exists() or state.read_text() == ""


def test_interrupted_reviewer_records_nothing(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_transcript(tmp_path, "user", f"REPO: {marked_repo}\nReview.")
    _fire_path(transcript, "simo-dev:code-reviewer", "sess-13", cwd_repo, last="", state=state)
    assert not state.exists() or state.read_text() == ""


def _mk_handback_transcript(tmp_path, prompt, message="verdict: mergeable", tail=(), counter=[0]):
    counter[0] += 1
    f = tmp_path / f"handback-{counter[0]}.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_hb", "name": "SubagentHandback",
             "input": {"message": message}}]}},
        {"type": "attachment", "attachment": {"type": "noise"}},
        *tail,
    ]
    f.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return f


def _handback_fire(tmp_path, session, prompt_for, **kw):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_handback_transcript(tmp_path, prompt_for(marked_repo), **kw)
    _fire_path(transcript, "simo-dev:code-reviewer", session, cwd_repo, last="", state=state)
    return _recorded(state), marked_repo


def test_handback_conclusion_without_last_message_records(tmp_path):
    recorded, marked_repo = _handback_fire(tmp_path, "sess-14", lambda r: f"REPO: {r}\nReview.")
    assert recorded.startswith(_tree_id(marked_repo) + ":sess-14:")


def test_handback_with_blank_message_records_nothing(tmp_path):
    recorded, _ = _handback_fire(tmp_path, "sess-15", lambda r: f"REPO: {r}\nReview.", message="  ")
    assert recorded == ""


def test_handback_with_non_string_message_records_nothing(tmp_path):
    recorded, _ = _handback_fire(tmp_path, "sess-16", lambda r: f"REPO: {r}\nReview.", message=["x"])
    assert recorded == ""


def test_interrupted_resume_after_earlier_handback_records_nothing(tmp_path):
    """Round 1 handed back; round 2 (SendMessage resume, same transcript) was interrupted
    mid-tool -- the stale round-1 handback must not count as round 2's conclusion."""
    tail = (
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_hb",
             "content": [{"type": "text", "text": "{\"success\":true}"}]}]}},
        {"type": "user", "message": {"role": "user", "content": "Re-review the new diff."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_bash", "name": "Bash", "input": {"command": "git diff"}}]}},
    )
    recorded, _ = _handback_fire(tmp_path, "sess-17", lambda r: f"REPO: {r}\nReview.", tail=tail)
    assert recorded == ""


def test_resume_interrupted_right_after_new_prompt_records_nothing(tmp_path):
    """The resume prompt is a plain user message after the earlier handback's tool_result;
    an interrupt before any assistant output leaves it last."""
    tail = (
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_hb",
             "content": [{"type": "text", "text": "{\"success\":true}"}]}]}},
        {"type": "user", "message": {"role": "user", "content": "Re-review the new diff."}},
    )
    recorded, _ = _handback_fire(tmp_path, "sess-25", lambda r: f"REPO: {r}\nReview.", tail=tail)
    assert recorded == ""


def _result_entry(tool_use_id):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id,
         "content": [{"type": "text", "text": "{\"success\":true}"}]}]}}


def test_handback_followed_by_its_own_tool_result_records(tmp_path):
    recorded, marked_repo = _handback_fire(tmp_path, "sess-20", lambda r: f"REPO: {r}\nReview.",
                                           tail=(_result_entry("toolu_hb"),))
    assert recorded.startswith(_tree_id(marked_repo) + ":sess-20:")


def test_handback_followed_by_its_own_error_result_records_nothing(tmp_path):
    error_result = _result_entry("toolu_hb")
    error_result["message"]["content"][0]["is_error"] = True
    recorded, _ = _handback_fire(tmp_path, "sess-24", lambda r: f"REPO: {r}\nReview.",
                                 tail=(error_result,))
    assert recorded == ""


def test_handback_followed_by_foreign_tool_result_records_nothing(tmp_path):
    recorded, _ = _handback_fire(tmp_path, "sess-21", lambda r: f"REPO: {r}\nReview.",
                                 tail=(_result_entry("toolu_other"),))
    assert recorded == ""


def test_handback_marker_below_line_one_records_nothing(tmp_path):
    recorded, _ = _handback_fire(tmp_path, "sess-18", lambda r: f"Review.\nREPO: {r}")
    assert recorded == ""


def test_handback_malformed_lines_do_not_raise(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    state = tmp_path / "state"
    f = tmp_path / "malformed.jsonl"
    f.write_text("not json\n" + json.dumps([1, 2]) + "\n" + json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "SubagentHandback", "input": "oops"}, 7]}}) + "\n")
    _fire_path(f, "simo-dev:code-reviewer", "sess-19", cwd_repo, last="", state=state)
    assert _recorded(state) == ""


def test_handback_parallel_with_running_tool_records_nothing(tmp_path):
    """A handback emitted in the same response as another tool_use is written as a separate
    entry sharing message.id; while that tool still runs the handback is last, and an
    interrupt there must not count as a conclusion."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    f = tmp_path / "parallel.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": f"REPO: {marked_repo}\nReview."}},
        {"type": "assistant", "message": {"id": "msg_X", "role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."}]}},
        {"type": "assistant", "message": {"id": "msg_X", "role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_bash", "name": "Bash", "input": {"command": "pytest"}}]}},
        {"type": "assistant", "message": {"id": "msg_X", "role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_hb", "name": "SubagentHandback",
             "input": {"message": "verdict: mergeable"}}]}},
    ]
    f.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    _fire_path(f, "simo-dev:code-reviewer", "sess-22", cwd_repo, last="", state=state)
    assert _recorded(state) == ""


def test_handback_sharing_message_id_with_thinking_only_records(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    f = tmp_path / "same-id.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": f"REPO: {marked_repo}\nReview."}},
        {"type": "assistant", "message": {"id": "msg_Y", "role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."}]}},
        {"type": "assistant", "message": {"id": "msg_Y", "role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_hb", "name": "SubagentHandback",
             "input": {"message": "verdict: mergeable"}}]}},
    ]
    f.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    _fire_path(f, "simo-dev:code-reviewer", "sess-23", cwd_repo, last="", state=state)
    assert _recorded(state).startswith(_tree_id(marked_repo) + ":sess-23:")


@pytest.mark.parametrize("sep", [0x85, 0x2028, 0x2029])
def test_handback_report_with_a_raw_unicode_line_separator_records(tmp_path, sep):
    """The CLI writes JSONL with JSON.stringify, which leaves NEL, U+2028 and U+2029 raw inside
    strings; only "\\n" ends an entry. A report quoting one must still count as the conclusion."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    f = tmp_path / "raw-sep.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": f"REPO: {marked_repo}\nReview."}},
        {"type": "assistant", "message": {"id": "msg_Z", "role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."}]}},
        {"type": "assistant", "message": {"id": "msg_Z", "role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_hb", "name": "SubagentHandback",
             "input": {"message": f"verdict: mergeable, quoting `{chr(sep)}`"}}]}},
        _result_entry("toolu_hb"),
    ]
    f.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
                 encoding="utf-8")
    assert chr(sep) in f.read_text(encoding="utf-8")
    _fire_path(f, "simo-dev:code-reviewer", "sess-26", cwd_repo, last="", state=state)
    assert _recorded(state).startswith(_tree_id(marked_repo) + ":sess-26:")


def test_crlf_marker_records_the_stripped_path(tmp_path):
    """agent_marker keeps a trailing CR in line 1; the hook strips its output, so a CRLF
    dispatch still records the repo it names."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {marked_repo}\r\nReview.", "simo-dev:code-reviewer", "sess-27", cwd_repo, state)
    assert _recorded(state).startswith(_tree_id(marked_repo) + ":sess-27:")


@pytest.mark.parametrize("sep", [0x85, 0x2028, 0x2029])
def test_separator_inside_the_marker_path_records_nothing(tmp_path, sep):
    """A raw separator inside line 1 no longer cuts the path short: the whole line names no
    work tree, so nothing is recorded (before the fix, the prefix was recorded)."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    f = tmp_path / f"mid-sep-{sep}.jsonl"
    entry = {"type": "user", "message": {"role": "user",
                                         "content": f"REPO: {marked_repo}{chr(sep)}{cwd_repo}\nReview."}}
    f.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    _fire_path(f, "simo-dev:code-reviewer", "sess-28", cwd_repo, state=state)
    assert _recorded(state) == ""


def _wait_for_record(state, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _recorded(state):
            return _recorded(state)
        time.sleep(0.1)
    return _recorded(state)


def _stop_payload(transcript, session, cwd, last=None):
    payload = {"hook_event_name": "SubagentStop", "session_id": session, "cwd": str(cwd),
               "agent_id": "a0test", "agent_type": "simo-dev:code-reviewer",
               "agent_transcript_path": str(transcript)}
    if last is not None:
        payload["last_assistant_message"] = last
    return payload


def test_default_path_text_conclusion_records(tmp_path):
    """Pins the truthy half of the translation: a reviewer ending in text, not in a handback,
    records only through last_assistant_message, so the slim payload must carry it."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_transcript(tmp_path, "user", f"REPO: {marked_repo}\nReview.")
    payload = _stop_payload(transcript, "sess-29", cwd_repo, last="verdict: mergeable")
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                   text=True, env=_env(state), check=True, capture_output=True)
    assert _wait_for_record(state, 10).startswith(_tree_id(marked_repo) + ":sess-29:")


def test_default_path_records_through_the_detached_worker(tmp_path):
    """Smoke test of the default path end to end; it passes on the old synchronous hook too."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_handback_transcript(tmp_path, f"REPO: {marked_repo}\nReview.")
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(_stop_payload(transcript, "sess-26", cwd_repo)),
                   text=True, env=_env(state), check=True, capture_output=True)
    assert _wait_for_record(state, 10).startswith(_tree_id(marked_repo) + ":sess-26:")


def test_default_path_interrupted_reviewer_records_nothing(tmp_path):
    """Pins the foreground -> worker translation: an absent last_assistant_message must stay
    absent in the slim payload, or every interrupted reviewer would record false coverage."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_transcript(tmp_path, "user", f"REPO: {marked_repo}\nReview.")
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(_stop_payload(transcript, "sess-28", cwd_repo)),
                   text=True, env=_env(state), check=True, capture_output=True)
    # The worker waits HANDBACK_WAIT_SECONDS before giving up; a regression that records
    # after that wait must still land inside this window.
    assert _wait_for_record(state, 5) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: killpg, SIGKILL and a /bin/sh git shim")
def test_record_survives_the_hook_being_killed_mid_run(tmp_path):
    """2.1.308 binds SubagentStop hooks to the agent's abort signal and can kill one still
    running at stream teardown (a probe never saw it happen, the detach is insurance).
    A slow `git` keeps the record path busy past the kill;
    the record must still land, because the foreground process has already exited. Its stdout
    must also reach EOF in time: the CLI waits on the hook's streams, so a worker holding them
    would keep the hook alive into the kill."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_handback_transcript(tmp_path, f"REPO: {marked_repo}\nReview.")
    real_git = shutil.which("git")
    assert real_git
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    slow_git = bin_dir / "git"
    slow_git.write_text(f'#!/bin/sh\nsleep 1\nexec "{real_git}" "$@"\n')
    slow_git.chmod(0o755)
    env = {**_env(state), "PATH": f"{bin_dir}:/usr/bin:/bin"}
    proc = subprocess.Popen([sys.executable, str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    try:
        # 1.5 s: room for a cold interpreter on a loaded machine, still below the >= 2 s the
        # synchronous path spends in two slow-git calls (rev-parse, then repo_id).
        proc.communicate(json.dumps(_stop_payload(transcript, "sess-27", cwd_repo)), timeout=1.5)
        streams_closed_in_time = True
    except subprocess.TimeoutExpired:
        streams_closed_in_time = False
    # PermissionError: macOS answers EPERM for a group whose only member is an unreaped zombie.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.communicate()
    assert streams_closed_in_time
    assert _wait_for_record(state, 15).startswith(_tree_id(marked_repo) + ":sess-27:")


def _fire_with_plugin_setting(tmp_path, session, enabled):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    env = _env(state)
    settings = Path(env["HOME"]) / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"enabledPlugins": {"simo-dev@simo-local": enabled}}))
    transcript = _mk_handback_transcript(tmp_path, f"REPO: {marked_repo}\nReview.")
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(_stop_payload(transcript, session, cwd_repo)),
                   text=True, env=env, check=True, capture_output=True)
    return state, marked_repo


def test_disabled_plugin_records_nothing_on_the_default_path(tmp_path):
    """The plugin gate moved from the foreground into the worker; a disabled plugin must
    still record nothing."""
    state, _ = _fire_with_plugin_setting(tmp_path, "sess-30", False)
    assert _wait_for_record(state, 3) == ""


def test_enabled_plugin_records_on_the_default_path(tmp_path):
    """Reads an explicit `true`, not the missing-file default: an inverted check fails here."""
    state, marked_repo = _fire_with_plugin_setting(tmp_path, "sess-31", True)
    assert _wait_for_record(state, 10).startswith(_tree_id(marked_repo) + ":sess-31:")


@pytest.mark.parametrize("stdin", [b"", b"not json", b"[]", b"\xff"])
def test_malformed_stdin_exits_zero_silently(tmp_path, stdin):
    """A crash would exit non-zero with a traceback on stderr. b"\\xff" reaches the UnicodeError
    branch around sys.stdin.read() only under strict decoding: with no locale in the env,
    Python reads stdin with surrogateescape and never raises, hence PYTHONIOENCODING."""
    env = {**_env(tmp_path / "state"), "PYTHONIOENCODING": "utf-8:strict"}
    result = subprocess.run([sys.executable, str(HOOK)], input=stdin, env=env,
                            check=True, capture_output=True)
    assert result.stdout == b"" and result.stderr == b""


def test_handback_flushed_after_the_hook_starts_still_records(tmp_path):
    """CLI 2.1.308 fires SubagentStop before the closing SubagentHandback entry is on disk:
    a probe saw the transcript end at the final api-request lines at hook start, and the
    handback land 0-50 ms later. The worker must wait for it, not read once."""
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    transcript = _mk_handback_transcript(tmp_path, f"REPO: {marked_repo}\nReview.")
    lines = transcript.read_text().splitlines()
    handback = lines.pop(2)
    lines.append(json.dumps({"type": "api-request", "timestamp": "t"}))
    transcript.write_text("\n".join(lines) + "\n")
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(_stop_payload(transcript, "sess-32", cwd_repo)),
                   text=True, env=_env(state), check=True, capture_output=True)
    time.sleep(0.3)
    with transcript.open("a") as f:
        f.write(handback + "\n")
    assert _wait_for_record(state, 10).startswith(_tree_id(marked_repo) + ":sess-32:")


def _load_hook_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("review_record_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_handback_lands_waits_for_a_late_flush(tmp_path):
    """Deterministic twin of the subprocess test above: the first read is immediate, so a
    single-read regression fails here whatever the machine load."""
    import threading
    hook = _load_hook_module()
    transcript = _mk_handback_transcript(tmp_path, "REPO: /x\nReview.")
    lines = transcript.read_text().splitlines()
    handback = lines.pop(2)
    transcript.write_text("\n".join(lines) + "\n")

    def append_late():
        time.sleep(0.3)
        with transcript.open("a") as f:
            f.write(handback + "\n")

    writer = threading.Thread(target=append_late)
    writer.start()
    try:
        assert hook._handback_lands(str(transcript))
    finally:
        writer.join()


def test_handback_lands_gives_up_without_a_handback(tmp_path):
    hook = _load_hook_module()
    transcript = _mk_transcript(tmp_path, "user", "REPO: /x\nReview.")
    started = time.monotonic()
    assert not hook._handback_lands(str(transcript))
    assert time.monotonic() - started >= hook.HANDBACK_WAIT_SECONDS


@pytest.mark.parametrize("reads, expected", [([True, False], False), ([True, True], True)])
def test_handback_lands_needs_a_read_that_holds(tmp_path, reads, expected):
    """Pins the settle check: a True read that does not hold one poll later is the window
    between a handback entry and a parallel tool_use written after it."""
    hook = _load_hook_module()
    seq = iter(reads)
    hook._handback_concluded = lambda _transcript: next(seq, False)
    hook.HANDBACK_WAIT_SECONDS = 0.2
    assert hook._handback_lands(str(tmp_path / "unused.jsonl")) is expected
