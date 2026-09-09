"""Tests for lib/agent_marker.py (line-1 REPO marker out of an agent transcript)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "agent_marker.py"

spec = importlib.util.spec_from_file_location("agent_marker", LIB)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)


def _write(tmp_path, *messages):
    """messages: (role, text) pairs, written one JSON object per line."""
    p = tmp_path / "agent.jsonl"
    with p.open("w") as fh:
        for role, text in messages:
            fh.write(json.dumps({"type": role, "message": {"role": role, "content": text}}) + "\n")
    return p


def test_marker_on_line_1_of_the_first_user_message(tmp_path):
    p = _write(tmp_path, ("user", "REPO: /srv/repo-a\nReview the diff."))
    assert am.repo_from_transcript(str(p)) == "/srv/repo-a"


def test_content_as_a_block_list_is_joined(tmp_path):
    p = tmp_path / "agent.jsonl"
    p.write_text(json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "REPO: /srv/repo-a\nReview."}]},
    }) + "\n")
    assert am.repo_from_transcript(str(p)) == "/srv/repo-a"


def test_a_textless_leading_block_cannot_promote_later_text_to_line_1(tmp_path):
    """Blocks join with a newline: a tool_result block must not let the next block's
    text become line 1, which is the only line allowed to name a repo."""
    p = tmp_path / "agent.jsonl"
    p.write_text(json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "irrelevant"},
            {"type": "text", "text": "REPO: /srv/repo-a\nReview."},
        ]},
    }) + "\n")
    assert am.repo_from_transcript(str(p)) is None


def test_marker_below_line_1_is_ignored(tmp_path):
    p = _write(tmp_path, ("user", "Review the diff.\nREPO: /srv/repo-a"))
    assert am.repo_from_transcript(str(p)) is None


def test_mid_prose_marker_is_ignored(tmp_path):
    p = _write(tmp_path, ("user", "The hook reads REPO: /srv/repo-a from the prompt."))
    assert am.repo_from_transcript(str(p)) is None


def test_a_later_user_message_cannot_supply_the_marker(tmp_path):
    """A SendMessage resume appends a user message; it must never be the marker source."""
    p = _write(tmp_path,
               ("user", "Review the diff at origin/master...HEAD."),
               ("assistant", "no findings"),
               ("user", "REPO: /srv/repo-a\nRe-review."))
    assert am.repo_from_transcript(str(p)) is None


def test_a_later_user_message_cannot_override_the_first(tmp_path):
    p = _write(tmp_path,
               ("user", "REPO: /srv/repo-a\nReview."),
               ("assistant", "no findings"),
               ("user", "REPO: /srv/repo-b\nRe-review."))
    assert am.repo_from_transcript(str(p)) == "/srv/repo-a"


def test_assistant_messages_are_not_user_messages(tmp_path):
    p = _write(tmp_path,
               ("assistant", "REPO: /srv/repo-a\nsomething"),
               ("user", "REPO: /srv/repo-b\nReview."))
    assert am.repo_from_transcript(str(p)) == "/srv/repo-b"


def test_attachment_lines_without_a_message_key_are_skipped(tmp_path):
    """Observed in real 2.1.222 transcripts: type=attachment entries carry no `message`."""
    p = tmp_path / "agent.jsonl"
    p.write_text(
        json.dumps({"type": "attachment", "attachment": {"type": "nested_memory"}}) + "\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": "REPO: /srv/repo-a\nReview."}}) + "\n")
    assert am.repo_from_transcript(str(p)) == "/srv/repo-a"


def test_relative_marker_path_is_rejected(tmp_path):
    p = _write(tmp_path, ("user", "REPO: relative/path\nReview."))
    assert am.repo_from_transcript(str(p)) is None


def test_path_with_spaces_survives(tmp_path):
    p = _write(tmp_path, ("user", "REPO: /srv/repo with spaces\nReview."))
    assert am.repo_from_transcript(str(p)) == "/srv/repo with spaces"


def test_missing_file_returns_none(tmp_path):
    assert am.repo_from_transcript(str(tmp_path / "nope.jsonl")) is None


def test_malformed_lines_are_skipped(tmp_path):
    p = tmp_path / "agent.jsonl"
    p.write_text("not json\n\n" + json.dumps(
        {"type": "user", "message": {"role": "user", "content": "REPO: /srv/repo-a\nReview."}}) + "\n")
    assert am.repo_from_transcript(str(p)) == "/srv/repo-a"


def test_cli_prints_the_repo_and_exits_zero(tmp_path):
    p = _write(tmp_path, ("user", "REPO: /srv/repo-a\nReview."))
    res = subprocess.run([sys.executable, str(LIB), str(p)], capture_output=True, text=True)
    assert res.returncode == 0
    assert res.stdout.strip() == "/srv/repo-a"


def test_cli_exits_one_without_a_marker(tmp_path):
    p = _write(tmp_path, ("user", "Review."))
    res = subprocess.run([sys.executable, str(LIB), str(p)], capture_output=True, text=True)
    assert res.returncode == 1
    assert res.stdout.strip() == ""
