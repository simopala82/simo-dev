"""Tests for lib/local_queue_fetch.py (print ranked local-tracker issue candidates)."""
import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "local_queue_fetch.py"

spec = importlib.util.spec_from_file_location("local_queue_fetch", LIB)
lqf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lqf)


def _write_issue(root, feature, name, body):
    d = root / ".scratch" / feature / "issues"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def test_usage_error_missing_repo_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        lqf.main([])
    assert exc_info.value.code == 2


def test_no_scratch_dir_prints_nothing(tmp_path, capsys):
    assert lqf.main(["--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""


def test_filters_to_ready_for_agent_and_sorts_by_priority(tmp_path, capsys):
    _write_issue(tmp_path, "foo", "01-first.md", "Status: needs-triage\nPriority: P0\n\n# Should be excluded\n")
    _write_issue(tmp_path, "foo", "02-second.md", "Status: ready-for-agent\nPriority: P1\n\n# Second\n")
    _write_issue(tmp_path, "foo", "03-third.md", "Status: ready-for-agent\nPriority: P0\n\n# Third\n")
    assert lqf.main(["--repo", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out == ".scratch/foo/issues/03-third.md\tThird\n.scratch/foo/issues/02-second.md\tSecond\n"


def test_missing_priority_sorts_last_tiebreak_on_filename(tmp_path, capsys):
    _write_issue(tmp_path, "foo", "01-no-priority.md", "Status: ready-for-agent\n\n# No priority\n")
    _write_issue(tmp_path, "foo", "02-p2.md", "Status: ready-for-agent\nPriority: P2\n\n# Has P2\n")
    assert lqf.main(["--repo", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out == ".scratch/foo/issues/02-p2.md\tHas P2\n.scratch/foo/issues/01-no-priority.md\tNo priority\n"


def test_missing_heading_falls_back_to_filename(tmp_path, capsys):
    _write_issue(tmp_path, "foo", "01-untitled.md", "Status: ready-for-agent\nPriority: P0\n")
    assert lqf.main(["--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ".scratch/foo/issues/01-untitled.md\t01-untitled.md\n"
