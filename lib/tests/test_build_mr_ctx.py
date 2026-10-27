"""Tests for lib/build_mr_ctx.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "build_mr_ctx.py"
FAKE_SCOPE_CMD = Path(__file__).parent / "fixtures" / "fake_scope_cmd.py"
TRACKER_CONFIG = Path(__file__).parent.parent / "tracker_config.py"


def _cache_safe_path(tmp_path, src):
    # copies src into tmp_path so its path never trips tracker_config.py's cache-path guard
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    return dst


def _init_repo(path, branch):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / ".claude").mkdir()
    (path / ".claude" / "no-tracker").touch()
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    return path


def run_build(cwd, args):
    result = subprocess.run(
        [sys.executable, str(LIB), *args], capture_output=True, text=True, cwd=str(cwd),
    )
    return result.returncode, result.stdout, result.stderr


def test_repo_flag_uses_repo_path_not_cwd(tmp_path):
    repo_a = _init_repo(tmp_path / "repo-a", "other-branch")
    repo_b = _init_repo(tmp_path / "repo-b", "feat/target-branch")

    rc, out, err = run_build(repo_a, ["--repo", str(repo_b)])
    assert rc == 0, err
    ctx = json.loads(out)
    head_b = subprocess.check_output(["git", "-C", str(repo_b), "rev-parse", "HEAD"], text=True).strip()
    assert ctx["branch_meta"]["source"] == "feat/target-branch"
    assert ctx["branch_meta"]["head_sha"] == head_b
    assert ctx["branch_meta"]["jira_key"] == ""  # .claude/no-tracker marker in repo_b, not repo_a


def test_without_repo_flag_falls_back_to_cwd():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _init_repo(Path(tmp) / "repo", "feat/cwd-branch")
        rc, out, err = run_build(repo, [])
        assert rc == 0, err
        ctx = json.loads(out)
        assert ctx["branch_meta"]["source"] == "feat/cwd-branch"


def _init_repo_with_key(path, branch):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    return path


def test_ticket_summary_fetched_via_jira_scope_light_mode(tmp_path):
    repo = _init_repo_with_key(tmp_path / "repo", "feat/proj-1234-thing")
    tracker_cfg = tmp_path / "tracker.json"
    subprocess.run(
        [sys.executable, str(TRACKER_CONFIG), "set", "PROJ", "scope_fetch_cmd",
         f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}", "--config", str(tracker_cfg)],
        check=True,
    )
    env = dict(os.environ)
    env["SIMO_DEV_TRACKER_CONFIG"] = str(tracker_cfg)
    result = subprocess.run(
        [sys.executable, str(LIB), "--repo", str(repo)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)
    assert ctx["branch_meta"]["jira_key"] == "PROJ-1234"
    assert "PROJ-1234: Canned summary" in ctx["ticket_summary"]
    assert "comments_shown=0" in ctx["ticket_summary"]  # light mode -> comments forced to 0


def _init_repo_with_base_and_feature(path):
    """Bare 'origin' + a work checkout: one base commit on main (pushed, HEAD set),
    then a feature branch adding one file -- so build_mr_ctx.py can resolve a real
    base/target and diff against it (existing helpers in this file have no remote,
    so target/base always resolve empty and neither commit_log/file_stat/changed_files
    is exercised against a real diff anywhere in this suite)."""
    origin = path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    (work / ".claude").mkdir()
    (work / ".claude" / "no-tracker").touch()
    (work / "base.txt").write_text("base")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(work), "symbolic-ref",
                    "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], check=True)
    subprocess.run(["git", "-C", str(work), "switch", "-q", "-c", "feat/thing"], check=True)
    (work / "new_file.py").write_text("print('x')\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "add new_file"], check=True)
    return work


def test_changed_files_lists_full_untruncated_paths(tmp_path):
    repo = _init_repo_with_base_and_feature(tmp_path)
    rc, out, err = run_build(repo, [])
    assert rc == 0, err
    ctx = json.loads(out)
    assert ctx["changed_files"].strip() == "new_file.py"


def test_changed_files_preserves_paths_file_stat_truncates(tmp_path):
    """This is the feature's actual reason to exist (see the plan's Architecture note):
    `git diff --stat` abbreviates a long/deeply-nested path with '...', `--name-only` does not."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    (work / ".claude").mkdir()
    (work / ".claude" / "no-tracker").touch()
    (work / "base.txt").write_text("base")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(work), "symbolic-ref",
                    "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], check=True)
    subprocess.run(["git", "-C", str(work), "switch", "-q", "-c", "feat/thing"], check=True)
    deep_dir = work / "a/very/deeply/nested/directory/structure/that/is/quite/long/to/trigger/truncation/in/diff/stat/output"
    deep_dir.mkdir(parents=True)
    long_path = "a/very/deeply/nested/directory/structure/that/is/quite/long/to/trigger/truncation/in/diff/stat/output/module.py"
    (deep_dir / "module.py").write_text("print('x')\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "add deep file"], check=True)
    rc, out, err = run_build(work, [])
    assert rc == 0, err
    ctx = json.loads(out)
    assert long_path in ctx["changed_files"]
    assert long_path not in ctx["file_stat"]  # confirms file_stat really does truncate this path
    assert "..." in ctx["file_stat"]


def _git(work, *args):
    subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)


def _commit(work, name):
    (work / name).write_text(name)
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", f"add {name}")


def _stacked_repo(tmp_path):
    """main <- feat/parent (pushed, 1 commit) <- feat/child (pushed, 1 commit, checked out)."""
    work = _init_repo_with_base_and_feature(tmp_path)  # on feat/thing with new_file.py, unpushed
    _git(work, "branch", "-m", "feat/parent")
    _git(work, "push", "-q", "origin", "feat/parent")
    _git(work, "switch", "-q", "-c", "feat/child")
    _commit(work, "child.py")
    _git(work, "push", "-q", "origin", "feat/child")
    return work


def _meta(repo, args=()):
    rc, out, err = run_build(repo, list(args))
    assert rc == 0, err
    return json.loads(out)


def test_default_target_source_when_not_stacked(tmp_path):
    meta = _meta(_init_repo_with_base_and_feature(tmp_path))["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("main", "default")


def test_stacked_branch_targets_parent(tmp_path):
    ctx = _meta(_stacked_repo(tmp_path))
    assert (ctx["branch_meta"]["target"], ctx["branch_meta"]["target_source"]) == ("feat/parent", "stacked")
    assert ctx["changed_files"].strip() == "child.py"
    assert "new_file.py" not in ctx["file_stat"]
    assert len(ctx["commit_log"].strip().splitlines()) == 1


def test_unrelated_remote_branch_is_ignored(tmp_path):
    repo = _init_repo_with_base_and_feature(tmp_path)
    _git(repo, "switch", "-q", "-c", "feat/other", "main")
    _commit(repo, "other.py")
    _git(repo, "push", "-q", "origin", "feat/other")
    _git(repo, "switch", "-q", "feat/thing")
    rc, out, err = run_build(repo, [])
    assert rc == 0, err
    meta = json.loads(out)["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("main", "default")
    assert "feat/other" not in err  # filtered out by --contains, not merely reported as diverged


def test_nearest_parent_wins_in_three_level_stack(tmp_path):
    repo = _stacked_repo(tmp_path)  # main <- feat/parent <- feat/child
    _git(repo, "switch", "-q", "-c", "feat/grandchild")
    _commit(repo, "grandchild.py")
    meta = _meta(repo)["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("feat/child", "stacked")


def test_own_remote_branch_is_not_a_candidate(tmp_path):
    repo = _stacked_repo(tmp_path)  # feat/child pushed
    _commit(repo, "child2.py")  # HEAD now ahead of origin/feat/child
    assert _meta(repo)["branch_meta"]["target"] == "feat/parent"


def test_branch_stacked_on_top_of_head_is_not_a_candidate(tmp_path):
    """No ancestor candidate here, so a descendant that slipped into `diverged` would surface in the note."""
    repo = _init_repo_with_base_and_feature(tmp_path)  # feat/thing, one commit on main
    _git(repo, "push", "-q", "origin", "feat/thing")
    _git(repo, "switch", "-q", "-c", "feat/on-top")
    _commit(repo, "on_top.py")
    _git(repo, "push", "-q", "origin", "feat/on-top")
    _git(repo, "switch", "-q", "feat/thing")
    rc, out, err = run_build(repo, [])
    assert rc == 0, err
    meta = json.loads(out)["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("main", "default")
    assert "feat/on-top" not in err  # skipped, not reported as a diverged candidate


def test_no_own_commit_yet_still_targets_parent(tmp_path):
    """Step 0.5 runs before the first commit: HEAD still equals origin/<parent>."""
    repo = _init_repo_with_base_and_feature(tmp_path)
    _git(repo, "branch", "-m", "feat/parent")
    _git(repo, "push", "-q", "origin", "feat/parent")
    _git(repo, "switch", "-q", "-c", "feat/child")
    (repo / "pending.py").write_text("x")  # uncommitted
    rc, out, err = run_build(repo, ["--print-target"])
    assert rc == 0, err
    assert out == "feat/parent\n"


def _advanced_parent_repo(tmp_path):
    """_stacked_repo, then feat/parent gets one more pushed commit after feat/child forked."""
    repo = _stacked_repo(tmp_path)
    _git(repo, "switch", "-q", "feat/parent")
    _commit(repo, "review_fix.py")
    _git(repo, "push", "-q", "origin", "feat/parent")
    return repo


def test_advanced_parent_falls_back_to_default_with_note(tmp_path):
    repo = _advanced_parent_repo(tmp_path)
    _git(repo, "switch", "-q", "feat/child")
    rc, out, err = run_build(repo, [])
    assert rc == 0, err
    meta = json.loads(out)["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("main", "default")
    assert "note:" in err and "feat/parent" in err and "--target" in err


def test_parent_never_targets_its_own_child(tmp_path):
    repo = _advanced_parent_repo(tmp_path)  # checked out on feat/parent
    rc, out, err = run_build(repo, [])
    assert rc == 0, err
    meta = json.loads(out)["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("main", "default")
    assert "feat/child" in err


def test_ancestor_parent_beats_sibling_at_same_distance(tmp_path):
    repo = _stacked_repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "feat/sibling", "feat/parent")
    _commit(repo, "sibling.py")
    _git(repo, "push", "-q", "origin", "feat/sibling")
    _git(repo, "switch", "-q", "feat/child")
    assert _meta(repo)["branch_meta"]["target"] == "feat/parent"


def test_explicit_target_wins_over_detection(tmp_path):
    ctx = _meta(_stacked_repo(tmp_path), ["--target", "main"])
    assert (ctx["branch_meta"]["target"], ctx["branch_meta"]["target_source"]) == ("main", "explicit")
    assert sorted(ctx["changed_files"].split()) == ["child.py", "new_file.py"]


def test_empty_target_means_not_given(tmp_path):
    meta = _meta(_stacked_repo(tmp_path), ["--target", ""])["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("feat/parent", "stacked")


def test_explicit_target_missing_on_origin_stops(tmp_path):
    rc, out, err = run_build(_stacked_repo(tmp_path), ["--target", "no-such-branch"])
    assert rc == 2
    assert out == ""
    assert "STOP:" in err and "origin/no-such-branch" in err


def test_merged_parent_is_not_a_stacked_candidate(tmp_path):
    repo = _stacked_repo(tmp_path)
    _git(repo, "push", "-q", "origin", "feat/parent:main")  # parent fast-forwarded into main, branch not deleted
    _git(repo, "fetch", "-q", "origin")
    meta = _meta(repo)["branch_meta"]
    assert (meta["target"], meta["target_source"]) == ("main", "default")


def test_stacked_tie_stops_with_candidates(tmp_path):
    repo = _stacked_repo(tmp_path)
    _git(repo, "push", "-q", "origin", "feat/parent:refs/heads/feat/parent-twin")
    _git(repo, "fetch", "-q", "origin")
    rc, out, err = run_build(repo, [])
    assert rc == 3
    assert out == ""
    assert "feat/parent" in err and "feat/parent-twin" in err and "--target" in err


def test_print_target_prints_only_the_name(tmp_path):
    rc, out, err = run_build(_stacked_repo(tmp_path), ["--print-target"])
    assert rc == 0, err
    assert out == "feat/parent\n"


@pytest.mark.parametrize("siblings, expected", [
    (2, "note: feat/parent, feat/sib0, feat/sib1 share"),
    (3, "note: feat/parent, feat/sib0, feat/sib1 (+1 more) share"),
])
def test_note_caps_the_diverged_list(tmp_path, siblings, expected):
    repo = _advanced_parent_repo(tmp_path)  # feat/parent advanced after feat/child forked
    for i in range(siblings):
        _git(repo, "switch", "-q", "-c", f"feat/sib{i}", "feat/parent~1")
        _commit(repo, f"sib{i}.py")
        _git(repo, "push", "-q", "origin", f"feat/sib{i}")
    _git(repo, "switch", "-q", "feat/child")
    rc, out, err = run_build(repo, [])
    assert rc == 0, err
    assert expected in err
    assert "feat/sib2" not in err


def test_print_target_without_origin_head_prints_empty_line(tmp_path):
    repo = _stacked_repo(tmp_path)
    _git(repo, "remote", "set-head", "origin", "-d")
    rc, out, err = run_build(repo, ["--print-target"])
    assert rc == 0, err
    assert out == "\n"


def test_print_target_with_explicit_target(tmp_path):
    rc, out, err = run_build(_stacked_repo(tmp_path), ["--target", "main", "--print-target"])
    assert rc == 0, err
    assert out == "main\n"


def test_print_target_on_tie_keeps_stdout_empty(tmp_path):
    repo = _stacked_repo(tmp_path)
    _git(repo, "push", "-q", "origin", "feat/parent:refs/heads/feat/parent-twin")
    _git(repo, "fetch", "-q", "origin")
    rc, out, err = run_build(repo, ["--print-target"])
    assert rc == 3
    assert out == ""
    assert "STOP:" in err
