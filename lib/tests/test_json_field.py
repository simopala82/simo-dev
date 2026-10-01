"""Tests for lib/json_field.py (replaces '<path> // <default>' shell field-extraction invocations)."""
import importlib.util
import io
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "json_field.py"

spec = importlib.util.spec_from_file_location("json_field", LIB)
jf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jf)


def test_nested_dotted_path(tmp_path, capsys):
    f = tmp_path / "mr_ctx.json"
    f.write_text('{"branch_meta": {"target": "develop"}}')
    assert jf.main([str(f), "branch_meta.target"]) == 0
    assert capsys.readouterr().out == "develop"


def test_missing_path_no_default_prints_empty(tmp_path, capsys):
    f = tmp_path / "mr_ctx.json"
    f.write_text('{"branch_meta": {}}')
    assert jf.main([str(f), "branch_meta.target"]) == 0
    assert capsys.readouterr().out == ""


def test_missing_path_with_default(tmp_path, capsys):
    f = tmp_path / "review_ctx.json"
    f.write_text('{}')
    assert jf.main([str(f), "jira_key", ""]) == 0
    assert capsys.readouterr().out == ""
    f.write_text('{"jira_key": null}')
    assert jf.main([str(f), "jira_key", "FALLBACK"]) == 0
    assert capsys.readouterr().out == "FALLBACK"


def test_multi_path_fallback_chain(tmp_path, capsys):
    f = tmp_path / "review_meta.json"
    f.write_text('{"headRefName": "feat/x"}')
    assert jf.main([str(f), "source_branch", "headRefName", ""]) == 0
    assert capsys.readouterr().out == "feat/x"


def test_boolean_true_prints_lowercase(tmp_path, capsys):
    f = tmp_path / "review_pres.json"
    f.write_text('{"ticket_drift": true}')
    assert jf.main([str(f), "ticket_drift", "false"]) == 0
    assert capsys.readouterr().out == "true"


def test_boolean_false_falls_through_to_default(tmp_path, capsys):
    f = tmp_path / "review_pres.json"
    f.write_text('{"ticket_drift": false}')
    assert jf.main([str(f), "ticket_drift", "false"]) == 0
    assert capsys.readouterr().out == "false"


def test_stdin_via_dash(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"username": "someuser"}'))
    assert jf.main(["-", "username"]) == 0
    assert capsys.readouterr().out == "someuser"


def test_invalid_json_exits_1(tmp_path, capsys):
    f = tmp_path / "broken.json"
    f.write_text("not json")
    assert jf.main([str(f), "x"]) == 1
    assert "json_field.py:" in capsys.readouterr().err


def test_missing_file_exits_1(tmp_path, capsys):
    f = tmp_path / "does_not_exist.json"
    assert jf.main([str(f), "x"]) == 1
    assert "json_field.py:" in capsys.readouterr().err


def test_object_valued_path_exits_1(tmp_path, capsys):
    f = tmp_path / "mr_ctx.json"
    f.write_text('{"branch_meta": {"target": "develop"}}')
    assert jf.main([str(f), "branch_meta"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "json_field.py:" in err


def test_array_valued_path_exits_1(tmp_path, capsys):
    f = tmp_path / "review_pres.json"
    f.write_text('{"warnings": ["a", "b"]}')
    assert jf.main([str(f), "warnings"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "json_field.py:" in err


def test_non_ascii_scalar_from_file_on_cp1252_stdout(tmp_path):
    from cp1252_run import run_cp1252
    src = tmp_path / "in.json"
    src.write_text('{"title": "old → new ★"}', encoding="utf-8")
    script = Path(__file__).parent.parent / "json_field.py"
    result = run_cp1252(script, [str(src), "title"])
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8") == "old → new ★"


def test_non_ascii_scalar_from_stdin_on_cp1252_stdout():
    from cp1252_run import run_cp1252
    script = Path(__file__).parent.parent / "json_field.py"
    result = run_cp1252(script, ["-", "title"], stdin='{"title": "old → new ★"}'.encode("utf-8"))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8") == "old → new ★"


def test_non_utf8_file_reports_error_without_traceback(tmp_path):
    from cp1252_run import run_cp1252
    src = tmp_path / "in.json"
    src.write_bytes(b'{"title": "caf\xe9"}')
    script = Path(__file__).parent.parent / "json_field.py"
    result = run_cp1252(script, [str(src), "title"])
    assert result.returncode == 1
    assert result.stderr.decode("utf-8").startswith("json_field.py: ")
    assert b"Traceback" not in result.stderr
