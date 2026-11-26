"""task-intake's merged-branch path (lib/worktree-rare-paths.md) must accept a tree whose old branch
was squash-merged (content on origin/<base> at some first-parent commit, whatever base did since),
keep a plain merge passing, stop when HEAD carries content base never held, and cut the new branch
from origin/<base>.
Runs the section's real bash blocks against a temp repo, under bash and zsh."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent
RARE = PLUGIN / "skills" / "task-intake" / "lib" / "worktree-rare-paths.md"
SKILL = PLUGIN / "skills" / "task-intake" / "SKILL.md"
SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]
OLD, NEW = "feat/old-task", "fix/new-task"
# a developer's global git config (autoSetupMerge, gpgsign, ...) must not decide the outcome
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def merged_branch_section():
    text = RARE.read_text()
    i = text.index("## Merged-branch path")
    return text[i:text.index("\n## ", i + 1)]


def blocks():
    section, found, pos = merged_branch_section(), [], 0
    while (start := section.find("```bash\n", pos)) != -1:
        start += len("```bash\n")
        end = section.index("```", start)
        found.append(section[start:end])
        pos = end + 3
    return found


def run_block(shell, block, wt, env=ENV):
    script = (block.replace("<HEADREF>", OLD).replace("<base>", "master")
              .replace("<branch>", NEW).replace("<WT>", str(wt)))
    return subprocess.run([shell, "-c", script], capture_output=True, text=True, env=env)


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=cwd, check=True, capture_output=True, text=True, env=ENV).stdout.strip()


def commit_file(repo, name, content, msg):
    (repo / name).write_text(content)
    git("add", name, cwd=repo)
    git("commit", "-qm", msg, cwd=repo)


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "master", str(origin), cwd=tmp_path)
    wt = tmp_path / "wt"
    git("clone", "-q", str(origin), str(wt), cwd=tmp_path)
    commit_file(wt, "base.txt", "base\n", "init")
    git("push", "-q", "origin", "master", cwd=wt)
    git("switch", "-qc", OLD, cwd=wt)
    commit_file(wt, "a.txt", "one\n", "a1")
    commit_file(wt, "a.txt", "one\ntwo\n", "a2")
    git("push", "-q", "origin", OLD, cwd=wt)
    other = tmp_path / "other"
    git("clone", "-q", str(origin), str(other), cwd=tmp_path)
    return wt, other


def squash_merge(other):
    git("fetch", "-q", "origin", cwd=other)
    git("merge", "--squash", f"origin/{OLD}", cwd=other)
    git("commit", "-qm", "squashed", cwd=other)
    git("push", "-q", "origin", "master", cwd=other)
    git("push", "-q", "origin", "--delete", OLD, cwd=other)


def advance_base(other, name="later.txt", content="later\n"):
    commit_file(other, name, content, "unrelated")
    git("push", "-q", "origin", "master", cwd=other)


def verdict(shell, wt, env=ENV):
    git("fetch", "-q", "origin", "master", cwd=wt)
    result = run_block(shell, blocks()[0], wt, env)
    lines = [l for l in result.stdout.splitlines() if l.startswith("merged=")]
    assert len(lines) == 1, result.stdout + result.stderr
    for line in ("task-name rc=0", "not-base rc=0", "remote rc=2"):
        assert line in result.stdout, result.stdout + result.stderr
    return lines[0]


@pytest.mark.parametrize("shell", SHELLS)
def test_squash_merge_on_advanced_base_passes_as_content(repos, shell):
    wt, other = repos
    squash_merge(other)
    advance_base(other)
    assert verdict(shell, wt) == "merged=content"


@pytest.mark.parametrize("shell", SHELLS)
def test_extra_local_commit_after_squash_stops(repos, shell):
    # conflict case: the extra commit edits a.txt, which the squash added too (add/add) → merge-tree rc 1 at every C
    wt, other = repos
    squash_merge(other)
    advance_base(other)
    commit_file(wt, "a.txt", "one\ntwo\nthree\n", "after merge")
    assert verdict(shell, wt) == "merged=no"


@pytest.mark.parametrize("shell", SHELLS)
def test_extra_new_file_after_squash_stops(repos, shell):
    # clean case: merge-tree rc 0 at the squash commit, so only the tree-equality check can stop it
    wt, other = repos
    squash_merge(other)
    advance_base(other)
    commit_file(wt, "b.txt", "extra\n", "after merge")
    assert verdict(shell, wt) == "merged=no"


@pytest.mark.parametrize("shell", SHELLS)
def test_base_rewrote_the_same_lines_after_squash_passes(repos, shell):
    # the content was on base at the squash commit; later edits on base do not undo that
    wt, other = repos
    squash_merge(other)
    advance_base(other, "a.txt", "rewritten\n")
    assert verdict(shell, wt) == "merged=content"


@pytest.mark.parametrize("shell", SHELLS)
def test_base_deleted_a_file_the_branch_added_passes(repos, shell):
    # same: a.txt was on base at the squash commit, its later removal is base's own change
    wt, other = repos
    squash_merge(other)
    git("rm", "-q", "a.txt", cwd=other)
    git("commit", "-qm", "drop a", cwd=other)
    git("push", "-q", "origin", "master", cwd=other)
    assert verdict(shell, wt) == "merged=content"


@pytest.mark.parametrize("shell", SHELLS)
def test_base_later_bumps_a_line_the_branch_bumped(repos, shell):
    # a release bump on every MR: base's tip conflicts with HEAD, the squash commit does not
    wt, other = repos
    commit_file(wt, "base.txt", "1.1\n", "bump")
    git("push", "-q", "origin", OLD, cwd=wt)
    squash_merge(other)
    advance_base(other, "base.txt", "1.2\n")
    assert verdict(shell, wt) == "merged=content"


@pytest.mark.parametrize("shell", SHELLS)
def test_merge_ours_driver_cannot_fake_a_clean_merge(repos, shell):
    # VERSION changed on both sides; a `merge=ours` driver would resolve to C and give a false content verdict
    wt, other = repos
    commit_file(wt, "VERSION", "1.0\n", "v")
    git("push", "-q", "origin", OLD, cwd=wt)
    git("fetch", "-q", "origin", cwd=other)
    git("merge", "-q", "--squash", f"origin/{OLD}", cwd=other)
    git("commit", "-qm", "squashed", cwd=other)
    commit_file(other, "VERSION", "1.1\n", "bump")
    (other / ".gitattributes").write_text("VERSION merge=ours\n")
    git("add", ".gitattributes", cwd=other)
    git("config", "merge.ours.driver", "true", cwd=other)
    git("commit", "-qm", "attrs", cwd=other)
    git("push", "-q", "origin", "master", cwd=other)
    commit_file(wt, "VERSION", "1.1-HEAD-ONLY\n", "head only")
    git("config", "merge.ours.driver", "true", cwd=wt)
    (wt / ".gitattributes").write_text("VERSION merge=ours\n")
    assert verdict(shell, wt) == "merged=no"


def ours_scenario(wt, other):
    # VERSION changed on both sides; a `merge=ours` driver would resolve to C and give a false content verdict
    commit_file(wt, "VERSION", "1.0\n", "v")
    git("push", "-q", "origin", OLD, cwd=wt)
    squash_merge(other)
    commit_file(other, "VERSION", "1.1\n", "bump")
    git("push", "-q", "origin", "master", cwd=other)
    commit_file(wt, "VERSION", "1.1-HEAD-ONLY\n", "head only")


def union_scenario(wt, other):
    # HEAD deleted a line base changed after the squash: a `merge=union` merge keeps base's side only, equal to C's tree
    git("switch", "-q", "master", cwd=wt)
    commit_file(wt, "f.txt", "a\nb\nc\n", "f")
    git("push", "-q", "origin", "master", cwd=wt)
    git("switch", "-q", OLD, cwd=wt)
    git("rebase", "-q", "master", cwd=wt)
    git("push", "-q", "-f", "origin", OLD, cwd=wt)
    git("pull", "-q", "--ff-only", "origin", "master", cwd=other)
    squash_merge(other)
    commit_file(other, "f.txt", "a\nB2\nc\n", "change b")
    git("push", "-q", "origin", "master", cwd=other)
    commit_file(wt, "f.txt", "a\nc\n", "drop b")


def info_attributes(wt, text):
    path = Path(git("rev-parse", "--path-format=absolute", "--git-path", "info/attributes", cwd=wt))
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)


def hijack_info_ours(wt, tmp_path):
    info_attributes(wt, "VERSION merge=ours\n")
    git("config", "merge.ours.driver", "true", cwd=wt)
    return ENV


def hijack_info_union(wt, tmp_path):
    info_attributes(wt, "f.txt merge=union\n")
    return ENV


def hijack_in_tree_union(wt, tmp_path):
    (wt / ".gitattributes").write_text("f.txt merge=union\n")
    return ENV


def hijack_global_union(wt, tmp_path):
    attrs = tmp_path / "global-attributes"
    attrs.write_text("f.txt merge=union\n")
    cfg = tmp_path / "global-gitconfig"
    cfg.write_text(f"[core]\n\tattributesFile = {attrs}\n")
    return {**ENV, "GIT_CONFIG_GLOBAL": str(cfg)}


def hijack_merge_default(wt, tmp_path):
    git("config", "merge.default", "ours", cwd=wt)
    git("config", "merge.ours.driver", "true", cwd=wt)
    return ENV


def hijack_merge_default_union(wt, tmp_path):
    git("config", "merge.default", "union", cwd=wt)
    return ENV


def hijack_global(wt, tmp_path):
    attrs = tmp_path / "global-attributes"
    attrs.write_text("VERSION merge=ours\n")
    cfg = tmp_path / "global-gitconfig"
    cfg.write_text(f"[core]\n\tattributesFile = {attrs}\n[merge \"ours\"]\n\tdriver = true\n")
    return {**ENV, "GIT_CONFIG_GLOBAL": str(cfg)}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("scenario, hijack", [
    (ours_scenario, hijack_info_ours),
    (union_scenario, hijack_info_union),
    (union_scenario, hijack_in_tree_union),
    (union_scenario, hijack_global_union),
    (ours_scenario, hijack_merge_default),
    (ours_scenario, hijack_global),
    (union_scenario, hijack_merge_default_union),
], ids=["info-attributes-ours", "info-attributes-union", "in-tree-union", "global-attributes-union", "merge-default", "global-config-and-attributes", "merge-default-union"])
def test_untracked_merge_drivers_cannot_fake_a_clean_merge(repos, tmp_path, shell, scenario, hijack):
    wt, other = repos
    scenario(wt, other)
    assert verdict(shell, wt, hijack(wt, tmp_path)) == "merged=no"


@pytest.mark.parametrize("shell", SHELLS)
def test_info_attributes_stop_names_its_cause(repos, shell):
    wt, other = repos
    squash_merge(other)
    info_attributes(wt, "# comment\n")
    git("fetch", "-q", "origin", "master", cwd=wt)
    result = run_block(shell, blocks()[0], wt)
    assert "merged=no" in result.stdout and "cause: info/attributes non-empty" in result.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_plain_merge_passes_as_ancestor(repos, shell):
    wt, other = repos
    git("fetch", "-q", "origin", cwd=other)
    git("merge", "-q", "--no-ff", "-m", "merge", f"origin/{OLD}", cwd=other)
    git("push", "-q", "origin", "master", cwd=other)
    assert verdict(shell, wt) == "merged=ancestor"


@pytest.mark.parametrize("shell", SHELLS)
def test_new_branch_starts_from_base_and_keeps_untracked_plan(repos, shell):
    wt, other = repos
    squash_merge(other)
    advance_base(other)
    git("fetch", "-q", "origin", "master", cwd=wt)
    plan = wt / "docs" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("plan\n")
    result = run_block(shell, blocks()[1], wt)
    assert result.returncode == 0, result.stderr
    assert git("branch", "--show-current", cwd=wt) == NEW
    assert git("rev-parse", "HEAD", cwd=wt) == git("rev-parse", "origin/master", cwd=wt)
    assert plan.read_text() == "plan\n"
    upstream = subprocess.run(["git", "-C", str(wt), "config", f"branch.{NEW}.merge"], capture_output=True, env=ENV)
    assert upstream.returncode == 1, "--no-track: the new branch must not inherit an upstream"


def test_section_names_the_delete_form_per_verdict():
    section = merged_branch_section()
    after = section[section.rindex("```"):]
    assert 'branch -d "<HEADREF>"` on `merged=ancestor' in after
    assert 'branch -D "<HEADREF>"` on `merged=content' in after


def test_step6_defers_named_branches_to_merged_branch_path():
    text = SKILL.read_text()
    step6 = text[text.index("### 6. Branch and worktree"):text.index("### 7. Detailed plan")]
    i = step6.index("`next: merged-branch-path`")
    span = step6[i:step6.index("\n", i)]
    assert '"Merged-branch path"' in span and "`foreign:` lines are expected" in span
    # adopt's own verdict (named branch: no ASK, foreign commits listed) is in test_worktree_resolve.py
