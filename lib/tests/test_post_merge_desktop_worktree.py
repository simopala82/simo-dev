"""post-merge.md "Worktree cleanup" must keep a desktop-app worktree (harness path + marker file
in its per-worktree git dir) and remove every other clean, pushed one."""
import subprocess
from pathlib import Path

import pytest

POST_MERGE = Path(__file__).parent.parent.parent / "skills" / "mr-followup" / "lib" / "post-merge.md"


def cleanup_block():
    text = POST_MERGE.read_text()
    section = text[text.index("## Worktree cleanup"):]
    start = section.index("```bash\n") + len("```bash\n")
    return section[start:section.index("```", start)]


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def main(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "--bare", "-b", "master", str(origin), cwd=tmp_path)
    main = tmp_path / "main"
    git("clone", str(origin), str(main), cwd=tmp_path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "init", cwd=main)
    git("push", "-q", "origin", "master", cwd=main)
    git("remote", "set-head", "origin", "-a", cwd=main)
    return main


def add_worktree(main, rel, branch, marker):
    path = main / rel
    git("worktree", "add", "-q", "-b", branch, str(path), "origin/master", cwd=main)
    git("push", "-q", "origin", branch, cwd=main)
    if marker:
        gitdir = subprocess.run(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-dir"],
                                check=True, capture_output=True, text=True).stdout.strip()
        Path(gitdir, "claude-desktop-worktree").touch()
    return path


def advance_origin(main, tmp_path):
    other = tmp_path / "other"
    git("clone", "-q", str(main / ".." / "origin.git"), str(other), cwd=tmp_path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "later", cwd=other)
    git("push", "-q", "origin", "master", cwd=other)
    return subprocess.run(["git", "-C", str(other), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def run_cleanup(main, path, branch):
    script = (cleanup_block().replace("<main-checkout-abs>", str(main))
              .replace("<path>", str(path)).replace("<name>", branch))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


@pytest.mark.parametrize("rel, marker, kept", [
    (".claude/worktrees/random-a1", True, True),
    (".claude/worktrees/random-b2", False, False),
    (".worktrees/task-c3", True, False),
])
def test_only_marked_harness_tree_is_kept(main, tmp_path, rel, marker, kept):
    branch = "feat/" + rel.rsplit("/", 1)[1]
    path = add_worktree(main, rel, branch, marker)
    new_master = advance_origin(main, tmp_path)
    result = run_cleanup(main, path, branch)
    assert head(main) == new_master, "the block must go on to refresh the main checkout"
    assert path.exists() is kept, result.stdout + result.stderr
    has_branch = subprocess.run(["git", "-C", str(main), "rev-parse", "--verify", "--quiet", branch],
                                capture_output=True).returncode == 0
    assert has_branch is kept
    assert ("desktop-app worktree" in result.stdout) is kept


def test_dirty_desktop_tree_reports_marker_skip_and_dirt(main):
    branch = "feat/random-d4"
    path = add_worktree(main, ".claude/worktrees/random-d4", branch, marker=True)
    (path / "untracked.txt").write_text("x")
    result = run_cleanup(main, path, branch)
    assert path.exists()
    assert "desktop-app worktree" in result.stdout and "Uncommitted:" in result.stdout, result.stdout


def test_main_checkout_resumed_in_place_is_kept(main):
    branch = "feat/resumed"
    git("switch", "-qc", branch, cwd=main)
    git("push", "-q", "origin", branch, cwd=main)
    result = run_cleanup(main, main, branch)
    assert main.exists() and "is the main checkout" in result.stdout, result.stdout + result.stderr
    assert subprocess.run(["git", "-C", str(main), "branch", "--show-current"],
                          capture_output=True, text=True).stdout.strip() == branch
    assert "skipped main refresh" in result.stdout
