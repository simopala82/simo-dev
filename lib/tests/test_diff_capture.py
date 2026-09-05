"""Tests for lib/diff_capture.py (the diff capture context gate)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "diff_capture.py"

# No CLAUDE.md "sixth case" risk here: this script takes no config path
# argument that could persist a Path(__file__)-derived value -- `LIB` above
# is only used to load the module for these tests, never written anywhere.
spec = importlib.util.spec_from_file_location("diff_capture", LIB)
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "--quiet")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "a.txt").write_text("line1\nline2\n")
    _git(r, "add", "a.txt")
    _git(r, "commit", "--quiet", "-m", "initial")
    return r


def test_line_count_parses_numstat(repo):
    (repo / "a.txt").write_text("line1\nline2\nline3\n")
    n = dc._line_count(str(repo), "a.txt", ["HEAD"], is_untracked=False)
    assert n == 1


def test_line_count_zero_for_unchanged_file(repo):
    n = dc._line_count(str(repo), "a.txt", ["HEAD"], is_untracked=False)
    assert n == 0


def test_capture_raises_giterror_on_bad_ref(repo):
    with pytest.raises(dc.GitError):
        dc.capture(str(repo), ["not-a-real-ref"], include_untracked=False)


def test_capture_includes_untracked_when_asked(repo):
    (repo / "b.txt").write_text("new file\n")
    out = dc.capture(str(repo), ["HEAD"], include_untracked=True)
    assert "new file" in out


def test_capture_excludes_untracked_by_default(repo):
    (repo / "b.txt").write_text("new file\n")
    out = dc.capture(str(repo), ["HEAD"], include_untracked=False)
    assert "new file" not in out


def test_capture_includes_untracked_file_with_unicode_name(repo):
    (repo / "café.txt").write_text("bonjour\n")
    out = dc.capture(str(repo), ["HEAD"], include_untracked=True)
    assert "bonjour" in out


def test_capture_handles_non_utf8_content_without_raising(repo):
    (repo / "a.txt").write_bytes(b"line1\nline2\n\xff\xfe\n")
    out = dc.capture(str(repo), ["HEAD"], include_untracked=False)
    assert isinstance(out, str)


def test_main_missing_dashdash_returns_2(capsys):
    rc = dc.main([str(Path("/x"))])
    assert rc == 2
    assert "usage" in capsys.readouterr().err


def test_main_missing_diff_args_returns_2(repo, capsys):
    rc = dc.main([str(repo), "--"])
    assert rc == 2


def test_main_missing_repo_returns_2(capsys):
    rc = dc.main(["--", "HEAD"])
    assert rc == 2


def test_main_bad_threshold_value_returns_2(repo, capsys):
    rc = dc.main([str(repo), "--threshold", "notanumber", "--", "HEAD"])
    assert rc == 2


def test_main_threshold_missing_value_returns_2(repo, capsys):
    rc = dc.main([str(repo), "--threshold", "--", "HEAD"])
    assert rc == 2


def test_main_small_diff_prints_full_content(repo, capsys):
    (repo / "a.txt").write_text("line1\nline2\nline3\n")
    rc = dc.main([str(repo), "--", "HEAD"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "+line3" in out
    assert "Full diff written to:" not in out


def test_main_large_diff_writes_scratch_file_and_summarizes(repo, capsys):
    (repo / "a.txt").write_text("line1\nline2\nline3\n")
    rc = dc.main([str(repo), "--threshold", "1", "--", "HEAD"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "a.txt" in out
    assert "Full diff written to:" in out
    written_path = out.strip().splitlines()[-1].split("Full diff written to: ")[1].split(" (")[0]
    assert Path(written_path).exists()
    assert "+line3" in Path(written_path).read_text()


def test_main_large_diff_skips_slice_for_files_over_1000_lines(repo, capsys):
    (repo / "a.txt").write_text("\n".join(f"line{i}" for i in range(2000)))
    rc = dc.main([str(repo), "--threshold", "1", "--", "HEAD"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--- a.txt ---" not in out


def test_main_large_diff_lists_large_untracked_file_without_inlining(repo, capsys):
    (repo / "big.txt").write_text("\n".join(f"line{i}" for i in range(2000)))
    rc = dc.main([str(repo), "--threshold", "1", "--include-untracked", "--", "HEAD"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "big.txt" in out
    assert "not inlined" in out


def test_main_git_failure_returns_1_no_stdout_diff(repo, capsys):
    rc = dc.main([str(repo), "--", "not-a-real-ref"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err != ""
