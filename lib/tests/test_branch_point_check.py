"""Tests for lib/branch_point_check.py."""
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "branch_point_check.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "doc.md").write_text("one\ntwo\nthree\nfour\nfive\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "keep.md").write_text("k\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def run(repo, base, *targets):
    return subprocess.run([sys.executable, str(LIB), "--repo", str(repo), "--base-sha", base, *targets], capture_output=True, text=True)


def check(repo, base, *targets):
    r = run(repo, base, *targets)
    return r.returncode, dict(line.split("\t", 1) for line in r.stdout.splitlines())


def test_unchanged_line_is_pre_existing(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\ntwo\nNEW\nthree\nfour\nfive\n")
    rc, out = check(repo, base, "doc.md:1", "doc.md:5")
    assert rc == 0
    assert out == {"doc.md:1": "pre-existing", "doc.md:5": "pre-existing"}


def test_single_added_line_hunk_without_count(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\ntwo\nNEW\nthree\nfour\nfive\n")
    assert check(repo, base, "doc.md:3")[1] == {"doc.md:3": "branch-added"}


def test_modified_line_is_branch_added(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\nTWO\nthree\nfour\nfive\n")
    assert check(repo, base, "doc.md:2", "doc.md:3")[1] == {"doc.md:2": "branch-added", "doc.md:3": "pre-existing"}


def test_multi_line_added_block(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\nA\nB\nC\ntwo\nthree\nfour\nfive\n")
    _, out = check(repo, base, "doc.md:2", "doc.md:4", "doc.md:5")
    assert out == {"doc.md:2": "branch-added", "doc.md:4": "branch-added", "doc.md:5": "pre-existing"}


def test_unchanged_line_between_hunks_ignores_inter_hunk_context(tmp_path):
    repo, base = make_repo(tmp_path)
    git(repo, "config", "diff.interHunkContext", "5")
    (repo / "doc.md").write_text("one\nTWO\nthree\nFOUR\nfive\n")
    assert check(repo, base, "doc.md:3")[1] == {"doc.md:3": "pre-existing"}


def test_lines_next_to_a_branch_deletion_are_branch_added(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\nfour\nfive\n")
    _, out = check(repo, base, "doc.md:1", "doc.md:2", "doc.md:3")
    assert out == {"doc.md:1": "branch-added", "doc.md:2": "branch-added", "doc.md:3": "pre-existing"}


def test_deletion_at_file_start_marks_the_new_first_line(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("three\nfour\nfive\n")
    _, out = check(repo, base, "doc.md:1", "doc.md:2")
    assert out == {"doc.md:1": "branch-added", "doc.md:2": "pre-existing"}


def test_non_ascii_target_on_cp1252_stdout(tmp_path):
    from cp1252_run import run_cp1252
    repo, base = make_repo(tmp_path)
    (repo / "é→.md").write_text("x\n")
    r = run_cp1252(LIB, ["--repo", str(repo), "--base-sha", base, "é→.md:1"])
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout.decode("utf-8") == "é→.md:1\tbranch-added\n"


def test_committed_branch_line_is_branch_added(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\ntwo\nthree\nfour\nfive\nsix\n")
    git(repo, "commit", "-q", "-am", "branch work")
    assert check(repo, base, "doc.md:6")[1] == {"doc.md:6": "branch-added"}


def test_untracked_new_file_is_branch_added(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "new.md").write_text("x\ny\n")
    assert check(repo, base, "new.md:1")[1] == {"new.md:1": "branch-added"}


def test_base_file_dropped_from_index_is_error(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\nNEW\ntwo\nthree\nfour\nfive\n")
    git(repo, "rm", "-q", "--cached", "doc.md")
    assert check(repo, base, "doc.md:2")[1]["doc.md:2"].startswith("error: ")


def test_assume_unchanged_and_skip_worktree_are_error(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "sub" / "keep.md").write_text("k\nNEW\n")
    (repo / "doc.md").write_text("one\nNEW\ntwo\nthree\nfour\nfive\n")
    git(repo, "update-index", "--assume-unchanged", "doc.md")
    git(repo, "update-index", "--skip-worktree", "sub/keep.md")
    _, out = check(repo, base, "doc.md:2", "sub/keep.md:2")
    assert out["doc.md:2"].startswith("error: ")
    assert out["sub/keep.md:2"].startswith("error: ")


def test_clean_filter_is_error(tmp_path):
    repo, base = make_repo(tmp_path)
    git(repo, "config", "filter.strip.clean", "sed '/^#/d'")
    (repo / ".gitattributes").write_text("doc.md filter=strip\n")
    (repo / "doc.md").write_text("#x\n#y\none\ntwo\nNEW\nthree\nfour\nfive\n")
    assert check(repo, base, "doc.md:5")[1]["doc.md:5"].startswith("error: ")


def test_glob_characters_in_path_are_literal(tmp_path):
    repo, base = make_repo(tmp_path)
    for d in ("[id]", "i"):
        (repo / d).mkdir()
        (repo / d / "p.md").write_text("a\nb\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "dirs")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "i" / "p.md").write_text("a\nB\n")
    assert check(repo, base, "[id]/p.md:2")[1] == {"[id]/p.md:2": "pre-existing"}


def test_skills_wire_the_check():
    skills = LIB.parent.parent / "skills"
    review_loop = (skills / "review-loop" / "SKILL.md").read_text()
    mr_followup = (skills / "mr-followup" / "SKILL.md").read_text()
    assert "printf 'rc=%s\\nBASE_SHA=%s\\n%s\\n'" in review_loop
    fixer_steps = (skills / "review-loop" / "lib" / "fixer-steps.md").read_text()
    assert '/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "<BASE_SHA>"' in fixer_steps
    assert 'git -C "<worktree-abs>" diff --quiet HEAD || {' in mr_followup
    assert 'every target is error"; exit 0; }' in mr_followup
    assert '/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "$(git -C' in mr_followup
    plan_execute = (skills / "plan-execute" / "SKILL.md").read_text()
    assert '/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "$(git -C "<worktree-abs>" merge-base HEAD "origin/<ledger-base>")"' in plan_execute
    assert "diff --quiet HEAD" not in plan_execute
    assert '/lib/plan_execute_preflight.py" "<plan-path>" --repo "<worktree-abs>" --base "<base>"' in plan_execute
    assert "`LEDGER_BASE=` → `<ledger-base>`" in plan_execute
    dispatch = (skills / "plan-execute" / "lib" / "agent-dispatch.md").read_text()
    assert "`exempt`" in dispatch
    assert "`completes-branch`" in dispatch


def test_missing_file_is_error(tmp_path):
    repo, base = make_repo(tmp_path)
    assert check(repo, base, "nope.md:1")[1]["nope.md:1"].startswith("error: ")


def test_line_outside_file_is_error(tmp_path):
    repo, base = make_repo(tmp_path)
    _, out = check(repo, base, "doc.md:0", "doc.md:99")
    assert out["doc.md:0"].startswith("error: ")
    assert out["doc.md:99"].startswith("error: ")


def test_trailing_blank_lines_are_in_range(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "blank.md").write_text("a\nb\n\n\n")
    git(repo, "add", "blank.md")
    git(repo, "commit", "-q", "-m", "blank")
    base = git(repo, "rev-parse", "HEAD")
    _, out = check(repo, base, "blank.md:4", "blank.md:5")
    assert out["blank.md:4"] == "pre-existing"
    assert out["blank.md:5"].startswith("error: ")


def test_last_line_without_trailing_newline_is_in_range(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\ntwo\nthree\nfour\nfive\nsix")
    assert check(repo, base, "doc.md:6")[1] == {"doc.md:6": "branch-added"}


def test_textconv_and_external_diff_are_ignored(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / ".gitattributes").write_text("doc.md diff=dup\n")
    git(repo, "config", "diff.dup.textconv", "sed p")
    git(repo, "config", "diff.external", "true")
    (repo / "doc.md").write_text("one\nTWO\nthree\nfour\nfive\n")
    assert check(repo, base, "doc.md:2", "doc.md:3")[1] == {"doc.md:2": "branch-added", "doc.md:3": "pre-existing"}


def test_forced_color_is_ignored(tmp_path):
    repo, base = make_repo(tmp_path)
    git(repo, "config", "color.ui", "always")
    git(repo, "config", "color.diff", "always")
    (repo / "doc.md").write_text("one\nTWO\nthree\nfour\nfive\n")
    assert check(repo, base, "doc.md:2")[1] == {"doc.md:2": "branch-added"}


def test_repo_not_a_work_tree_is_error_even_from_inside_a_repo(tmp_path):
    repo, base = make_repo(tmp_path)
    outside = tmp_path / "plain"
    outside.mkdir()
    r = subprocess.run([sys.executable, str(LIB), "--repo", str(outside), "--base-sha", base, "doc.md:1"],
                       capture_output=True, text=True, cwd=repo)
    assert r.returncode == 0
    assert r.stdout.split("\t", 1)[1].startswith("error: ")


def test_absolute_path_is_error(tmp_path):
    repo, base = make_repo(tmp_path)
    target = f"{repo / 'doc.md'}:1"
    assert check(repo, base, target)[1][target].startswith("error: ")


def test_binary_attribute_file_still_reports_added_lines(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / ".gitattributes").write_text("doc.md -diff\n")
    (repo / "doc.md").write_text("one\nNEW\ntwo\nthree\nfour\nfive\n")
    assert check(repo, base, "doc.md:2", "doc.md:3")[1] == {"doc.md:2": "branch-added", "doc.md:3": "pre-existing"}


def test_repo_pointing_at_subdirectory_still_reads_from_toplevel(tmp_path):
    repo, base = make_repo(tmp_path)
    (repo / "doc.md").write_text("one\nTWO\nthree\nfour\nfive\n")
    assert check(repo / "sub", base, "doc.md:2")[1] == {"doc.md:2": "branch-added"}


def test_bad_base_sha_is_error_not_pre_existing(tmp_path):
    repo, _ = make_repo(tmp_path)
    rc, out = check(repo, "0" * 40, "doc.md:1")
    assert rc == 0
    assert out["doc.md:1"].startswith("error: ")


def test_target_without_line_is_rc_2(tmp_path):
    repo, base = make_repo(tmp_path)
    r = run(repo, base, "doc.md")
    assert r.returncode == 2
    assert "bad target" in r.stderr
