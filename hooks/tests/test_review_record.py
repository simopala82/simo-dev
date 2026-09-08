#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of the now-deleted hooks/test_review_record.sh (removed in c0d0234f,
the same commit that added this file); cases 1-13 are ported here. Case 14, the
settings.json hook-registration check, has no pytest counterpart in this file."""
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
