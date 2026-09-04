"""Tests for lib/session_tokens.py (dev-flow transcript token sum)."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "session_tokens.py"
FIXTURES = Path(__file__).parent / "fixtures"

spec = importlib.util.spec_from_file_location("session_tokens", LIB)
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def test_mixed_fixture_skips_non_dict_lines_and_counts_them():
    total, skipped = st.sum_output_tokens(FIXTURES / "session_transcript_mixed.jsonl")
    assert total == 123
    assert skipped == 2


def test_bare_integer_line_does_not_crash(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('572\n{"message": {"usage": {"output_tokens": 7}}}\n')
    assert st.sum_output_tokens(f) == (7, 1)


def test_json_list_line_is_skipped(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('[1, 2, 3]\n{"message": {"usage": {"output_tokens": 7}}}\n')
    assert st.sum_output_tokens(f) == (7, 1)


def test_non_dict_message_is_a_normal_record_not_a_skip(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('{"message": "hello"}\n{"message": {"usage": {"output_tokens": 7}}}\n')
    assert st.sum_output_tokens(f) == (7, 0)


def test_non_integer_output_tokens_is_counted_as_skipped(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('{"message": {"usage": {"output_tokens": "lots"}}}\n')
    assert st.sum_output_tokens(f) == (0, 1)


def test_float_and_bool_output_tokens_are_skipped_not_summed(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        '{"message": {"usage": {"output_tokens": true}}}\n'
        '{"message": {"usage": {"output_tokens": 12.5}}}\n'
        '{"message": {"usage": {"output_tokens": 10}}}\n'
    )
    assert st.sum_output_tokens(f) == (10, 2)


def test_usage_without_output_tokens_is_not_a_skip(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('{"message": {"usage": {"input_tokens": 4}}}\n')
    assert st.sum_output_tokens(f) == (0, 0)


def test_blank_lines_are_not_counted_as_skipped(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('\n\n{"message": {"usage": {"output_tokens": 7}}}\n\n')
    assert st.sum_output_tokens(f) == (7, 0)


def test_unreadable_path_returns_none(tmp_path):
    assert st.sum_output_tokens(tmp_path / "missing.jsonl") is None


def test_no_session_id_exits_4_with_a_line_on_stdout(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(st.sys, "argv", ["session_tokens.py"])
    assert st.cli() == 4
    assert capsys.readouterr().out.strip() == "main=0 subagent=na total=0 (no session id)"


def test_missing_main_transcript_exits_4_with_a_line_on_stdout(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(st.sys, "argv", ["session_tokens.py", "sid-does-not-exist"])
    assert st.cli() == 4
    assert capsys.readouterr().out.strip() == "main=0 subagent=na total=0 (main transcript not found)"


def test_internal_error_exits_3_and_still_prints_a_line(monkeypatch, capsys):
    def boom(_session_id):
        raise RuntimeError("harness layout changed")

    monkeypatch.setattr(st, "find_main_transcript", boom)
    monkeypatch.setattr(st.sys, "argv", ["session_tokens.py", "sid-whatever"])
    assert st.cli() == 3
    captured = capsys.readouterr()
    assert captured.out.strip() == "main=0 subagent=na total=0 (internal error - see stderr)"
    assert "RuntimeError: harness layout changed" in captured.err


def test_main_transcript_only_exits_0_and_reports_skipped(monkeypatch, capsys, tmp_path):
    session_id = "sid-plan-fixture-0001"
    project = tmp_path / ".claude" / "projects" / "-some-slug"
    project.mkdir(parents=True)
    (project / f"{session_id}.jsonl").write_text(
        (FIXTURES / "session_transcript_mixed.jsonl").read_text()
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(st, "find_subagent_transcripts", lambda _sid: [])
    monkeypatch.setattr(st.sys, "argv", ["session_tokens.py", session_id])
    assert st.cli() == 0
    out = capsys.readouterr().out.strip()
    assert out == "main=123 subagent=na total=123 (subagents not captured, 2 lines skipped)"


def test_subagent_transcripts_are_summed_and_counted(monkeypatch, capsys, tmp_path):
    session_id = "sid-plan-fixture-0002"
    project = tmp_path / ".claude" / "projects" / "-some-slug"
    project.mkdir(parents=True)
    (project / f"{session_id}.jsonl").write_text('{"message": {"usage": {"output_tokens": 10}}}\n')
    sub_ok = tmp_path / "sub_ok.output"
    sub_ok.write_text('572\n{"message": {"usage": {"output_tokens": 5}}}\n')
    sub_gone = tmp_path / "sub_gone.output"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(st, "find_subagent_transcripts", lambda _sid: [str(sub_ok), str(sub_gone)])
    monkeypatch.setattr(st.sys, "argv", ["session_tokens.py", session_id])
    assert st.cli() == 0
    out = capsys.readouterr().out.strip()
    assert out == "main=10 subagent=5 total=15 (1 subagent transcripts, 1 unreadable, 1 lines skipped)"


def test_process_exit_status_reaches_the_shell(tmp_path):
    """cli() returning a code is not enough — `sys.exit(cli())` is what the caller reads.

    Without this the guard could regress to a bare `cli()` and every in-process
    test above would still pass while the shell saw rc 0.
    """
    proc = subprocess.run(
        [sys.executable, str(LIB)],
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_CODE_SESSION_ID": "", "HOME": str(tmp_path)},
    )
    assert proc.returncode == 4
    assert proc.stdout.strip() == "main=0 subagent=na total=0 (no session id)"


def test_process_exit_status_is_zero_on_a_real_transcript(tmp_path):
    session_id = "sid-subprocess-0001"
    project = tmp_path / ".claude" / "projects" / "-some-slug"
    project.mkdir(parents=True)
    (project / f"{session_id}.jsonl").write_text('{"message": {"usage": {"output_tokens": 9}}}\n')
    proc = subprocess.run(
        [sys.executable, str(LIB), session_id],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "main=9 subagent=na total=9 (subagents not captured)"


def test_note_appends_nothing_when_nothing_was_skipped():
    """The clean-session line is the common case and no other test pins it.

    Without this, `note()` losing its `if skipped` guard writes `, 0 lines skipped`
    into every future journal entry and the whole suite stays green.
    """
    assert st.note("15 subagent transcripts", 0) == "15 subagent transcripts"
    assert st.note("15 subagent transcripts", 578) == "15 subagent transcripts, 578 lines skipped"
