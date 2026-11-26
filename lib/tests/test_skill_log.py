"""Tests for lib/skill_log.py (dev-flow skill log reader/pruner)."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "skill_log.py"

spec = importlib.util.spec_from_file_location("skill_log", LIB)
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

LEGACY = "upbeat-nightingale-581454\tclaude/upbeat-nightingale-581454\tsimo-dev:task-intake\n"
OTHER_SESSION = "2026-07-28T08:00:00Z\tsess-other\tservice-a\tdevelop\tsimo-dev:commit\n"
MINE_A = "2026-07-28T08:22:52Z\tsess-mine\tmyrepo\tclaude/frosty-wilson\tsimo-dev:task-intake\n"
MINE_B = "2026-07-28T08:26:08Z\tsess-mine\tmyrepo\tclaude/frosty-wilson\tsimo-dev:plan-write\n"
MINE_DUP = "2026-07-28T09:00:00Z\tsess-mine\tmyrepo\tclaude/frosty-wilson\tsimo-dev:task-intake\n"
# A session whose cwd IS the task worktree: the branch column is the task branch.
SPAWNED = "2026-07-28T09:30:00Z\tsess-spawned\tmyrepo\tfix/my-task\tsimo-dev:review-loop\n"
# An earlier task in the SAME session, parked before the merge so never pruned.
EARLIER = "2026-07-28T06:30:00Z\tsess-mine\tmyrepo\tclaude/frosty-wilson\tsimo-dev:grill-me\n"


def write_log(tmp_path, *lines):
    log = tmp_path / "dev-flow-skills.log"
    log.write_text("".join(lines))
    return log


def run(action, log, *args, env=None):
    out = subprocess.run(
        [sys.executable, str(LIB), action, "--log", str(log), *args],
        capture_output=True, text=True, check=True,
        env=None if env is None else {**os.environ, **env},
    )
    return out


def read(log, *args, env=None):
    return run("read", log, *args, env=env).stdout.strip()


def test_legacy_three_field_line_never_matches():
    assert not sl.belongs(LEGACY, "sess-mine", "myrepo", "claude/upbeat-nightingale-581454")
    assert not sl.prunable(LEGACY, "myrepo", "claude/upbeat-nightingale-581454")


def test_session_id_matches_regardless_of_repo_and_branch():
    assert sl.belongs(MINE_A, "sess-mine", "", "")


def test_repo_and_branch_match_without_session():
    assert sl.belongs(SPAWNED, "sess-mine", "myrepo", "fix/my-task")


def test_repo_alone_does_not_match():
    assert not sl.belongs(OTHER_SESSION, "sess-mine", "service-a", "")


def test_branch_alone_does_not_match():
    assert not sl.belongs(OTHER_SESSION, "sess-mine", "", "develop")


def test_read_unions_session_and_repo_branch(tmp_path):
    log = write_log(tmp_path, LEGACY, OTHER_SESSION, MINE_A, MINE_B, SPAWNED)
    got = read(log, "--session", "sess-mine", "--repo", "myrepo", "--branch", "fix/my-task")
    assert got == "simo-dev:plan-write,simo-dev:review-loop,simo-dev:task-intake"


def test_read_keeps_every_entry_of_the_task_including_pre_commit_ones(tmp_path):
    """The entries this log exists for are all logged before the first commit —
    task-intake and plan-write precede simo-dev:commit. Nothing may filter them."""
    log = write_log(tmp_path, MINE_A, MINE_B)
    assert read(log, "--session", "sess-mine") == "simo-dev:plan-write,simo-dev:task-intake"


def test_read_absorbs_an_earlier_task_of_the_same_session(tmp_path):
    """Documented limit: no time bound is obtainable, so an earlier task of the
    same session leaks in. Over-reporting beats dropping the current task."""
    log = write_log(tmp_path, EARLIER, MINE_A, MINE_B)
    got = read(log, "--session", "sess-mine")
    assert got == "simo-dev:grill-me,simo-dev:plan-write,simo-dev:task-intake"


def test_read_deduplicates_repeated_skills(tmp_path):
    log = write_log(tmp_path, MINE_A, MINE_DUP)
    assert read(log, "--session", "sess-mine") == "simo-dev:task-intake"


def test_read_empty_when_nothing_matches(tmp_path):
    log = write_log(tmp_path, LEGACY, OTHER_SESSION)
    assert read(log, "--session", "sess-mine") == ""


def test_read_missing_log_is_not_an_error(tmp_path):
    assert read(tmp_path / "absent.log", "--session", "sess-mine") == ""


def test_session_defaults_to_the_env_var(tmp_path):
    log = write_log(tmp_path, LEGACY, OTHER_SESSION, MINE_A)
    assert read(log, env={"CLAUDE_CODE_SESSION_ID": "sess-mine"}) == "simo-dev:task-intake"


def test_prune_removes_the_repo_branch_lines(tmp_path):
    log = write_log(tmp_path, LEGACY, OTHER_SESSION, SPAWNED)
    out = run("prune", log, "--session", "sess-spawned", "--repo", "myrepo", "--branch", "fix/my-task")
    assert log.read_text() == LEGACY + OTHER_SESSION
    assert "pruned 1 lines" in out.stderr


def test_prune_never_removes_a_session_only_match(tmp_path):
    """Session-keyed lines can belong to an interleaved task — deleting them
    would destroy the other task's entries, so they are always kept."""
    log = write_log(tmp_path, MINE_A, MINE_B)
    out = run("prune", log, "--session", "sess-mine")
    assert log.read_text() == MINE_A + MINE_B
    assert "pruned 0 lines" in out.stderr


def test_prune_reports_zero_on_an_unsubstituted_placeholder(tmp_path):
    log = write_log(tmp_path, MINE_A, SPAWNED)
    out = run("prune", log, "--repo", "myrepo", "--branch", "<the merged branch name>")
    assert log.read_text() == MINE_A + SPAWNED
    assert "pruned 0 lines" in out.stderr


def test_prune_does_not_create_a_missing_log(tmp_path):
    absent = tmp_path / "absent.log"
    run("prune", absent, "--session", "sess-mine")
    assert not absent.exists()


def test_needs_a_key(tmp_path):
    log = write_log(tmp_path, MINE_A)
    out = subprocess.run(
        [sys.executable, str(LIB), "read", "--log", str(log)],
        capture_output=True, text=True,
        env={**os.environ, "CLAUDE_CODE_SESSION_ID": ""},
    )
    assert out.returncode != 0
    assert "session" in out.stderr
