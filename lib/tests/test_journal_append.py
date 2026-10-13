"""Tests for lib/journal_append.py (dev-flow journal writer)."""
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "journal_append.py"

BLOCK = "## 2026-10-12 · repo · fix/x · mode=direct\n- ticket: none\n"


def run(journal, stdin):
    return subprocess.run(
        [sys.executable, str(LIB), "--journal", str(journal)],
        input=stdin.encode("utf-8"), capture_output=True,
    )


def test_default_path_is_home_claude_state(tmp_path):
    out = subprocess.run(
        [sys.executable, str(LIB)], input=BLOCK.encode("utf-8"), capture_output=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert out.returncode == 0, out.stderr
    assert (tmp_path / ".claude" / "state" / "dev-flow-journal.md").read_text(encoding="utf-8") == BLOCK


def test_newline_only_file_gets_no_extra_blank_line(tmp_path):
    journal = tmp_path / "dev-flow-journal.md"
    journal.write_text("\n", encoding="utf-8")
    assert run(journal, BLOCK).returncode == 0
    assert journal.read_text(encoding="utf-8") == "\n" + BLOCK


def test_existing_empty_file_gets_no_separator(tmp_path):
    journal = tmp_path / "dev-flow-journal.md"
    journal.write_text("", encoding="utf-8")
    assert run(journal, BLOCK).returncode == 0
    assert journal.read_text(encoding="utf-8") == BLOCK


def test_non_utf8_stdin_crashes_without_writing(tmp_path):
    journal = tmp_path / "state" / "dev-flow-journal.md"
    out = subprocess.run(
        [sys.executable, str(LIB), "--journal", str(journal)],
        input=b"## bad \xff\n", capture_output=True,
    )
    assert out.returncode == 1
    assert not journal.parent.exists()


def test_step_8_5_code_never_names_the_state_dir():
    # A `.claude/state` path on a command line brings back the non-persistable "Allow once"
    # prompt in sessions outside this repo; the journal must go through this script.
    post_merge = LIB.parent.parent / "skills" / "mr-followup" / "lib" / "post-merge.md"
    in_section = in_fence = False
    code = []
    for line in post_merge.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## "):
            in_section = line.startswith("## Dev-flow journal (step 8.5)")
            continue
        if in_section and in_fence:
            code.append(line)
    assert any('lib/journal_append.py" <<' in l for l in code)
    assert not [l for l in code if ".claude/state" in l]


def test_creates_missing_parent_dir(tmp_path):
    journal = tmp_path / "state" / "dev-flow-journal.md"
    out = run(journal, BLOCK)
    assert out.returncode == 0, out.stderr
    assert journal.read_text(encoding="utf-8") == BLOCK
    assert out.stdout.decode().strip() == f"appended 2 lines to {journal}"


def test_appends_with_one_blank_line_separator(tmp_path):
    journal = tmp_path / "dev-flow-journal.md"
    journal.write_text("## older\n- notes: x\n", encoding="utf-8")
    out = run(journal, "\n\n" + BLOCK.rstrip("\n"))
    assert out.returncode == 0, out.stderr
    assert journal.read_text(encoding="utf-8") == "## older\n- notes: x\n\n" + BLOCK


def test_no_extra_blank_line_when_file_already_ends_blank(tmp_path):
    journal = tmp_path / "dev-flow-journal.md"
    journal.write_text("## older\n\n", encoding="utf-8")
    assert run(journal, BLOCK).returncode == 0
    assert journal.read_text(encoding="utf-8") == "## older\n\n" + BLOCK


def test_file_without_trailing_newline(tmp_path):
    journal = tmp_path / "dev-flow-journal.md"
    journal.write_text("## older", encoding="utf-8")
    assert run(journal, BLOCK).returncode == 0
    assert journal.read_text(encoding="utf-8") == "## older\n\n" + BLOCK


def test_empty_stdin_rc4_and_no_write(tmp_path):
    journal = tmp_path / "state" / "dev-flow-journal.md"
    out = run(journal, "  \n\n")
    assert out.returncode == 4
    assert b"empty" in out.stderr
    assert not journal.parent.exists()
