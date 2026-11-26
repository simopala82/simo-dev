"""Tests for lib/resume_point.py."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "resume_point.py"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _write(repo, rel, text="x"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(repo, rel, text="x"):
    _write(repo, rel, text)
    _git(repo, "add", rel)
    _git(repo, "commit", "-qm", f"add {rel}")


def _run(repo, *args):
    r = subprocess.run([sys.executable, str(LIB), "--repo", str(repo), *args], capture_output=True, text=True)
    return r.returncode, (json.loads(r.stdout) if r.returncode == 0 else None), r.stderr


@pytest.fixture
def work(tmp_path):
    up, work = tmp_path / "up.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _commit(work, "src/app.py")
    _commit(work, "docs/plans/old-plan.md")
    _git(work, "remote", "add", "origin", str(up))
    _git(work, "push", "-q", "origin", "main")
    _git(work, "remote", "set-head", "origin", "main")
    return work


def test_protected_branch_clean_is_intake(work):
    rc, out, _ = _run(work)
    assert rc == 0 and out["entry"] == "intake" and out["protected"] is True and out["base"] == "main"


def test_protected_branch_dirty_stops(work):
    _write(work, "src/app.py", "changed")
    rc, out, _ = _run(work)
    assert out["entry"] == "stop" and "feature branch" in out["reason"]


def test_develop_is_protected_even_when_not_the_base(work):
    _git(work, "switch", "-qc", "develop")
    _commit(work, "src/dev.py")
    assert _run(work)[1]["entry"] == "stop"


def test_pushed_clean_develop_ahead_of_the_base_is_intake(work):
    _git(work, "switch", "-qc", "develop")
    _commit(work, "src/dev.py")
    _git(work, "push", "-q", "origin", "develop")
    rc, out, _ = _run(work)
    assert out["ahead"] == 1 and out["unpushed"] == 0 and out["entry"] == "intake"


def test_feature_branch_without_work_is_intake(work):
    _git(work, "switch", "-qc", "feat/x")
    rc, out, _ = _run(work)
    assert out["entry"] == "intake" and out["dirty"] == 0 and out["ahead"] == 0


@pytest.mark.parametrize("committed", [False, True])
def test_feature_branch_with_code_is_review_loop(work, committed):
    _git(work, "switch", "-qc", "feat/x")
    (_commit if committed else _write)(work, "src/new.py")
    rc, out, _ = _run(work)
    assert out["entry"] == "review-loop"
    assert (out["ahead"], out["dirty"]) == ((1, 0) if committed else (0, 1))


@pytest.mark.parametrize("committed", [False, True])
def test_plan_only_is_plan_execute(work, committed):
    _git(work, "switch", "-qc", "feat/x")
    (_commit if committed else _write)(work, "docs/plans/2026-01-01-x.md")
    _write(work, "docs/specs/x-design.md")
    rc, out, _ = _run(work)
    assert out["entry"] == "plan-execute" and out["plans"] == ["docs/plans/2026-01-01-x.md"]


def test_plan_plus_code_is_review_loop_naming_the_plan(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "docs/plans/2026-01-01-x.md")
    _write(work, "src/new.py")
    rc, out, _ = _run(work)
    assert out["entry"] == "review-loop" and out["plans"] == ["docs/plans/2026-01-01-x.md"]


def test_editing_an_existing_plan_is_not_a_new_plan(work):
    _git(work, "switch", "-qc", "feat/x")
    _write(work, "docs/plans/old-plan.md", "edited")
    rc, out, _ = _run(work)
    assert out["plans"] == [] and out["entry"] == "review-loop"


def test_open_mr_pushed_and_clean_is_mr_followup(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "src/new.py")
    _git(work, "push", "-q", "origin", "feat/x")
    rc, out, _ = _run(work, "--mr-rc", "0")
    assert out["entry"] == "mr-followup" and out["mr"] == "open" and out["unpushed"] == 0


@pytest.mark.parametrize("extra", ["dirty", "unpushed"])
def test_open_mr_with_local_work_is_review_loop(work, extra):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "src/new.py")
    _git(work, "push", "-q", "origin", "feat/x")
    (_write if extra == "dirty" else _commit)(work, "src/more.py")
    rc, out, _ = _run(work, "--mr-rc", "0")
    assert out["entry"] == "review-loop" and out["mr"] == "open"


def test_merged_mr_stops(work):
    _git(work, "switch", "-qc", "feat/x")
    _write(work, "src/app.py", "changed")
    rc, out, _ = _run(work, "--mr-rc", "1")
    assert out["entry"] == "stop" and out["mr"] == "terminal"


def test_unverifiable_mr_counts_as_none_with_a_warning(work):
    _git(work, "switch", "-qc", "feat/x")
    _write(work, "src/new.py")
    rc, out, _ = _run(work, "--mr-rc", "2")
    assert out["entry"] == "review-loop" and out["mr"] == "unknown" and out["warnings"]


def test_negative_mr_rc_is_not_checked(work):
    _git(work, "switch", "-qc", "feat/x")
    _write(work, "src/new.py")
    rc, out, _ = _run(work, "--mr-rc", "-1")
    assert out["mr"] == "not-checked" and out["warnings"] == [] and out["entry"] == "review-loop"


def test_detached_head_ignores_the_mr(work):
    _git(work, "switch", "-q", "--detach")
    _write(work, "src/new.py")
    rc, out, _ = _run(work, "--mr-rc", "0")
    assert out["branch"] == "" and out["mr"] == "not-checked" and out["entry"] == "review-loop"


def test_rename_counts_once(work):
    _git(work, "switch", "-qc", "feat/x")
    _git(work, "mv", "src/app.py", "src/renamed.py")
    rc, out, _ = _run(work)
    assert out["dirty"] == 1 and out["entry"] == "review-loop"


def test_explicit_base(work):
    _git(work, "switch", "-qc", "parent")
    _commit(work, "src/parent.py")
    _git(work, "push", "-q", "origin", "parent")
    _git(work, "switch", "-qc", "child")
    assert _run(work, "--base", "parent")[1]["entry"] == "intake"
    assert _run(work)[1]["entry"] == "review-loop"


def test_unresolvable_base_is_rc_2(work):
    rc, out, err = _run(work, "--base", "nope", "--no-fetch")
    assert rc == 2 and "origin/nope" in err


def test_not_a_checkout_is_rc_2(tmp_path):
    rc, _, err = _run(tmp_path)
    assert rc == 2 and "not a git checkout" in err


def test_protected_branch_with_only_untracked_files_is_intake(work):
    _write(work, "notes.txt")
    _write(work, "docs/specs/draft.md")
    rc, out, _ = _run(work)
    assert out["entry"] == "intake" and out["dirty"] == 2


def test_merged_mr_on_a_clean_branch_is_intake(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "src/new.py")
    _git(work, "push", "-q", "origin", "feat/x")
    rc, out, _ = _run(work, "--mr-rc", "1")
    assert out["entry"] == "intake" and out["mr"] == "terminal"


def test_merged_mr_with_unpushed_commits_stops(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "src/new.py")
    _git(work, "push", "-q", "origin", "feat/x")
    _commit(work, "src/later.py")
    assert _run(work, "--mr-rc", "1")[1]["entry"] == "stop"


def test_no_mr_is_absent(work):
    _git(work, "switch", "-qc", "feat/x")
    _write(work, "src/new.py")
    rc, out, _ = _run(work, "--mr-rc", "3")
    assert out["mr"] == "absent" and out["warnings"] == [] and out["entry"] == "review-loop"


def test_failed_fetch_is_a_warning(work, tmp_path):
    _git(work, "switch", "-qc", "feat/x")
    _write(work, "src/new.py")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    rc, out, _ = _run(work)
    assert rc == 0 and out["entry"] == "review-loop" and any("fetch" in w for w in out["warnings"])


def test_worktree_side_rename_keeps_both_paths(work):
    _git(work, "switch", "-qc", "feat/x")
    (work / "src" / "app.py").rename(work / "src" / "moved.py")
    _git(work, "add", "-N", "src/moved.py")
    rc, out, _ = _run(work)
    assert out["dirty"] == 1 and out["entry"] == "review-loop"


def test_moving_code_into_a_plan_is_not_plan_only(work):
    _git(work, "switch", "-qc", "feat/x")
    _git(work, "mv", "src/app.py", "docs/plans/2026-01-01-x.md")
    assert _run(work)[1]["entry"] == "review-loop"
    _git(work, "commit", "-qm", "move")
    assert _run(work)[1]["entry"] == "review-loop"


def test_non_ascii_plan_name(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "docs/plans/2026-01-01-caffè.md")
    rc, out, _ = _run(work)
    assert out["entry"] == "plan-execute" and out["plans"] == ["docs/plans/2026-01-01-caffè.md"]


def test_plan_deleted_on_disk_is_not_offered(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "docs/plans/2026-01-01-x.md")
    (work / "docs/plans/2026-01-01-x.md").unlink()
    rc, out, _ = _run(work)
    assert out["plans"] == []


def test_several_plans_sort_by_name(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "docs/plans/2026-02-01-b.md")
    _write(work, "docs/plans/2026-01-01-a.md")
    assert _run(work)[1]["plans"] == ["docs/plans/2026-01-01-a.md", "docs/plans/2026-02-01-b.md"]


def test_merged_mr_with_remote_branch_gone_is_intake(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "src/new.py")
    _git(work, "push", "-q", "origin", "feat/x")
    _git(work, "push", "-q", "origin", "--delete", "feat/x")
    _git(work, "fetch", "-q", "--prune", "origin")
    _commit(work, "src/after-merge.py")
    rc, out, _ = _run(work, "--mr-rc", "1")
    assert out["unpushed"] is None and out["entry"] == "intake"


def test_merged_mr_with_only_untracked_files_is_intake(work):
    _git(work, "switch", "-qc", "feat/x")
    _commit(work, "src/new.py")
    _git(work, "push", "-q", "origin", "feat/x")
    _write(work, ".DS_Store")
    rc, out, _ = _run(work, "--mr-rc", "1")
    assert out["dirty"] == 1 and out["entry"] == "intake"


def test_status_does_not_rewrite_the_index(work):
    import os
    import time
    (work / "src/app.py").touch()
    index = work / ".git" / "index"
    before = index.stat().st_mtime_ns
    time.sleep(1.1)
    _run(work, "--no-fetch")
    assert index.stat().st_mtime_ns == before
    assert not (work / ".git" / "index.lock").exists() and os.path.exists(index)
