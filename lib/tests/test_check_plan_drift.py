"""Tests for lib/check_plan_drift.py (plan-embeds-a-just-fixed-file detector)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "check_plan_drift.py"

spec = importlib.util.spec_from_file_location("check_plan_drift", LIB)
cpd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpd)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path):
    """Bare origin + clone, master pushed with origin/HEAD set, HEAD on a
    'feat' branch forked from master. Returns the clone path."""
    up, clone = tmp_path / "up", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    (clone / "README.md").write_text("x\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "init")
    _git(clone, "remote", "add", "origin", str(up))
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    _git(clone, "checkout", "-q", "-b", "feat")
    return clone


def test_decide_no_pairs():
    assert cpd.decide([]) == (0, "")


def test_decide_reports_pairs():
    rc, msg = cpd.decide([("docs/plans/a.md", "lib/x.py")])
    assert rc == 1
    assert "docs/plans/a.md" in msg and "lib/x.py" in msg


def test_plans_mentioning_reports_match(tmp_path):
    clone = _make_repo(tmp_path)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Modify: `lib/x.py:1-2`\n")
    assert cpd.plans_mentioning(str(clone), "master", ["lib/x.py"]) == [("docs/plans/p.md", "lib/x.py")]


def test_plans_mentioning_no_overlap(tmp_path):
    clone = _make_repo(tmp_path)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Modify: `lib/other.py`\n")
    assert cpd.plans_mentioning(str(clone), "master", ["lib/x.py"]) == []


def test_plans_mentioning_excludes_merged_plan(tmp_path):
    """A plan already present in origin/<base> (already merged) is excluded
    even when it declares a changed file -- its declared files are stale
    intent for the current round, not a live plan to resync."""
    up, clone = tmp_path / "up", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(up)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "old.md").write_text("- Modify: `lib/x.py`\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "old plan, merged")
    _git(clone, "remote", "add", "origin", str(up))
    _git(clone, "push", "-q", "origin", "master")
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    _git(clone, "checkout", "-q", "-b", "feat")
    assert cpd.plans_mentioning(str(clone), "master", ["lib/x.py"]) == []


def test_plans_mentioning_multiple_files_multiple_plans(tmp_path):
    clone = _make_repo(tmp_path)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "a.md").write_text("- Modify: `lib/x.py`\n")
    (plans / "b.md").write_text("- Modify: `lib/y.py`\n- Test: `lib/x.py`\n")
    pairs = cpd.plans_mentioning(str(clone), "master", ["lib/x.py", "lib/y.py"])
    assert sorted(pairs) == [
        ("docs/plans/a.md", "lib/x.py"),
        ("docs/plans/b.md", "lib/x.py"),
        ("docs/plans/b.md", "lib/y.py"),
    ]


def test_plans_mentioning_no_plans_dir(tmp_path):
    clone = _make_repo(tmp_path)
    assert cpd.plans_mentioning(str(clone), "master", ["lib/x.py"]) == []


def test_main_reports_and_returns_1(tmp_path, capsys):
    clone = _make_repo(tmp_path)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Modify: `lib/x.py`\n")
    rc = cpd.main(["--repo", str(clone), "--base", "master", "--changed-file", "lib/x.py"])
    assert rc == 1
    assert "docs/plans/p.md" in capsys.readouterr().out


def test_main_clean_returns_0(tmp_path):
    clone = _make_repo(tmp_path)
    rc = cpd.main(["--repo", str(clone), "--base", "master", "--changed-file", "lib/x.py"])
    assert rc == 0


def test_main_default_base_omitted(tmp_path, capsys):
    """No --base: main() must resolve the base for real via default_base()
    (origin/HEAD, set by _make_repo to master) and fetch it via fetch_base()
    -- the exact invocation shape review-loop's wiring uses in production
    (skills/review-loop/SKILL.md never passes --base)."""
    clone = _make_repo(tmp_path)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_text("- Modify: `lib/x.py`\n")
    rc = cpd.main(["--repo", str(clone), "--changed-file", "lib/x.py"])
    assert rc == 1
    assert "docs/plans/p.md" in capsys.readouterr().out


def test_main_git_error_returns_2(monkeypatch):
    def boom(repo):
        raise cpd.GitError("origin/HEAD not set")

    monkeypatch.setattr(cpd, "default_base", boom)
    assert cpd.main(["--repo", "/x", "--changed-file", "lib/x.py"]) == 2


def test_main_requires_at_least_one_changed_file():
    with pytest.raises(SystemExit):
        cpd.main(["--repo", "/x"])


def test_main_unreadable_plan_returns_2(tmp_path):
    """A plan file that can't be decoded maps to rc 2 (error), never an
    uncaught exception -- same contract check_base_freshness.py holds for
    --plan-files."""
    clone = _make_repo(tmp_path)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.md").write_bytes(b"\xff\xfe- Modify: `lib/x.py`\n")
    assert cpd.main(["--repo", str(clone), "--base", "master", "--changed-file", "lib/x.py"]) == 2


def test_main_skips_plan_vanished_between_glob_and_read(tmp_path, monkeypatch, capsys):
    """An untracked plan moved away by a concurrent session after the glob
    listed it is skipped -- the remaining plans' matches are still reported,
    instead of rc 2 for the whole check."""
    clone = _make_repo(tmp_path)
    plans = clone / "docs" / "plans"
    plans.mkdir(parents=True)
    real = plans / "real.md"
    real.write_text("- Modify: `lib/x.py`\n")
    ghost = plans / "ghost.md"
    monkeypatch.setattr(cpd.glob, "glob", lambda pattern: [str(ghost), str(real)])
    rc = cpd.main(["--repo", str(clone), "--base", "master", "--changed-file", "lib/x.py"])
    assert rc == 1
    assert "docs/plans/real.md mentions lib/x.py" in capsys.readouterr().out
