#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of the now-deleted hooks/test_review_record.sh (removed in c0d0234f,
the same commit that added this file); cases 1-13 are ported here. Case 14, the
settings.json hook-registration check, has no pytest counterpart in this file. The
SubagentHandback cases at the bottom are new, not part of that port."""
import json
import subprocess
import sys
from pathlib import Path

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


def _repo_id(path):
    common = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return str(Path(common).parent.resolve())


def _mk_transcript(tmp_path, *role_text_pairs, counter=[0]):
    counter[0] += 1
    f = tmp_path / f"agent-{counter[0]}.jsonl"
    lines = []
    it = iter(role_text_pairs)
    for role, text in zip(it, it):
        lines.append(json.dumps({"type": role, "message": {"role": role, "content": text}}))
    f.write_text("\n".join(lines) + "\n")
    return f


def _recorded(state):
    return state.read_text() if state.exists() else ""


def _fire_path(transcript, agent_type, session, cwd, last="mergeable", state=None):
    payload = {"hook_event_name": "SubagentStop", "session_id": session, "cwd": str(cwd),
               "agent_id": "a0test", "agent_type": agent_type,
               "agent_transcript_path": str(transcript)}
    if last != "":
        payload["last_assistant_message"] = last
    env = {"REVIEW_MARKER_STATE_FILE": str(state)}
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
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
    assert recorded.startswith(_repo_id(marked_repo) + ":sess-1:")
    assert _repo_id(cwd_repo) not in recorded


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
    assert _recorded(state).startswith(_repo_id(marked_repo) + ":sess-7:")


def test_line_one_marker_wins_over_a_later_one(tmp_path):
    cwd_repo = tmp_path / "cwd-repo"; _mkrepo(cwd_repo)
    marked_repo = tmp_path / "marked-repo"; _mkrepo(marked_repo)
    state = tmp_path / "state"
    _fire(tmp_path, f"REPO: {marked_repo}\nReview the diff.\nREPO: {cwd_repo}",
          "simo-dev:code-reviewer", "sess-8", cwd_repo, state)
    recorded = _recorded(state)
    assert recorded.startswith(_repo_id(marked_repo) + ":sess-8:")
    assert _repo_id(cwd_repo) not in recorded


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
    assert recorded.startswith(_repo_id(marked_repo) + ":sess-11:")
    assert _repo_id(cwd_repo) not in recorded


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
    assert recorded.startswith(_repo_id(marked_repo) + ":sess-14:")


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
    assert recorded.startswith(_repo_id(marked_repo) + ":sess-20:")


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
    assert _recorded(state).startswith(_repo_id(marked_repo) + ":sess-23:")
