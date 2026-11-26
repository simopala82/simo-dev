"""Tests for lib/check_mr_draft.py (plan **MR draft:** detector)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "check_mr_draft.py"

spec = importlib.util.spec_from_file_location("check_mr_draft", LIB)
cmd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmd)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo, rel, body, msg="c"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    _git(repo, "add", rel)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    _commit(r, "README.md", "x\n")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(r, "switch", "-q", "-c", "feat/x")
    return r


def test_branch_plan_declares_yes(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**Goal:** g\n**MR draft:** yes\n")
    assert cmd.wants_draft(repo, "main") is True


def test_branch_plan_declares_no(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**MR draft:** no\n")
    assert cmd.wants_draft(repo, "main") is False


def test_branch_plan_field_absent(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**Goal:** g\n")
    assert cmd.wants_draft(repo, "main") is False


def test_no_plan_on_branch(repo):
    _commit(repo, "src.txt", "y\n")
    assert cmd.wants_draft(repo, "main") is False


def test_plan_already_on_base_is_ignored(tmp_path):
    r = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    _commit(r, "docs/plans/2026-01-01-old.md", "**MR draft:** yes\n")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(r, "switch", "-q", "-c", "feat/x")
    _commit(r, "src.txt", "y\n")
    assert cmd.wants_draft(r, "main") is False


def test_uncommitted_edit_is_not_read(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**MR draft:** no\n")
    (repo / "docs/plans/2026-01-01-example.md").write_text("**MR draft:** yes\n")
    assert cmd.wants_draft(repo, "main") is False


def test_plan_outside_docs_plans_is_ignored(repo):
    _commit(repo, "notes/plan.md", "**MR draft:** yes\n")
    assert cmd.wants_draft(repo, "main") is False


def test_unknown_target_raises(repo):
    with pytest.raises(RuntimeError):
        cmd.wants_draft(repo, "no-such-branch")


def test_cli_prints_1(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**MR draft:** YES\n")
    res = subprocess.run([sys.executable, str(LIB), "--repo", str(repo), "--target", "main"],
                         capture_output=True, text=True)
    assert (res.returncode, res.stdout) == (0, "1")


def test_body_example_is_ignored(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**Goal:** g\n\n## Tasks\n**MR draft:** yes\n")
    assert cmd.wants_draft(repo, "main") is False


def test_value_on_next_line_does_not_match(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**MR draft:**\nyes\n")
    assert cmd.wants_draft(repo, "main") is False


def test_uncommitted_yes_plan_warns(repo):
    (repo / "docs/plans").mkdir(parents=True)
    (repo / "docs/plans/2026-01-01-example.md").write_text("**MR draft:** yes\n")
    res = subprocess.run([sys.executable, str(LIB), "--repo", str(repo), "--target", "main"],
                         capture_output=True, text=True)
    assert (res.returncode, res.stdout) == (3, "0")
    assert "WARNING: docs/plans/2026-01-01-example.md declares MR draft: yes" in res.stderr


def test_name_with_space_is_read(repo):
    _commit(repo, "docs/plans/2026-01-01-a b.md", "**MR draft:** yes\n")
    assert cmd.wants_draft(repo, "main") is True


def test_malformed_value_warns(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**MR draft:** `yes`\n")
    res = subprocess.run([sys.executable, str(LIB), "--repo", str(repo), "--target", "main"],
                         capture_output=True, text=True)
    assert (res.returncode, res.stdout) == (3, "0")
    assert "WARNING: docs/plans/2026-01-01-example.md has an MR draft value other than a bare yes/no" in res.stderr


def test_ignored_yes_plan_warns(repo):
    (repo / ".gitignore").write_text("docs/plans/\n")
    (repo / "docs/plans").mkdir(parents=True)
    (repo / "docs/plans/2026-01-01-example.md").write_text("**MR draft:** yes\n")
    res = subprocess.run([sys.executable, str(LIB), "--repo", str(repo), "--target", "main"],
                         capture_output=True, text=True)
    assert (res.returncode, res.stdout) == (3, "0")
    assert "WARNING: docs/plans/2026-01-01-example.md declares MR draft: yes" in res.stderr


def _cli(repo):
    return subprocess.run([sys.executable, str(LIB), "--repo", str(repo), "--target", "main"],
                          capture_output=True, text=True)


def test_modified_tracked_yes_plan_warns(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**MR draft:** no\n")
    (repo / "docs/plans/2026-01-01-example.md").write_text("**MR draft:** yes\n")
    res = _cli(repo)
    assert (res.returncode, res.stdout) == (3, "0")
    assert "WARNING: docs/plans/2026-01-01-example.md declares MR draft: yes" in res.stderr


def test_staged_rename_reads_new_path(repo):
    _commit(repo, "docs/plans/2026-01-01-old.md", "**MR draft:** no\n" + "line\n" * 20)
    _git(repo, "mv", "docs/plans/2026-01-01-old.md", "docs/plans/2026-01-01-new.md")
    res = _cli(repo)
    assert (res.returncode, res.stdout) == (0, "0"), res.stderr
    (repo / "docs/plans/2026-01-01-new.md").write_text("**MR draft:** yes\n" + "line\n" * 20)
    res = _cli(repo)
    assert (res.returncode, res.stdout) == (3, "0")
    assert "WARNING: docs/plans/2026-01-01-new.md declares MR draft: yes" in res.stderr


def test_uncommitted_yes_ignored_when_answer_is_1(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**MR draft:** yes\n")
    (repo / "docs/plans/2026-01-02-other.md").write_text("**MR draft:** yes\n")
    assert (_cli(repo).returncode, _cli(repo).stdout) == (0, "1")


def test_uncommitted_malformed_plan_warns(repo):
    (repo / "docs/plans").mkdir(parents=True)
    (repo / "docs/plans/2026-01-01-example.md").write_text("**MR draft:** yes (blocked)\n")
    res = _cli(repo)
    assert (res.returncode, res.stdout) == (3, "0")
    assert "has an MR draft value other than a bare yes/no but is not committed" in res.stderr


def test_base_plan_edited_on_branch_is_ignored(tmp_path):
    r = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    _commit(r, "docs/plans/2026-01-01-old.md", "**MR draft:** yes\n")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(r, "switch", "-q", "-c", "feat/x")
    _commit(r, "docs/plans/2026-01-01-old.md", "**MR draft:** yes\n\n## Tasks\nresynced\n")
    (r / "docs/plans/2026-01-01-old.md").write_text("**MR draft:** yes\n\n## Tasks\nresynced again\n")
    assert (_cli(r).returncode, _cli(r).stdout) == (0, "0")


def test_fenced_example_in_header_is_ignored(repo):
    _commit(repo, "docs/plans/2026-01-01-example.md", "**Goal:** g\n```markdown\n**MR draft:** maybe\n```\n")
    assert (_cli(repo).returncode, _cli(repo).stdout) == (0, "0")


def test_cli_git_failure_rc1(repo):
    res = subprocess.run([sys.executable, str(LIB), "--repo", str(repo), "--target", "no-such-branch"],
                         capture_output=True, text=True)
    assert res.returncode == 1
    assert res.stdout == ""
    assert "check_mr_draft" in res.stderr
