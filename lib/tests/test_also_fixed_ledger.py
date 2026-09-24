"""Tests for lib/also_fixed_ledger.py."""
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "also_fixed_ledger.py"


def run_lib(args):
    result = subprocess.run([sys.executable, str(LIB)] + args, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feat/x", str(repo)], check=True)
    return repo


def pending(repo):
    return run_lib(["pending", "--repo", str(repo)])[1].splitlines()


def section(repo):
    return run_lib(["section", "--repo", str(repo)])[1]


def test_ledger_lives_in_git_dir(tmp_path):
    repo = make_repo(tmp_path)
    assert run_lib(["add", "--repo", str(repo), "`a.py` — x — y"])[0] == 0
    assert (repo / ".git" / "simo-dev-also-fixed.feat-x.md").read_text() == "`a.py` — x — y\n"


def test_each_branch_has_its_own_ledger(tmp_path):
    repo = make_repo(tmp_path)
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y"])
    subprocess.run(["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/feat/other"], check=True)
    assert pending(repo) == []
    run_lib(["add", "--repo", str(repo), "`b.py` — z — w"])
    assert pending(repo) == ["`b.py` — z — w"]
    subprocess.run(["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/feat/x"], check=True)
    assert pending(repo) == ["`a.py` — x — y"]


def make_commit(repo):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c"], check=True)


def test_each_linked_worktree_has_its_own_ledger(tmp_path):
    repo = make_repo(tmp_path)
    make_commit(repo)
    other = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/y", str(other)], check=True)
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y"])
    run_lib(["add", "--repo", str(other), "`b.py` — z — w"])
    assert pending(repo) == ["`a.py` — x — y"]
    assert pending(other) == ["`b.py` — z — w"]
    assert (repo / ".git" / "worktrees" / "wt" / "simo-dev-also-fixed.feat-y.md").exists()


def test_detached_head_uses_detached_key(tmp_path):
    repo = make_repo(tmp_path)
    make_commit(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach"], check=True)
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y"])
    assert (repo / ".git" / "simo-dev-also-fixed.detached.md").read_text() == "`a.py` — x — y\n"


def test_add_dedupes_normalizes_and_marks_pending(tmp_path):
    repo = make_repo(tmp_path)
    assert run_lib(["add", "--repo", str(repo), "`a.py` — x — y", "  ", "- `b.py` — z — w"])[0] == 0
    assert run_lib(["add", "--repo", str(repo), "* `a.py` — x — y", "`c.py` — it's quoted — ok", "`c.py` — it's quoted — ok"])[0] == 0
    assert pending(repo) == ["`a.py` — x — y", "`b.py` — z — w", "`c.py` — it's quoted — ok"]


def test_section_renders_full_list_or_nothing(tmp_path):
    repo = make_repo(tmp_path)
    assert section(repo) == ""
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y", "`b.py` — z — w"])
    assert section(repo) == "### Also fixed\n\n- `a.py` — x — y\n- `b.py` — z — w\n"


def test_commit_done_empties_pending_but_keeps_full_list(tmp_path):
    repo = make_repo(tmp_path)
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y"])
    assert run_lib(["commit-done", "--repo", str(repo)])[0] == 0
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y", "`b.py` — z — w"])
    assert pending(repo) == ["`b.py` — z — w"]
    assert section(repo) == "### Also fixed\n\n- `a.py` — x — y\n- `b.py` — z — w\n"


def test_drop_removes_line_from_both_lists(tmp_path):
    repo = make_repo(tmp_path)
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y", "`b.py` — z — w"])
    assert run_lib(["drop", "--repo", str(repo), "`a.py` — x — y"])[0] == 0
    assert pending(repo) == ["`b.py` — z — w"]
    assert section(repo) == "### Also fixed\n\n- `b.py` — z — w\n"


def test_drop_of_unlisted_line_exits_1_and_names_it(tmp_path):
    repo = make_repo(tmp_path)
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y"])
    rc, out, err = run_lib(["drop", "--repo", str(repo), "`a.py` — x — reworded"])
    assert rc == 1
    assert "not listed: `a.py` — x — reworded" in err
    assert pending(repo) == ["`a.py` — x — y"]


def test_clear_empties_both_lists(tmp_path):
    repo = make_repo(tmp_path)
    run_lib(["add", "--repo", str(repo), "`a.py` — x — y"])
    assert run_lib(["clear", "--repo", str(repo)])[0] == 0
    assert pending(repo) == []
    assert section(repo) == ""


def test_non_repo_exits_2(tmp_path):
    rc, out, err = run_lib(["pending", "--repo", str(tmp_path)])
    assert rc == 2
    assert "not a git repo" in err


def test_relative_or_empty_repo_exits_2():
    for repo in ["", "some/relative/dir"]:
        rc, out, err = run_lib(["pending", "--repo", repo])
        assert rc == 2
        assert "absolute path" in err
