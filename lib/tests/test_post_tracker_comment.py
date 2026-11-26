"""Tests for lib/post_tracker_comment.py (single post-a-gate-comment call site)."""
import importlib.util
from pathlib import Path

LIB = Path(__file__).parent.parent / "post_tracker_comment.py"

spec = importlib.util.spec_from_file_location("post_tracker_comment", LIB)
ptc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ptc)


def test_no_key_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(ptc.tc, "config_path", lambda: tmp_path / "tracker.json")
    assert ptc.post("", str(tmp_path / "body.txt")) == "skipped: no ticket key"


def test_unconfigured_prefix_skips(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    monkeypatch.setattr(ptc.tc, "config_path", lambda: path)
    assert ptc.post("PROJ-1", str(tmp_path / "body.txt")) == "skipped: comment_cmd not configured for PROJ"


def test_configured_and_success_posts(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    target = tmp_path / "echo_ok.py"
    target.write_text("import sys\nsys.exit(0)\n")
    ptc.tc.set_field("PROJ", "comment_cmd", f"python3 {target}", path)
    monkeypatch.setattr(ptc.tc, "config_path", lambda: path)
    body = tmp_path / "body.txt"
    body.write_text("hello")
    assert ptc.post("PROJ-1", str(body)) == "posted: PROJ-1"


def test_configured_and_failing_reports_rc(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    target = tmp_path / "fail.py"
    target.write_text("import sys\nsys.exit(5)\n")
    ptc.tc.set_field("PROJ", "comment_cmd", f"python3 {target}", path)
    monkeypatch.setattr(ptc.tc, "config_path", lambda: path)
    body = tmp_path / "body.txt"
    body.write_text("hello")
    assert ptc.post("PROJ-1", str(body)) == "failed: comment_cmd exited 5 for PROJ-1"


def test_prefix_derived_uppercase_from_lowercase_key(tmp_path, monkeypatch):
    path = tmp_path / "tracker.json"
    monkeypatch.setattr(ptc.tc, "config_path", lambda: path)
    assert ptc.post("proj-1", str(tmp_path / "body.txt")) == "skipped: comment_cmd not configured for PROJ"


def test_cli_usage_error_exits_1(capsys):
    rc = ptc.main(["only-one-arg"])
    assert rc == 1


def _repo_on(tmp_path, branch):
    import os
    import subprocess
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True, env=env)
    return repo


def test_branch_of_derives_the_key_from_the_repo_branch(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEV_FLOW_NO_TRACKER", raising=False)
    monkeypatch.delenv("DEV_FLOW_JIRA_PROJECTS", raising=False)
    path = tmp_path / "tracker.json"
    target = tmp_path / "echo_ok.py"
    target.write_text("import sys\nsys.exit(0)\n")
    ptc.tc.set_field("PROJ", "comment_cmd", f"python3 {target}", path)
    monkeypatch.setattr(ptc.tc, "config_path", lambda: path)
    repo = _repo_on(tmp_path, "feat/proj-7-thing")
    body = tmp_path / "body.txt"
    body.write_text("hello")
    assert ptc.main(["--branch-of", str(repo), str(body)]) == 0
    assert capsys.readouterr().out.strip() == "posted: PROJ-7"


def test_branch_of_honours_the_no_tracker_marker(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEV_FLOW_NO_TRACKER", raising=False)
    monkeypatch.setattr(ptc.tc, "config_path", lambda: tmp_path / "tracker.json")
    repo = _repo_on(tmp_path, "feat/proj-7-thing")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "no-tracker").write_text("")
    assert ptc.main(["--branch-of", str(repo), str(tmp_path / "body.txt")]) == 0
    assert capsys.readouterr().out.strip() == "skipped: no ticket key"


def test_branch_of_on_a_branch_without_key_skips(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEV_FLOW_NO_TRACKER", raising=False)
    monkeypatch.setattr(ptc.tc, "config_path", lambda: tmp_path / "tracker.json")
    repo = _repo_on(tmp_path, "docs/no-key-here")
    assert ptc.main(["--branch-of", str(repo), str(tmp_path / "body.txt")]) == 0
    assert capsys.readouterr().out.strip() == "skipped: no ticket key"


def test_branch_of_usage_error_exits_1():
    assert ptc.main(["--branch-of", "/x"]) == 1


def test_branch_of_honours_the_ticket_key_override(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEV_FLOW_NO_TRACKER", raising=False)
    monkeypatch.delenv("DEV_FLOW_JIRA_PROJECTS", raising=False)
    path = tmp_path / "tracker.json"
    target = tmp_path / "echo_ok.py"
    target.write_text("import sys\nsys.exit(0)\n")
    ptc.tc.set_field("OTHER", "comment_cmd", f"python3 {target}", path)
    monkeypatch.setattr(ptc.tc, "config_path", lambda: path)
    repo = _repo_on(tmp_path, "feat/proj-7-thing")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "ticket-key-override").write_text("OTHER-9\n")
    body = tmp_path / "body.txt"
    body.write_text("hello")
    assert ptc.main(["--branch-of", str(repo), str(body)]) == 0
    assert capsys.readouterr().out.strip() == "posted: OTHER-9"


def test_branch_of_a_missing_dir_is_not_reported_as_no_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEV_FLOW_NO_TRACKER", raising=False)
    monkeypatch.setattr(ptc.tc, "config_path", lambda: tmp_path / "tracker.json")
    missing = tmp_path / "nope"
    assert ptc.main(["--branch-of", str(missing), str(tmp_path / "body.txt")]) == 0
    assert capsys.readouterr().out.strip() == f"skipped: no branch at {missing}"
