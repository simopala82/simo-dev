"""lib/plan_gate_precheck.py: task-intake step 7.5's pre-gate eligibility check.

Cases ported from step 7.5's three conditions at 64fb8699 before the script existed: (1) the target branch
exists on origin (`git ls-remote --exit-code`, exit 2 = absent, any other exit = present), (2) step 3's
row is 3s/3a/3b/4, (3) row 1 with `submodule: yes`. Rows 1 and 2 without a submodule are the only ones
with no bypass.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "plan_gate_precheck.py"
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
BRANCH = "feat/proj-1-thing"


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=ENV).stdout


@pytest.fixture
def clone(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "master", str(origin))
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work))
    (work / "a").write_text("a\n")
    git("add", ".", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("push", "-q", "origin", "master", cwd=work)
    return work


def run(repo, row, submodule="no", branch=BRANCH):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--branch", branch, "--row", row,
                           "--submodule", submodule], capture_output=True, text=True, env=ENV)
    lines = dict(line.split(": ", 1) if ": " in line else line.split("=", 1) for line in proc.stdout.splitlines())
    return proc.returncode, lines


@pytest.mark.parametrize("row", ["1", "2"])
def test_new_branch_on_row_1_or_2_has_no_bypass(clone, row):
    rc, out = run(clone, row)
    assert rc == 0
    assert out["ls-remote rc"] == "2"
    assert out["bypass"] == "none"


def test_condition_1_branch_exists_on_origin(clone):
    git("push", "-q", "origin", f"master:refs/heads/{BRANCH}", cwd=clone)
    rc, out = run(clone, "1")
    assert out["ls-remote rc"] == "0"
    assert out["bypass"].startswith("follow-up")


@pytest.mark.parametrize("ref", [f"refs/heads/x/{BRANCH}", f"refs/tags/{BRANCH}"])
def test_condition_1_ignores_refs_that_only_share_the_tail(clone, ref):
    git("push", "-q", "origin", f"master:{ref}", cwd=clone)
    rc, out = run(clone, "1")
    assert out["ls-remote rc"] == "2"
    assert out["bypass"] == "none"


def test_condition_1_ls_remote_error_counts_as_present(tmp_path):
    repo = tmp_path / "noremote"
    git("init", "-q", str(repo))
    rc, out = run(repo, "1")
    assert out["ls-remote rc"] not in ("0", "2")
    assert out["bypass"].startswith("follow-up")


@pytest.mark.parametrize("row", ["3s", "3a", "3b", "4"])
def test_condition_2_linked_worktree_rows(clone, row):
    rc, out = run(clone, row)
    assert out["bypass"] == f"linked-worktree (row {row})"


def test_condition_3_submodule_on_row_1(clone):
    rc, out = run(clone, "1", submodule="yes")
    assert out["bypass"] == "submodule"


def test_every_firing_condition_is_named(clone):
    git("push", "-q", "origin", f"master:refs/heads/{BRANCH}", cwd=clone)
    rc, out = run(clone, "3a")
    assert out["bypass"].startswith("follow-up") and "linked-worktree (row 3a)" in out["bypass"]


def test_unknown_row_is_refused(clone):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(clone), "--branch", BRANCH, "--row", "5",
                           "--submodule", "no"], capture_output=True, text=True, env=ENV)
    assert proc.returncode == 2
