"""Tests for lib/post_requirements_review.py (single requirements-write-back call site)."""
import importlib.util
from pathlib import Path

LIB = Path(__file__).parent.parent / "post_requirements_review.py"

spec = importlib.util.spec_from_file_location("post_requirements_review", LIB)
prr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prr)


def test_no_key_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(prr.tc, "config_path", lambda: tmp_path / "tracker.json")
    assert prr.post("", str(tmp_path / "body.txt")) == "skipped: no ticket key"


def test_unconfigured_prefix_skips(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    monkeypatch.setattr(prr.tc, "config_path", lambda: path)
    assert prr.post("PROJ-1", str(tmp_path / "body.txt")) == "skipped: requirements_review_cmd not configured for PROJ"


def test_configured_and_success_posts(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    target = tmp_path / "echo_ok.py"
    target.write_text("import sys\nsys.exit(0)\n")
    prr.tc.set_field("PROJ", "requirements_review_cmd", f"python3 {target}", path)
    monkeypatch.setattr(prr.tc, "config_path", lambda: path)
    body = tmp_path / "body.txt"
    body.write_text("- one\n- two\n")
    assert prr.post("PROJ-1", str(body)) == "posted: PROJ-1"


def test_configured_and_failing_reports_rc(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    target = tmp_path / "fail.py"
    target.write_text("import sys\nsys.exit(5)\n")
    prr.tc.set_field("PROJ", "requirements_review_cmd", f"python3 {target}", path)
    monkeypatch.setattr(prr.tc, "config_path", lambda: path)
    body = tmp_path / "body.txt"
    body.write_text("- one\n")
    assert prr.post("PROJ-1", str(body)) == "failed: requirements_review_cmd exited 5 for PROJ-1"


def test_prefix_derived_uppercase_from_lowercase_key(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    monkeypatch.setattr(prr.tc, "config_path", lambda: path)
    assert prr.post("proj-1", str(tmp_path / "body.txt")) == "skipped: requirements_review_cmd not configured for PROJ"


def test_cli_usage_error_exits_1(capsys):
    rc = prr.main(["only-one-arg"])
    assert rc == 1
