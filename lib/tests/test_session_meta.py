"""Tests for lib/session_meta.py (dev-flow journal model/effort)."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "session_meta.py"

spec = importlib.util.spec_from_file_location("session_meta", LIB)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)


def _assistant(model=None, effort=None, sidechain=False):
    obj = {"type": "assistant", "isSidechain": sidechain, "message": {}}
    if model is not None:
        obj["message"]["model"] = model
    if effort is not None:
        obj["effort"] = effort
    return json.dumps(obj)


def _transcript(home, session_id, lines):
    project = home / ".claude" / "projects" / "-some-slug"
    project.mkdir(parents=True)
    path = project / f"{session_id}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_last_assistant_turn_wins(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        _assistant("claude-sonnet-5", "medium"),
        _assistant("claude-sonnet-5", "high"),
    ]) + "\n")
    assert sm.read_meta(f) == ("claude-sonnet-5", "high", ["claude-sonnet-5"])


def test_models_are_listed_in_first_seen_order(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        _assistant("claude-opus-5-5", "high"),
        _assistant("claude-sonnet-5", "high"),
        _assistant("claude-opus-5-5", "high"),
    ]) + "\n")
    assert sm.read_meta(f) == ("claude-opus-5-5", "high", ["claude-opus-5-5", "claude-sonnet-5"])


def test_sidechain_synthetic_and_non_assistant_lines_are_ignored(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        _assistant("claude-sonnet-5", "high"),
        _assistant("claude-haiku-4-5", "low", sidechain=True),
        _assistant("<synthetic>", "max"),
        json.dumps({"type": "user", "message": {"model": "claude-fable-5-1"}, "effort": "xhigh"}),
    ]) + "\n")
    assert sm.read_meta(f) == ("claude-sonnet-5", "high", ["claude-sonnet-5"])


def test_synthetic_turn_does_not_overwrite_effort(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        _assistant("claude-sonnet-5", "high"),
        _assistant("<synthetic>", "max"),
    ]) + "\n")
    assert sm.read_meta(f)[1] == "high"


def test_effort_comes_from_the_same_turn_as_the_model(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        _assistant("claude-opus-5-5", "high"),
        _assistant("claude-sonnet-5"),
    ]) + "\n")
    assert sm.read_meta(f) == ("claude-sonnet-5", None, ["claude-opus-5-5", "claude-sonnet-5"])


def test_assistant_turn_without_model_moves_nothing(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        _assistant("claude-sonnet-5", "high"),
        _assistant(None, "low"),
    ]) + "\n")
    assert sm.read_meta(f) == ("claude-sonnet-5", "high", ["claude-sonnet-5"])


def test_missing_effort_field_is_none(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(_assistant("claude-opus-5") + "\n")
    assert sm.read_meta(f) == ("claude-opus-5", None, ["claude-opus-5"])


def test_malformed_lines_are_tolerated(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("not json\n572\n[1, 2]\n" + json.dumps({"type": "assistant", "message": "x"}) + "\n"
                 + _assistant("claude-sonnet-5", "high") + "\n")
    assert sm.read_meta(f) == ("claude-sonnet-5", "high", ["claude-sonnet-5"])


def test_unreadable_path_returns_none(tmp_path):
    assert sm.read_meta(tmp_path / "missing.jsonl") is None


def test_format_line_single_and_mixed():
    assert sm.format_line("claude-sonnet-5", "high", ["claude-sonnet-5"]) == "model=claude-sonnet-5 effort=high"
    assert sm.format_line("claude-sonnet-5", None, ["claude-opus-5", "claude-sonnet-5"]) == (
        "model=claude-sonnet-5 effort=unknown (mixed: claude-opus-5, claude-sonnet-5)"
    )
    assert sm.format_line(None, None, [], reason="no session id") == "model=unknown effort=unknown (no session id)"


def test_no_session_id_exits_4_with_a_line_on_stdout(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(sm.sys, "argv", ["session_meta.py"])
    assert sm.cli() == 4
    assert capsys.readouterr().out.strip() == "model=unknown effort=unknown (no session id)"


def test_missing_main_transcript_exits_4(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sm.sys, "argv", ["session_meta.py", "sid-does-not-exist"])
    assert sm.cli() == 4
    assert capsys.readouterr().out.strip() == "model=unknown effort=unknown (main transcript not found)"


def test_transcript_without_assistant_model_exits_4(monkeypatch, capsys, tmp_path):
    _transcript(tmp_path, "sid-empty-0001", [json.dumps({"type": "user", "message": {}})])
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sm.sys, "argv", ["session_meta.py", "sid-empty-0001"])
    assert sm.cli() == 4
    assert capsys.readouterr().out.strip() == "model=unknown effort=unknown (no assistant turn)"


def test_real_transcript_exits_0(monkeypatch, capsys, tmp_path):
    _transcript(tmp_path, "sid-real-0001", [_assistant("claude-opus-5-5", "high")])
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sm.sys, "argv", ["session_meta.py", "sid-real-0001"])
    assert sm.cli() == 0
    assert capsys.readouterr().out.strip() == "model=claude-opus-5-5 effort=high"


def test_internal_error_exits_3_and_still_prints_a_line(monkeypatch, capsys):
    def boom(_session_id):
        raise RuntimeError("harness layout changed")

    monkeypatch.setattr(sm, "find_main_transcript", boom)
    monkeypatch.setattr(sm.sys, "argv", ["session_meta.py", "sid-whatever"])
    assert sm.cli() == 3
    captured = capsys.readouterr()
    assert captured.out.strip() == "model=unknown effort=unknown (internal error - see stderr)"
    assert "RuntimeError: harness layout changed" in captured.err


def test_process_exit_status_reaches_the_shell(tmp_path):
    _transcript(tmp_path, "sid-subprocess-0001", [_assistant("claude-sonnet-5", "high")])
    proc = subprocess.run(
        [sys.executable, str(LIB), "sid-subprocess-0001"],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "model=claude-sonnet-5 effort=high"
