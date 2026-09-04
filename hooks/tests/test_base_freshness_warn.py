#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_base_freshness_warn.sh — same 5 cases."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "base_freshness_warn.py"


def _mkworktree(tmp_path, advance, declare_plan):
    """advance: "none"|"overlap"|"disjoint". Bare origin + a worktree-shaped clone
    (parent dir literally named .worktrees, since the hook's scoping check keys off
    that name)."""
    root = tmp_path / "root"
    up = root / "up.git"
    real = root / "real"
    wt = real / ".worktrees" / "feat-x"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(real)], check=True)
    subprocess.run(["git", "-C", str(real), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(real), "config", "user.name", "t"], check=True)
    (real / "shared.py").write_text("base\n")
    subprocess.run(["git", "-C", str(real), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(real), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(real), "remote", "add", "origin", str(up)], check=True)
    subprocess.run(["git", "-C", str(real), "push", "-q", "origin", "master"], check=True)
    subprocess.run(["git", "-C", str(real), "symbolic-ref", "refs/remotes/origin/HEAD",
                    "refs/remotes/origin/master"], check=True)
    (real / ".worktrees").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(real), "worktree", "add", "-q", "-b", "feat-x",
                    str(wt), "master"], check=True, capture_output=True)
    if declare_plan:
        (wt / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        (wt / "docs" / "plans" / "p.md").write_text("- Create: `shared.py`\n")
    if advance != "none":
        target = "shared.py" if advance == "overlap" else "other.py"
        (real / target).write_text("advanced\n")
        subprocess.run(["git", "-C", str(real), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(real), "commit", "-qm", "advance"], check=True)
        subprocess.run(["git", "-C", str(real), "push", "-q", "origin", "master"], check=True)
    return wt


def _warns(file_path, state_file):
    env = {**os.environ, "BASE_FRESHNESS_HOOK_STATE_FILE": str(state_file)}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_input": {"file_path": str(file_path)}}),
        text=True, env=env, capture_output=True,
    )
    return "additionalContext" in result.stdout


def test_overlap_declared_file_warns_once_then_armed_silent(tmp_path):
    wt = _mkworktree(tmp_path, "overlap", True)
    state = tmp_path / "state" / "base-freshness-hook"
    assert _warns(wt / "x.py", state)
    assert not _warns(wt / "y.py", state)   # second write, same branch -- armed


def test_disjoint_advance_never_warns(tmp_path):
    wt = _mkworktree(tmp_path, "disjoint", True)
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(wt / "x.py", state)


def test_no_declared_plan_files_never_warns(tmp_path):
    wt = _mkworktree(tmp_path, "overlap", False)
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(wt / "x.py", state)


def test_docs_plans_write_skipped_but_does_not_consume_the_arm(tmp_path):
    wt = _mkworktree(tmp_path, "overlap", True)
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(wt / "docs" / "plans" / "p.md", state)
    assert _warns(wt / "x.py", state)   # the real first write still warns


def test_main_checkout_write_never_warns(tmp_path):
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(main_repo)], check=True)
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(main_repo / "x.py", state)
