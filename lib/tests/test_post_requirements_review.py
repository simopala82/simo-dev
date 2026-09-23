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


def test_rc4_maps_to_prior_block_skipped(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    target = tmp_path / "prior.py"
    target.write_text("import sys\nsys.exit(4)\n")
    prr.tc.set_field("PROJ", "requirements_review_cmd", f"python3 {target}", path)
    monkeypatch.setattr(prr.tc, "config_path", lambda: path)
    body = tmp_path / "body.txt"
    body.write_text("- one\n")
    assert prr.post("PROJ-1", str(body)) == "skipped: prior requirements block present on PROJ-1"


def test_replace_flag_forwarded_only_when_set(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    target = tmp_path / "record.py"
    out = tmp_path / "argv.txt"
    target.write_text(f"import sys\nopen({str(out)!r}, 'a').write(repr(sys.argv[1:]) + '\\n')\n")
    prr.tc.set_field("PROJ", "requirements_review_cmd", f"python3 {target}", path)
    monkeypatch.setattr(prr.tc, "config_path", lambda: path)
    body = tmp_path / "body.txt"
    body.write_text("- one\n")
    assert prr.post("PROJ-1", str(body)) == "posted: PROJ-1"
    assert prr.post("PROJ-1", str(body), replace=True) == "posted: PROJ-1"
    lines = out.read_text().splitlines()
    assert lines == [repr(["PROJ-1", str(body)]), repr(["PROJ-1", str(body), "--replace"])]


def test_cli_replace_flag_forwarded(tmp_path, monkeypatch, capsys):
    path = tmp_path / "tracker.json"
    target = tmp_path / "record.py"
    out = tmp_path / "argv.txt"
    target.write_text(f"import sys\nopen({str(out)!r}, 'w').write(repr(sys.argv[1:]))\n")
    prr.tc.set_field("PROJ", "requirements_review_cmd", f"python3 {target}", path)
    monkeypatch.setattr(prr.tc, "config_path", lambda: path)
    body = tmp_path / "body.txt"
    body.write_text("- one\n")
    assert prr.main(["PROJ-1", str(body), "--replace"]) == 0
    assert capsys.readouterr().out.strip() == "posted: PROJ-1"
    assert out.read_text() == repr(["PROJ-1", str(body), "--replace"])


def test_cli_bad_third_arg_exits_1():
    assert prr.main(["PROJ-1", "body.txt", "--force"]) == 1
