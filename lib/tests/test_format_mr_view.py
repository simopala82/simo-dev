"""Tests for skills/mr-followup/lib/format_mr_view.py (replaces the composite-string
shell invocation at skills/mr-followup/SKILL.md step 1)."""
import importlib.util
import io
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "skills" / "mr-followup" / "lib" / "format_mr_view.py"

spec = importlib.util.spec_from_file_location("format_mr_view", MODULE)
fmv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fmv)


def test_formats_all_five_fields(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        '{"target_branch": "master", "iid": 42, "state": "opened", '
        '"draft": false, "source_branch": "feat/x"}'
    ))
    assert fmv.main([]) == 0
    out = capsys.readouterr().out
    assert out == "BASE=master  IID=42  STATE=opened  DRAFT=false  SOURCE=feat/x\n"


def test_draft_true_prints_lowercase(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        '{"target_branch": "master", "iid": 1, "state": "opened", '
        '"draft": true, "source_branch": "feat/y"}'
    ))
    assert fmv.main([]) == 0
    assert "DRAFT=true" in capsys.readouterr().out


def test_empty_stdin_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert fmv.main([]) == 1
    assert capsys.readouterr().out == ""


def test_invalid_json_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert fmv.main([]) == 1


def test_non_object_json_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("null"))
    assert fmv.main([]) == 1
    assert capsys.readouterr().out == ""
