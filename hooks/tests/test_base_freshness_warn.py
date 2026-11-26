#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_base_freshness_warn.sh (its 5 cases), plus the desktop-app
tree layout (.claude/worktrees/<name>, recognised only with its marker file)."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "base_freshness_warn.py"


def _mark_flow(wt):
    gitdir = subprocess.run(["git", "-C", str(wt), "rev-parse", "--absolute-git-dir"],
                            check=True, capture_output=True, text=True).stdout.strip()
    (Path(gitdir) / "simo-dev-flow-active").write_text("1\n")


def _mkworktree(tmp_path, advance, declare_plan, layout="canonical", flow=True):
    """advance: "none"|"overlap"|"disjoint". layout: "canonical" (real/.worktrees/feat-x)
    | "desktop" (real/.claude/worktrees/brave-x plus the desktop-app marker in its git
    dir) | "desktop-unmarked" (same path, no marker). Bare origin + a worktree-shaped
    clone, since the hook's scoping check keys off the layout."""
    root = tmp_path / "root"
    up = root / "up.git"
    real = root / "real"
    wt = real / ".worktrees" / "feat-x" if layout == "canonical" else real / ".claude" / "worktrees" / "brave-x"
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
    wt.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(real), "worktree", "add", "-q", "-b", "feat-x",
                    str(wt), "master"], check=True, capture_output=True)
    if flow:
        _mark_flow(wt)
    if layout == "desktop":
        gitdir = subprocess.run(["git", "-C", str(wt), "rev-parse", "--absolute-git-dir"],
                                check=True, capture_output=True, text=True).stdout.strip()
        (Path(gitdir) / "claude-desktop-worktree").write_text("")
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
    env = {**os.environ, "BASE_FRESHNESS_HOOK_STATE_FILE": str(state_file),
           "SIMO_DEV_FLOW_ACTIVE_DIR": str(state_file.parent / "active")}
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


def test_marked_desktop_tree_warns(tmp_path):
    wt = _mkworktree(tmp_path, "overlap", True, layout="desktop")
    state = tmp_path / "state" / "base-freshness-hook"
    assert _warns(wt / "x.py", state)


def test_unmarked_claude_worktrees_tree_never_warns(tmp_path):
    wt = _mkworktree(tmp_path, "overlap", True, layout="desktop-unmarked")
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(wt / "x.py", state)


def test_main_checkout_write_never_warns(tmp_path):
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(main_repo)], check=True)
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(main_repo / "x.py", state)


def test_stacked_branch_checks_its_parent_not_the_default(tmp_path):
    """feat-x is stacked on a pushed parent; master advanced on the declared file after the
    parent forked. Against the parent nothing advanced, so no warning."""
    wt = _mkworktree(tmp_path, "none", True)
    real = wt.parent.parent
    (wt / "parent.py").write_text("p\n")
    subprocess.run(["git", "-C", str(wt), "add", "parent.py"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-qm", "parent"], check=True)
    subprocess.run(["git", "-C", str(wt), "push", "-q", "origin", "HEAD:parent"], check=True)
    (wt / "own.py").write_text("own\n")   # a commit of feat-x's own: a real stack, not a snapshot
    subprocess.run(["git", "-C", str(wt), "add", "own.py"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-qm", "own"], check=True)
    (real / "shared.py").write_text("advanced\n")
    subprocess.run(["git", "-C", str(real), "commit", "-qam", "advance"], check=True)
    subprocess.run(["git", "-C", str(real), "push", "-q", "origin", "master"], check=True)
    subprocess.run(["git", "-C", str(wt), "fetch", "-q", "origin"], check=True)
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(wt / "x.py", state)


def test_overlap_outside_the_flow_never_warns_and_keeps_the_arm(tmp_path):
    wt = _mkworktree(tmp_path, "overlap", True, flow=False)
    state = tmp_path / "state" / "base-freshness-hook"
    assert not _warns(wt / "x.py", state)
    _mark_flow(wt)
    assert _warns(wt / "y.py", state)   # the inactive write did not consume the arm


def test_session_flag_alone_activates_the_hook(tmp_path):
    wt = _mkworktree(tmp_path, "overlap", True, flow=False)
    state = tmp_path / "state" / "base-freshness-hook"
    (state.parent / "active").mkdir(parents=True)
    (state.parent / "active" / "sess-y").write_text("1\n")
    env = {**os.environ, "BASE_FRESHNESS_HOOK_STATE_FILE": str(state),
           "SIMO_DEV_FLOW_ACTIVE_DIR": str(state.parent / "active")}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"session_id": "sess-y", "tool_input": {"file_path": str(wt / "x.py")}}),
        text=True, env=env, capture_output=True,
    )
    assert "additionalContext" in result.stdout
