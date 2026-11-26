"""lib/worktree_resolve.py: task-intake's step-3 pre-flight verdicts and step-6 worktree table.

One test per row and per stop case, ported from the step-3/step-6 prose of task-intake SKILL.md at
112f7cc7 before the script existed. Every test builds the real git shape in a temp dir and runs the
script as the skill does; tests that check printed `run:` lines also execute them, under bash and zsh.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "worktree_resolve.py"
SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
BRANCH, NAME = "feat/proj-1-new-task", "proj-1-new-task"
MULTI = ("run", "foreign")


def git(*args, cwd=None):
    return subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, env=ENV).stdout.strip()


def run(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=ENV)
    out = {k: [] for k in MULTI}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(": ")
        if key in MULTI:
            out[key].append(value)
        else:
            assert key not in out, f"duplicate key {key}: {proc.stdout}"
            out[key] = value
    return proc.returncode, out, proc


def resolve(path, *extra, branch=True):
    args = ["resolve", "--dir", path, *extra]
    if branch:
        args += ["--branch", BRANCH, "--base", "master"]
    return run(*args)


def execute(lines, shell="bash"):
    for line in lines:
        proc = subprocess.run([shell, "-c", line], capture_output=True, text=True, env=ENV)
        assert proc.returncode == 0, (line, proc.stderr)


@pytest.fixture
def main(tmp_path):
    tmp = tmp_path.resolve()
    origin = tmp / "origin.git"
    git("init", "-q", "--bare", "-b", "master", str(origin))
    seed = tmp / "seed"
    git("clone", "-q", str(origin), str(seed))
    (seed / "a.txt").write_text("a\n")
    git("add", "a.txt", cwd=seed)
    git("commit", "-qm", "init", cwd=seed)
    git("push", "-q", "origin", "master", cwd=seed)
    repo = tmp / "main"
    git("clone", "-q", str(origin), str(repo))
    (repo / ".gitignore").write_text(".worktrees/\n")
    return repo


def harness(main, name="rnd", marker=True, detach=True, branch=None):
    top = main / ".claude" / "worktrees" / name
    args = ["worktree", "add", "-q", str(top)]
    args += ["-b", branch, "origin/master"] if branch else (["--detach", "origin/master"] if detach else [])
    git(*args, cwd=main)
    if marker:
        gitdir = Path(git("rev-parse", "--path-format=absolute", "--git-dir", cwd=top))
        (gitdir / "claude-desktop-worktree").touch()
    return top


# --- step 3: pre-flight stops ------------------------------------------------------------------

def test_unborn_head_stops_before_any_row(tmp_path):
    repo = tmp_path.resolve() / "unborn"
    git("init", "-q", str(repo))
    rc, out, proc = resolve(repo, branch=False)
    assert rc == 1 and "STOP" in out and "row" not in out, proc.stdout


def test_not_a_repo_stops(tmp_path):
    plain = tmp_path.resolve() / "plain"
    plain.mkdir()
    rc, out, _ = resolve(plain, branch=False)
    assert rc == 1 and "STOP" in out and "row" not in out


def test_missing_dir_stops(tmp_path):
    rc, out, _ = resolve(tmp_path.resolve() / "gone", branch=False)
    assert rc == 1 and "STOP" in out


@pytest.mark.parametrize("kind", ["cache", "marketplaces"])
def test_plugin_cache_or_marketplace_copy_with_its_own_git_stops(tmp_path, kind):
    copy = tmp_path.resolve() / ".claude" / "plugins" / kind / "mkt" / "plug"
    git("init", "-q", str(copy))
    git("commit", "-q", "--allow-empty", "-m", "c", cwd=copy)
    rc, out, _ = resolve(copy, branch=False)
    assert rc == 1 and "plugin cache/marketplace copy" in out["STOP"]
    assert out["real"] == str(copy)


def test_gitless_rsync_copy_inside_an_outer_repo_stops(main):
    copy = main / ".claude" / "plugins" / "cache" / "mkt" / "plug" / "1.0"
    copy.mkdir(parents=True)
    rc, out, _ = resolve(copy, branch=False)
    assert rc == 1 and "rsync" in out["STOP"]
    assert out["top"] == str(main), "the toplevel alone carries no plugins/ segment"


# --- step 6 rows: 1 ----------------------------------------------------------------------------

def test_row1_main_checkout_classify_mode(main):
    rc, out, _ = resolve(main, branch=False)
    assert (rc, out["row"], out["submodule"]) == (0, "1", "no")
    assert out["top"] == str(main) and out["real"] == str(main)
    assert out["run"] == [] and "wt" not in out


@pytest.mark.parametrize("shell", SHELLS)
def test_row1_adds_the_worktree_off_origin_base_with_no_upstream(main, shell):
    rc, out, proc = resolve(main)
    assert (rc, out["row"]) == (0, "1"), proc.stdout
    wt = main / ".worktrees" / NAME
    assert out["wt"] == str(wt) and out["repo"] == str(main)
    assert not any("worktree move" in r or "branch -m" in r for r in out["run"])
    add = [r for r in out["run"] if "worktree add" in r]
    assert len(add) == 1 and "--no-track" in add[0] and "origin/master" in add[0]
    assert out["run"].index(next(r for r in out["run"] if "fetch" in r)) < out["run"].index(add[0])
    execute(out["run"], shell)
    assert git("rev-parse", "--show-toplevel", cwd=wt) == str(wt)
    assert git("branch", "--show-current", cwd=wt) == BRANCH
    assert git("rev-parse", "HEAD", cwd=wt) == git("rev-parse", "origin/master", cwd=main)
    upstream = subprocess.run(["git", "-C", str(wt), "config", f"branch.{BRANCH}.merge"], capture_output=True, env=ENV)
    assert upstream.returncode == 1, "--no-track: no upstream before the first push"


def test_row1_detached_main_checkout_is_still_row1(main):
    git("checkout", "-q", "--detach", cwd=main)
    rc, out, _ = resolve(main)
    assert (rc, out["row"], out["headref"]) == (0, "1", "HEAD")


def test_row1_submodule_working_tree(main, tmp_path):
    sup = tmp_path.resolve() / "super"
    git("init", "-q", "-b", "master", str(sup))
    git("commit", "-q", "--allow-empty", "-m", "s", cwd=sup)
    git("submodule", "add", "-q", str(tmp_path.resolve() / "origin.git"), "sub", cwd=sup)
    rc, out, _ = resolve(sup / "sub", branch=False)
    assert (rc, out["row"], out["submodule"]) == (0, "1", "yes")
    assert out["real_ok"] == "n/a", "row 1 runs before the <REAL> assert a submodule would fail"


def test_row1_worktrees_dir_not_ignored_gets_a_local_exclude(main):
    (main / ".gitignore").unlink()
    rc, out, _ = resolve(main)
    assert rc == 0 and any("info/exclude" in r for r in out["run"])
    execute(out["run"])
    assert git("check-ignore", "-q", ".worktrees/probe", cwd=main) == ""
    assert git("status", "--porcelain", cwd=main) == "", "the exclude is local, nothing to commit"


def test_row1_ignored_worktrees_dir_adds_no_exclude(main):
    rc, out, _ = resolve(main)
    assert rc == 0 and not any("info/exclude" in r for r in out["run"])


def test_row1_destination_taken_stops(main):
    (main / ".worktrees" / NAME).mkdir(parents=True)
    rc, out, _ = resolve(main)
    assert rc == 1 and "STOP" in out and out["run"] == []


def test_row1_local_branch_already_exists_stops(main):
    git("branch", BRANCH, cwd=main)
    rc, out, _ = resolve(main)
    assert rc == 1 and BRANCH in out["STOP"] and out["run"] == []


def test_row1_existing_remote_branch_is_the_follow_up_path(main):
    git("push", "-q", "origin", f"master:{BRANCH}", cwd=main)
    rc, out, _ = resolve(main)
    assert (rc, out["next"]) == (0, "follow-up") and out["run"] == []


# --- row 2 -------------------------------------------------------------------------------------

def test_row2_canonical_tree_gets_a_sibling_under_real(main):
    other = main / ".worktrees" / "other"
    git("worktree", "add", "-q", str(other), "-b", "feat/other", "origin/master", cwd=main)
    rc, out, _ = resolve(other)
    assert (rc, out["row"], out["repo"]) == (0, "2", str(main))
    assert out["wt"] == str(main / ".worktrees" / NAME)
    execute(out["run"])
    assert git("branch", "--show-current", cwd=main / ".worktrees" / NAME) == BRANCH


def test_nested_harness_tree_inside_a_canonical_tree_is_not_canonical(main):
    other = main / ".worktrees" / "other"
    git("worktree", "add", "-q", str(other), "-b", "feat/other", "origin/master", cwd=main)
    nested = harness(other, marker=False)
    rc, out, _ = resolve(nested)
    assert (out["canonical"], out["harness"], out["row"]) == ("no", "yes", "3b")
    assert out["real"] == str(main), "nested worktrees share the main .git"


# --- rows 3s / 3a: desktop-app trees and the shared-tree check ---------------------------------

def test_desktop_tree_without_shared_flags_needs_the_check(main):
    top = harness(main)
    rc, out, _ = resolve(top)
    assert (rc, out["NEED"]) == (3, "shared-check") and "row" not in out and out["run"] == []
    rc, out, _ = resolve(top, branch=False)
    assert (rc, out["NEED"]) == (3, "shared-check")


def test_row3a_owned_desktop_tree_adopts_in_place(main):
    top = harness(main)
    rc, out, _ = resolve(top, "--self-worktree", top)
    assert (rc, out["row"], out["wt"], out["next"]) == (0, "3a", str(top), "adopt")
    assert out["run"] == [], "row 3a never moves the tree"


@pytest.mark.parametrize("flags", [
    lambda top, main: ["--self-worktree", ""],
    lambda top, main: ["--self-worktree", main],
    lambda top, main: ["--no-session-tools"],
])
def test_row3s_shared_tree_stops_with_no_command(main, flags):
    top = harness(main)
    rc, out, _ = resolve(top, *flags(top, main))
    assert (rc, out["row"]) == (1, "3s") and "STOP" in out
    assert out["run"] == [] and "next" not in out


def test_row3s_a_linked_session_in_the_same_dir_through_a_symlink(main, tmp_path):
    top = harness(main)
    link = tmp_path.resolve() / "link"
    link.symlink_to(top)
    rc, out, _ = resolve(top, "--self-worktree", top, "--linked-cwd", main, "--linked-cwd", link)
    assert (rc, out["row"]) == (1, "3s"), "-ef, not string equality"


def test_missing_linked_cwd_does_not_make_the_tree_shared(main, tmp_path):
    top = harness(main)
    rc, out, _ = resolve(top, "--self-worktree", top, "--linked-cwd", tmp_path / "gone")
    assert (rc, out["row"]) == (0, "3a")


def test_adopt_anyway_records_the_tree_as_not_shared(main):
    top = harness(main)
    rc, out, _ = resolve(top, "--no-session-tools", "--adopt-anyway")
    assert (rc, out["row"]) == (0, "3a")


def test_row3s_stops_in_classify_mode_too(main):
    top = harness(main)
    rc, out, _ = resolve(top, "--no-session-tools", branch=False)
    assert (rc, out["row"]) == (1, "3s")


# --- row 3b ------------------------------------------------------------------------------------

@pytest.mark.parametrize("shell", SHELLS)
def test_row3b_moves_the_tree_under_real_worktrees(main, shell):
    top = harness(main, marker=False)
    rc, out, _ = resolve(top)
    dest = main / ".worktrees" / NAME
    assert (rc, out["row"], out["wt"], out["next"]) == (0, "3b", str(dest), "adopt")
    assert any("worktree move" in r for r in out["run"])
    execute(out["run"], shell)
    assert not top.exists() and git("rev-parse", "--show-toplevel", cwd=dest) == str(dest)


def test_row3b_worktrees_dir_not_ignored_gets_a_local_exclude(main):
    (main / ".gitignore").unlink()
    top = harness(main, marker=False)
    rc, out, _ = resolve(top)
    assert rc == 0 and any("info/exclude" in r for r in out["run"])
    execute(out["run"])
    assert git("check-ignore", "-q", ".worktrees/probe", cwd=main) == ""


def test_row3b_destination_taken_stops(main):
    top = harness(main, marker=False)
    (main / ".worktrees" / NAME).mkdir(parents=True)
    rc, out, _ = resolve(top)
    assert (rc, out["row"]) == (1, "3b") and "STOP" in out and out["run"] == []


def test_row3b_is_never_shared_checked(main):
    top = harness(main, marker=False)
    rc, out, _ = resolve(top)
    assert "NEED" not in out and out["row"] == "3b"


# --- row 4 and the <REAL> assert ---------------------------------------------------------------

def test_row4_deliberate_linked_worktree_stops(main, tmp_path):
    release = tmp_path.resolve() / "myservice-v1"
    git("worktree", "add", "-q", "--detach", str(release), "origin/master", cwd=main)
    rc, out, _ = resolve(release)
    assert (rc, out["row"]) == (1, "4") and out["run"] == []
    rc, out, _ = resolve(release, branch=False)
    assert (rc, out["row"]) == (0, "4"), "classify mode leaves row 4 to step 6"


def test_desktop_marker_off_a_harness_path_is_row4(main, tmp_path):
    tree = tmp_path.resolve() / "hand-made"
    git("worktree", "add", "-q", "--detach", str(tree), "origin/master", cwd=main)
    gitdir = Path(git("rev-parse", "--path-format=absolute", "--git-dir", cwd=tree))
    (gitdir / "claude-desktop-worktree").touch()
    rc, out, _ = resolve(tree)
    assert (rc, out["row"]) == (1, "4")


def bare_worktree(tmp_path, parent):
    parent.mkdir(parents=True, exist_ok=True)
    bare = parent / "proj.git"
    git("clone", "-q", "--bare", str(tmp_path.resolve() / "origin.git"), str(bare))
    tree = parent / "x" / ".claude" / "worktrees" / "rnd"
    git("worktree", "add", "-q", "--detach", str(tree), "master", cwd=bare)
    return tree


def test_bare_repo_worktree_fails_the_real_assert(main, tmp_path):
    tree = bare_worktree(tmp_path, tmp_path.resolve() / "bare")
    rc, out, _ = resolve(tree)
    assert rc == 1 and out["real_ok"] == "no" and "REAL" in out["STOP"] and out["run"] == []
    rc, out, _ = resolve(tree, branch=False)
    assert (rc, out["real_ok"]) == (0, "no"), "classify mode reports it, step 6 stops on it"


def test_real_inside_somebody_elses_working_tree_fails_the_real_assert(main, tmp_path):
    tree = bare_worktree(tmp_path, main / "sub")
    rc, out, _ = resolve(tree)
    assert rc == 1 and out["real_ok"] == "no"


# --- adopt (rows 3a and 3b) --------------------------------------------------------------------

def adopt(wt, *extra):
    return run("adopt", "--wt", wt, "--branch", BRANCH, "--base", "master", *extra)


@pytest.mark.parametrize("shell", SHELLS)
def test_adopt_detached_renames_with_switch_c_only(main, shell):
    top = harness(main)
    rc, out, _ = adopt(top)
    assert (rc, out["shape"], out["next"]) == (0, "detached", "park")
    assert len(out["run"]) == 1 and "switch -c" in out["run"][0]
    execute(out["run"], shell)
    assert git("branch", "--show-current", cwd=top) == BRANCH


@pytest.mark.parametrize("shell", SHELLS)
def test_adopt_unpushed_claude_branch_renames_with_branch_m_and_drops_upstream(main, shell):
    top = harness(main, branch="claude/dazzling-x")
    git("branch", "--set-upstream-to=origin/master", cwd=top)
    rc, out, _ = adopt(top)
    assert (rc, out["shape"], out["old_remote"]) == (0, "claude", "no")
    assert not any("switch -c" in r for r in out["run"]) and any("branch -m" in r for r in out["run"])
    execute(out["run"], shell)
    assert git("branch", "--show-current", cwd=top) == BRANCH
    upstream = subprocess.run(["git", "-C", str(top), "config", f"branch.{BRANCH}.merge"], capture_output=True, env=ENV)
    assert upstream.returncode == 1


def test_adopt_claude_branch_without_upstream_runs_clean(main):
    top = harness(main, branch="claude/dazzling-y")
    rc, out, _ = adopt(top)
    assert rc == 0
    execute(out["run"])
    assert git("branch", "--show-current", cwd=top) == BRANCH


def test_adopt_pushed_claude_branch_asks_before_renaming(main):
    top = harness(main, branch="claude/pushed-z")
    git("push", "-q", "origin", "claude/pushed-z", cwd=top)
    rc, out, _ = adopt(top)
    assert (rc, out["ASK"], out["old_remote"]) == (4, "old-remote", "yes") and out["run"] == []
    rc, out, _ = adopt(top, "--old-remote-ok")
    assert rc == 0 and any("branch -m" in r for r in out["run"])


def test_adopt_foreign_commits_ask_before_renaming(main):
    top = harness(main)
    (top / "old.txt").write_text("old\n")
    git("add", "old.txt", cwd=top)
    git("commit", "-qm", "earlier task", cwd=top)
    rc, out, _ = adopt(top)
    assert (rc, out["ASK"]) == (4, "foreign-commits") and out["run"] == []
    assert len(out["foreign"]) == 1 and "earlier task" in out["foreign"][0]
    rc, out, _ = adopt(top, "--foreign-ok")
    assert rc == 0 and len(out["run"]) == 1


def test_adopt_named_branch_defers_to_the_merged_branch_path_without_asking(main):
    top = harness(main, branch="feat/old-task")
    (top / "old.txt").write_text("old\n")
    git("add", "old.txt", cwd=top)
    git("commit", "-qm", "squash-merged elsewhere", cwd=top)
    rc, out, _ = adopt(top)
    assert (rc, out["shape"], out["next"]) == (0, "named", "merged-branch-path")
    assert "ASK" not in out and out["run"] == [] and len(out["foreign"]) == 1


def test_adopt_retry_after_this_tasks_rename_stops(main):
    top = harness(main, branch=BRANCH)
    rc, out, _ = adopt(top)
    assert rc == 1 and "already" in out["STOP"] and out["run"] == []


def test_adopt_target_branch_exists_locally_stops(main):
    top = harness(main)
    git("branch", BRANCH, "origin/master", cwd=main)
    rc, out, _ = adopt(top)
    assert rc == 1 and BRANCH in out["STOP"]


def test_adopt_unfetchable_base_stops(main):
    top = harness(main)
    rc, out, _ = run("adopt", "--wt", top, "--branch", BRANCH, "--base", "no-such-base")
    assert rc == 1 and "STOP" in out and out["run"] == []


def test_adopt_missing_dir_stops(main, tmp_path):
    rc, out, _ = adopt(tmp_path.resolve() / "gone")
    assert rc == 1 and "STOP" in out


def test_adopt_refuses_a_main_checkout(main):
    rc, out, _ = adopt(main)
    assert rc == 1 and "STOP" in out


def test_adopt_refuses_a_row4_tree(main, tmp_path):
    release = tmp_path.resolve() / "myservice-v1"
    git("worktree", "add", "-q", "--detach", str(release), "origin/master", cwd=main)
    rc, out, _ = adopt(release)
    assert rc == 1 and "STOP" in out


def test_adopt_after_a_row3b_move(main):
    top = harness(main, marker=False)
    _, out, _ = resolve(top)
    execute(out["run"])
    rc, out, _ = adopt(out["wt"])
    assert (rc, out["shape"]) == (0, "detached")


@pytest.mark.parametrize("shell", SHELLS)
def test_adopt_unpushed_claude_branch_with_a_dangling_upstream_drops_it(main, shell):
    top = harness(main, branch="claude/x")
    git("config", "branch.claude/x.remote", "origin", cwd=top)
    git("config", "branch.claude/x.merge", "refs/heads/claude/x", cwd=top)
    rc, out, _ = adopt(top)
    assert (rc, out["old_remote"]) == (0, "no")
    execute(out["run"], shell)
    assert git("branch", "--show-current", cwd=top) == BRANCH
    merge = subprocess.run(["git", "-C", str(top), "config", f"branch.{BRANCH}.merge"], capture_output=True, env=ENV)
    assert merge.returncode == 1


def test_adopt_refuses_a_canonical_tree_with_another_name(main):
    other = main / ".worktrees" / "other-task"
    git("worktree", "add", "-q", "--detach", str(other), "origin/master", cwd=main)
    rc, out, _ = adopt(other)
    assert rc == 1 and "not the row-3b destination" in out["STOP"]


@pytest.mark.parametrize("shell", SHELLS)
def test_exclude_append_survives_a_missing_trailing_newline(main, shell):
    (main / ".gitignore").unlink()
    exclude = Path(git("rev-parse", "--path-format=absolute", "--git-path", "info/exclude", cwd=main))
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("# keep\n*.tmp")
    rc, out, _ = resolve(main)
    execute(out["run"], shell)
    assert exclude.read_text().splitlines() == ["# keep", "*.tmp", ".worktrees/"]
    assert git("check-ignore", "-q", ".worktrees/probe", cwd=main) == ""
