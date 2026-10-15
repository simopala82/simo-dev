"""Tests for lib/check_base_freshness.py (stale-base guard)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "check_base_freshness.py"

spec = importlib.util.spec_from_file_location("check_base_freshness", LIB)
cbf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cbf)


def test_decide_not_advanced():
    assert cbf.decide(False, []) == (0, "")


def test_decide_advanced_disjoint():
    rc, msg = cbf.decide(True, [])
    assert rc == 0
    assert "disjoint" in msg


def test_decide_advanced_overlap_stops():
    rc, msg = cbf.decide(True, ["lib/a.py", "lib/b.py"])
    assert rc == 1
    assert "lib/a.py" in msg and "lib/b.py" in msg
    assert "rebase" in msg.lower()


def test_main_safe(monkeypatch):
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (False, []))
    assert cbf.main(["--repo", "/x"]) == 0


def test_main_disjoint_advance(monkeypatch, capsys):
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (True, []))
    assert cbf.main(["--repo", "/x"]) == 0
    assert "disjoint" in capsys.readouterr().err


def test_main_overlap_stops(monkeypatch, capsys):
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (True, ["lib/x.py"]))
    rc = cbf.main(["--repo", "/x"])
    assert rc == 1
    assert "lib/x.py" in capsys.readouterr().out


def test_main_git_error_returns_2(monkeypatch):
    def boom(repo):
        raise cbf.GitError("origin/HEAD not set")

    monkeypatch.setattr(cbf, "default_base", boom)
    assert cbf.main(["--repo", "/x"]) == 2


def test_main_base_override_skips_default(monkeypatch):
    called = {"default": False}

    def default_base(repo):
        called["default"] = True
        return "master"

    monkeypatch.setattr(cbf, "default_base", default_base)
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base: (False, []))
    assert cbf.main(["--repo", "/x", "--base", "develop"]) == 0
    assert called["default"] is False


# --- integration tests for analyze() against real git repos ---


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path, advance, feat_commit=True):
    """Build origin + clone; feat forks at init; base advances per `advance`
    ('overlap' touches shared.py, 'disjoint' touches other.py, None: no advance).
    feat_commit=True → feat commits a shared.py change; False → feat has no commit
    (HEAD sits at the fork point, to isolate working-tree-only overlap).
    Returns the clone path with HEAD on feat."""
    up, clone = tmp_path / "up", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    (clone / "shared.py").write_text("a\n")
    (clone / "other.py").write_text("x\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "init")
    _git(clone, "remote", "add", "origin", str(up))
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    _git(clone, "checkout", "-q", "-b", "feat")
    if feat_commit:
        (clone / "shared.py").write_text("a\nfeat\n")
        _git(clone, "add", "-A")
        _git(clone, "commit", "-qm", "feat change")
    if advance:
        _git(clone, "checkout", "-q", "master")
        target = "shared.py" if advance == "overlap" else "other.py"
        (clone / target).write_text("advanced\n")
        _git(clone, "commit", "-qam", "base advance")
        _git(clone, "push", "-q", "origin", "master")
        _git(clone, "update-ref", "refs/remotes/origin/master", "refs/heads/master")
        _git(clone, "checkout", "-q", "feat")
    return clone


def test_analyze_no_advance(tmp_path):
    clone = _make_repo(tmp_path, advance=None)
    assert cbf.analyze(str(clone), "master") == (False, [])


def test_analyze_overlap(tmp_path):
    clone = _make_repo(tmp_path, advance="overlap")
    assert cbf.analyze(str(clone), "master") == (True, ["shared.py"])


def test_analyze_disjoint(tmp_path):
    clone = _make_repo(tmp_path, advance="disjoint")
    assert cbf.analyze(str(clone), "master") == (True, [])


def test_analyze_catches_uncommitted_overlap(tmp_path):
    """feat has NO committed change to shared.py (HEAD sits at the fork point);
    it touches shared.py only in the working tree. The pre-fix logic
    (merge_base..HEAD) returns [] here; the fix (merge_base..worktree) must catch
    the overlap. Isolates the pre-commit blind spot."""
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    (clone / "shared.py").write_text("a\nuncommitted feat\n")  # unstaged, uncommitted
    assert cbf.analyze(str(clone), "master") == (True, ["shared.py"])


def test_default_base_falls_back_when_head_unset(tmp_path):
    clone = _make_repo(tmp_path, advance=None)
    _git(clone, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
    assert cbf.default_base(str(clone)) == "master"


# --- --files / --plan-files override ---


def test_files_from_plan_bare_path():
    text = "**Files:**\n- Create: `lib/foo.py`\n- Test: `tests/test_foo.py`\n"
    assert cbf.files_from_plan(text) == {"lib/foo.py", "tests/test_foo.py"}


def test_files_from_plan_strips_line_range():
    text = "- Modify: `lib/bar.py:12-34`\n"
    assert cbf.files_from_plan(text) == {"lib/bar.py"}


def test_files_from_plan_ignores_anchor_token():
    text = "- Modify: `docs/adr/0001-x.md` at `## Some Heading`\n"
    assert cbf.files_from_plan(text) == {"docs/adr/0001-x.md"}


def test_files_from_plan_rejects_placeholder_path():
    """Observed case: a plan declaring `P` in Global Constraints and writing
    Files bullets as `<P>/...` -- the literal never matches a repo path, so
    every caller silently saw no overlap."""
    text = "- Modify: `.claude/CLAUDE.md`\n- Modify: `<P>/docs/canonical-flow.md`\n"
    with pytest.raises(cbf.PlanPathError, match=r"<P>/docs/canonical-flow\.md") as exc:
        cbf.files_from_plan(text)
    assert exc.value.bad == ["<P>/docs/canonical-flow.md"]
    assert exc.value.valid == {".claude/CLAUDE.md"}


def test_files_from_plan_rejects_shell_variable_path():
    with pytest.raises(cbf.PlanPathError, match=r"\$P/lib/x\.py"):
        cbf.files_from_plan("- Create: `$P/lib/x.py`\n")


def test_files_from_plan_fenced_placeholder_not_an_error():
    """A plan quoting the forbidden form inside a code fence is not declaring
    it; literal paths inside fences are still collected (a nested fence can
    mis-pair, and dropping real paths would be a silent miss)."""
    text = "```markdown\n- Modify: `<P>/x.md`\n- Create: `lib/fenced.py`\n```\n- Modify: `lib/real.py`\n"
    assert cbf.files_from_plan(text) == {"lib/fenced.py", "lib/real.py"}


def test_files_from_plan_longer_closing_fence_ends_block():
    """CommonMark: a closing fence may be longer than the opener."""
    text = "```\n- Modify: `<P>/a.md`\n````\n- Modify: `<P>/real.md`\n"
    with pytest.raises(cbf.PlanPathError) as exc:
        cbf.files_from_plan(text)
    assert exc.value.bad == ["<P>/real.md"]


def test_files_from_plan_tilde_fence_and_unclosed_fence():
    assert cbf.files_from_plan("~~~\n- Modify: `<P>/a.md`\n~~~\n") == set()
    with pytest.raises(cbf.PlanPathError, match=r"<P>/a\.md"):
        cbf.files_from_plan("```python\n- Modify: `<P>/a.md`\n")


def test_files_from_plan_fence_closes_only_on_same_char_bare_line():
    """A `~~~` line or an info-string line (```` ```python ````) inside a
    backtick fence does not close it."""
    assert cbf.files_from_plan("```\n~~~\n- Modify: `<P>/a.md`\n```\n") == set()
    assert cbf.files_from_plan("```\n```python\n- Modify: `<P>/a.md`\n```\n") == set()


def test_files_from_plan_backtick_in_info_string_is_not_a_fence():
    """CommonMark: ```` ``` ```` followed by text holding a backtick is inline
    code, not a fence opener, so it cannot hide the bullets after it."""
    text = "``` `inline` prose\n- Modify: `<P>/a.md`\n````\n"
    with pytest.raises(cbf.PlanPathError, match=r"<P>/a\.md"):
        cbf.files_from_plan(text)


def test_files_from_plan_placeholder_inside_and_outside_fence_is_reported():
    text = "```\n- Modify: `<P>/a.md`\n```\n- Modify: `<P>/a.md`\n"
    with pytest.raises(cbf.PlanPathError) as exc:
        cbf.files_from_plan(text)
    assert exc.value.bad == ["<P>/a.md"]


def test_files_from_plan_dollar_inside_real_filename_is_literal():
    """`$` in a real file name (route files, nested-class files) is not a placeholder."""
    text = "- Modify: `app/routes/posts.$slug.tsx`\n- Modify: `src/Foo$Bar.java`\n"
    assert cbf.files_from_plan(text) == {"app/routes/posts.$slug.tsx", "src/Foo$Bar.java"}


def test_files_from_plan_dir_unions_multiple_plans(tmp_path):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "a.md").write_text("- Create: `lib/a.py`\n")
    (plans / "b.md").write_text("- Modify: `lib/b.py:1-2`\n")
    assert cbf.files_from_plan_dir(str(tmp_path), "master") == ["lib/a.py", "lib/b.py"]


def test_files_from_plan_dir_empty_when_no_plans(tmp_path):
    assert cbf.files_from_plan_dir(str(tmp_path), "master") == []


def test_files_from_plan_dir_collects_placeholder_errors(tmp_path):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "a.md").write_text("- Modify: `lib/a.py`\n- Modify: `<P>/x.py`\n")
    (plans / "b.md").write_text("- Modify: `lib/b.py`\n")
    errors: list[str] = []
    assert cbf.files_from_plan_dir(str(tmp_path), "master", errors) == ["lib/a.py", "lib/b.py"]
    assert len(errors) == 1
    assert errors[0].startswith("docs/plans/a.md: ") and "<P>/x.py" in errors[0]


def test_files_from_plan_dir_without_errors_list_propagates(tmp_path):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "a.md").write_text("- Modify: `<P>/x.py`\n")
    with pytest.raises(cbf.PlanPathError):
        cbf.files_from_plan_dir(str(tmp_path), "master")


def test_analyze_files_override_replaces_working_tree_scan(tmp_path):
    """feat has no working-tree change to shared.py at all (feat_commit=False,
    no post-checkout edit) -- a bare analyze() call must report no overlap.
    Passing files=['shared.py'] must still catch it: at a task-intake gate the
    working tree holds only the plan, never the target files themselves."""
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    assert cbf.analyze(str(clone), "master") == (True, [])
    assert cbf.analyze(str(clone), "master", files=["shared.py"]) == (True, ["shared.py"])


def test_analyze_files_override_empty_list_means_no_overlap(tmp_path):
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    assert cbf.analyze(str(clone), "master", files=[]) == (True, [])


def test_main_files_flag_passes_override(monkeypatch):
    captured = {}

    def fake_analyze(repo, base, files=None):
        captured["files"] = files
        return (False, [])

    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", fake_analyze)
    assert cbf.main(["--repo", "/x", "--files", "a.py", "b.py"]) == 0
    assert captured["files"] == ["a.py", "b.py"]


def test_main_plan_files_flag_derives_from_disk(tmp_path, monkeypatch):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Create: `lib/x.py`\n")
    captured = {}

    def fake_analyze(repo, base, files=None):
        captured["files"] = files
        return (False, [])

    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", fake_analyze)
    assert cbf.main(["--repo", str(tmp_path), "--plan-files"]) == 0
    assert captured["files"] == ["lib/x.py"]


def test_main_files_and_plan_files_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cbf.main(["--repo", "/x", "--files", "a.py", "--plan-files"])


def test_main_plan_files_placeholder_path_returns_2(tmp_path, monkeypatch, capsys):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Modify: `<P>/README.md`\n")
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    monkeypatch.setattr(cbf, "analyze", lambda repo, base, files=None: (False, []))  # pre-fix: rc 0, the silent miss
    assert cbf.main(["--repo", str(tmp_path), "--plan-files"]) == 2
    err = capsys.readouterr().err
    assert "docs/plans/p.md" in err and "<P>/README.md" in err


def test_main_plan_files_placeholder_does_not_hide_overlap(tmp_path, monkeypatch, capsys):
    """One broken plan must not turn a real overlap from another plan into
    rc 2 -- callers read rc 2 as 'no change' (same fail-open the vanished-plan
    skip guards against)."""
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "broken.md").write_text("- Modify: `<P>/x.py`\n")
    (plans / "real.md").write_text("- Modify: `shared.py`\n")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    assert cbf.main(["--repo", str(clone), "--base", "master", "--plan-files"]) == 1
    out, err = capsys.readouterr()
    assert "shared.py" in out
    assert "docs/plans/broken.md" in err and "<P>/x.py" in err


def test_main_plan_files_git_error_still_prints_placeholder_errors(tmp_path, monkeypatch, capsys):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Modify: `<P>/README.md`\n")
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)

    def boom(repo, base, files=None):
        raise cbf.GitError("analyze failed")

    monkeypatch.setattr(cbf, "analyze", boom)
    assert cbf.main(["--repo", str(tmp_path), "--plan-files"]) == 2
    assert "<P>/README.md" in capsys.readouterr().err


def test_main_plan_files_unreadable_plan_returns_2(tmp_path, monkeypatch):
    """A plan file that can't be decoded must map to rc 2 (error), never an
    uncaught exception that would otherwise surface as an unhandled crash
    instead of the documented 'never a false STOP' contract."""
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_bytes(b"\xff\xfe- Create: `lib/x.py`\n")
    monkeypatch.setattr(cbf, "default_base", lambda repo: "master")
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    assert cbf.main(["--repo", str(tmp_path), "--plan-files"]) == 2


def test_main_plan_files_skips_plan_vanished_between_glob_and_read(tmp_path, monkeypatch, capsys):
    """A concurrent session can carry/mv an untracked plan out of docs/plans/
    after the glob listed it. That plan must be skipped, not turn the whole
    check into rc 2 -- rc 2 is read as 'no change' by the callers, so the real
    overlap of the remaining plans would never surface (silent fail-open)."""
    clone = _make_repo(tmp_path, advance="overlap", feat_commit=False)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    real = plans / "real.md"
    real.write_text("- Modify: `shared.py`\n")
    ghost = plans / "ghost.md"
    monkeypatch.setattr(cbf.glob, "glob", lambda pattern: [str(ghost), str(real)])
    monkeypatch.setattr(cbf, "fetch_base", lambda repo, base: None)
    assert cbf.main(["--repo", str(clone), "--base", "master", "--plan-files"]) == 1
    assert "shared.py" in capsys.readouterr().out


def test_files_from_plan_dir_excludes_merged_plans(tmp_path):
    """A plan file already present in origin/<base> (already merged) has its
    Files: bullets excluded; a plan file that does NOT yet exist there (the
    current task's own new plan) is still included. Isolates the bug: an
    unscoped union resurrects declared files from tasks long since merged
    and irrelevant to the branch under check."""
    up, clone = tmp_path / "up", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "old.md").write_text("- Create: `old_target.py`\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "old plan, merged")
    _git(clone, "remote", "add", "origin", str(up))
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    _git(clone, "checkout", "-q", "-b", "feat")
    (plans / "new.md").write_text("- Create: `new_target.py`\n")  # uncommitted -- current task's plan
    assert cbf.files_from_plan_dir(str(clone), "master") == ["new_target.py"]
