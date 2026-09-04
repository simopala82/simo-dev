"""Tests for lib/detect_state.py."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "detect_state.py"


def run(repo_root):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(repo_root)],
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout, result.stderr


def test_empty_repo_reports_nothing_configured(tmp_path):
    rc, out, err = run(tmp_path)
    assert rc == 0
    data = json.loads(out)
    assert data["claude_md"] == {"exists": False, "path": None}
    assert data["agent_skills_section_present"] is None
    assert data["docs_agents"] == {
        "issue_tracker": False,
        "triage_labels": False,
        "domain": False,
    }
    assert data["no_tracker_marker"] is False
    assert data["context_md"] is False
    assert data["context_map_md"] is False
    assert data["docs_adr"] is False


def test_claude_md_without_agent_skills_section(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# My Project\n\nSome instructions.\n")
    rc, out, err = run(tmp_path)
    assert rc == 0
    data = json.loads(out)
    assert data["claude_md"]["exists"] is True
    assert data["claude_md"]["path"] == str(tmp_path / "CLAUDE.md")
    assert data["agent_skills_section_present"] is False


def test_claude_md_with_agent_skills_section(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "# My Project\n\n## Agent skills\n\nSome content.\n"
    )
    rc, out, err = run(tmp_path)
    assert rc == 0
    data = json.loads(out)
    assert data["agent_skills_section_present"] is True


def test_agent_skills_heading_case_and_level_insensitive(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# agent SKILLS\n\nContent.\n")
    rc, out, err = run(tmp_path)
    data = json.loads(out)
    assert data["agent_skills_section_present"] is True


def test_docs_agents_and_domain_docs_detected(tmp_path):
    docs_agents = tmp_path / "docs" / "agents"
    docs_agents.mkdir(parents=True)
    (docs_agents / "issue-tracker.md").write_text("x")
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "CONTEXT.md").write_text("x")
    rc, out, err = run(tmp_path)
    assert rc == 0
    data = json.loads(out)
    assert data["docs_agents"] == {
        "issue_tracker": True,
        "triage_labels": False,
        "domain": False,
    }
    assert data["docs_adr"] is True
    assert data["context_md"] is True
    assert data["context_map_md"] is False


def test_no_tracker_marker_detected(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "no-tracker").write_text("")
    rc, out, err = run(tmp_path)
    assert rc == 0
    data = json.loads(out)
    assert data["no_tracker_marker"] is True


def test_missing_repo_root_errors(tmp_path):
    missing = tmp_path / "does-not-exist"
    rc, out, err = run(missing)
    assert rc == 2


def test_missing_argument_errors():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True,
    )
    assert result.returncode == 2
