"""pipeline_poll_github.py is executed directly, so it is code and gets tested like
code: we run the real file against a fake `gh`. Python port of
pipeline_poll_github.sh's test suite (OSS migration step 7c) -- same exit-code
contract, same 3-vs-5 liveness-probe split. Dropped: test_bash_is_pinned_on_both_ends
(moot once the script is Python, not bash).

What each case pins down:
  0 every check concluded | 1 refused to start | 3 the PR has no checks |
  4 a ceiling came due with no conclusion |
  5 the read that hit the ceiling could not be verified, so the state is unverified
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
SIMO = TESTS.parents[1]                        # lib/tests -> lib -> simo-dev
SCRIPT = SIMO / "skills" / "mr-followup" / "lib" / "pipeline_poll_github.py"
PR = "4242"

ENV_FAST = {"POLL_INTERVAL": "1", "POLL_MAX_WAIT": "3", "POLL_NOPIPE_MAX_WAIT": "2"}
TIMEOUT = 30
FAKE_HEAD = "f" * 40


def _init_repo(path):
    """A real one-commit repo, so the script's `git rev-parse HEAD` has an answer."""
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def _run(tmp_path, mode, pr=PR, env=None, timeout=TIMEOUT, counter=None, args=None,
         expect_pr=PR):
    """Run the real script with the fake `gh` first on PATH. Default argv is
    `<pr> <tmp_path>`, with tmp_path a real repo whose HEAD the fake reports as the PR
    head; the process runs from another directory and the fake answers only from
    tmp_path, so the script's `cwd=<worktree-abs>` on every gh call is observable. Explicit `args` get FAKE_HEAD instead, a SHA no real HEAD has: fine for the
    refuse-to-start cases, but a test passing a real worktree there would see a stale
    PR head."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "gh"
    shim.write_text("#!/bin/sh\nexec {} {} \"$@\"\n".format(sys.executable, FIXTURES / "fake_gh.py"))
    shim.chmod(0o755)

    run_env = dict(os.environ)
    for var in ("GIT_DIR", "GIT_WORK_TREE"):
        run_env.pop(var, None)
    run_env.update(ENV_FAST)
    run_env.update(env or {})
    run_env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    run_env["FAKE_GH_HEAD_SHA"] = _init_repo(tmp_path) if args is None else FAKE_HEAD
    if args is None:
        run_env["FAKE_GH_EXPECT_CWD"] = str(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    run_env["PATH"] = "{}{}{}".format(bindir, os.pathsep, run_env["PATH"])
    run_env["FAKE_GH_MODE"] = mode
    run_env["FAKE_GH_PR"] = PR
    if expect_pr is not None:
        run_env["FAKE_GH_EXPECT_PR"] = expect_pr
    if counter is not None:
        run_env["FAKE_GH_COUNTER"] = str(counter)

    argv = [sys.executable, str(SCRIPT)] + (args if args is not None else [pr, str(tmp_path)])
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=run_env, cwd=str(elsewhere))


@pytest.mark.parametrize("mode", ["pass", "fail", "skipping", "cancel"])
def test_concluded_checks_exit_zero(tmp_path, mode):
    r = _run(tmp_path, mode)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "All checks concluded." in r.stdout


def test_pending_forever_times_out_with_rc4(tmp_path):
    r = _run(tmp_path, "pending")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "TIMED OUT" in r.stdout


def test_no_checks_exits_rc3_on_the_short_ceiling(tmp_path):
    started = time.monotonic()
    r = _run(tmp_path, "nochecks", env={"POLL_NOPIPE_MAX_WAIT": "2", "POLL_MAX_WAIT": "600"})
    took = time.monotonic() - started
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO CHECKS" in r.stdout
    assert took < 30, "took {:.1f}s — that is POLL_MAX_WAIT, not the no-checks ceiling".format(took)


def test_unreachable_gh_exits_rc5_not_rc3(tmp_path):
    r = _run(tmp_path, "unreachable")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "NO CHECKS" not in r.stdout


def test_rc5_carries_the_cause_gh_printed(tmp_path):
    r = _run(tmp_path, "unreachable")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "simulated forge failure" in r.stdout, "rc 5 dropped the cause: " + r.stdout


def test_unparseable_json_is_not_a_read(tmp_path):
    r = _run(tmp_path, "garbage", env={"POLL_NOPIPE_MAX_WAIT": "2", "POLL_MAX_WAIT": "600"})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "read_ok=0" in r.stdout


def test_forge_dying_after_first_reply_exits_rc5_not_rc3(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "dying", counter=counter)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "NO CHECKS" not in r.stdout
    assert "simulated forge failure" in r.stdout, "rc 5 dropped the cause: " + r.stdout


def test_forge_dying_while_checks_pending_exits_rc5_not_rc4(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "pending_then_dead", counter=counter)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "TIMED OUT" not in r.stdout


def test_seen_stays_sticky_when_the_checks_vanish(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "pending_then_nochecks", counter=counter)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "checks_seen=1" in r.stdout
    assert "NO CHECKS" not in r.stdout


def test_transient_gh_failure_does_not_end_the_poll(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "blip", counter=counter,
             env={"POLL_NOPIPE_MAX_WAIT": "20", "POLL_MAX_WAIT": "20"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "All checks concluded." in r.stdout


def test_probe_runs_only_when_a_ceiling_is_due(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "nochecks", counter=counter,
             env={"POLL_INTERVAL": "1", "POLL_NOPIPE_MAX_WAIT": "3", "POLL_MAX_WAIT": "600"})
    assert r.returncode == 3, r.stdout + r.stderr
    lines = counter.read_text().split()
    assert lines.count("view") == 1, "probe ran {} times: {}".format(lines.count("view"), lines)
    assert lines.count("checks") >= 2, "the poll only read once: " + str(lines)


def test_a_green_poll_never_probes(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "pass", counter=counter)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "view" not in counter.read_text().split()


def test_missing_pr_argument_refuses_to_start(tmp_path):
    r = _run(tmp_path, "pass", args=[])
    assert r.returncode == 1, r.stdout + r.stderr


def test_missing_worktree_argument_refuses_to_start(tmp_path):
    r = _run(tmp_path, "pass", args=[PR])
    assert r.returncode == 1, r.stdout + r.stderr


def test_empty_worktree_literal_refuses_to_start(tmp_path):
    r = _run(tmp_path, "pass", args=[PR, ""])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "empty <worktree-abs>" in r.stderr, "rc 1 came from another path, not the guard"


def test_unreadable_pr_head_is_unverified_not_no_checks(tmp_path):
    """The head read fails while the liveness probe answers: nothing is known about
    HEAD's checks, so rc 5, never rc 3."""
    r = _run(tmp_path, "pass", env={"FAKE_GH_HEAD_FAIL": "1"})
    assert r.returncode == 5, r.stdout + r.stderr
    assert "head read failed" in r.stdout, "rc 5 dropped the cause: " + r.stdout


def test_worktree_that_is_not_a_git_repo_refuses_to_start(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _run(tmp_path, "pass", args=[PR, str(plain)])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "HEAD" in r.stderr


def test_checks_of_an_older_pr_head_are_not_reported(tmp_path):
    """Right after a push the PR can still point at the previous commit, whose checks
    already concluded. Pre-fix this was rc 0."""
    r = _run(tmp_path, "pass", env={"FAKE_GH_STALE_HEAD_CALLS": "all"})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "All checks concluded" not in r.stdout
    assert "NO CHECKS for" in r.stdout, "lost the head-mismatch line callers are told to read"


def test_checks_read_only_once_the_pr_head_is_head(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "pass", counter=counter,
             env={"FAKE_GH_STALE_HEAD_CALLS": "1", "POLL_NOPIPE_MAX_WAIT": "20", "POLL_MAX_WAIT": "20"})
    assert r.returncode == 0, r.stdout + r.stderr
    tokens = counter.read_text().split()
    assert tokens.count("head") == 2 and tokens.count("checks") == 1, tokens


def test_head_moving_past_head_after_checks_seen_ends_at_once(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "pending", counter=counter,
             env={"FAKE_GH_HEAD_MOVED_AFTER": "1", "POLL_NOPIPE_MAX_WAIT": "20", "POLL_MAX_WAIT": "20"})
    assert r.returncode == 4, r.stdout + r.stderr
    assert "HEAD MOVED" in r.stdout
    assert counter.read_text().split().count("checks") == 1, "kept polling past a moved head"


def test_worktree_vanishing_mid_poll_ends_with_rc1_not_a_traceback(tmp_path):
    """The first head read deletes the worktree; the next gh call can no longer run
    there and the poll must say so in one line, rc 1."""
    r = _run(tmp_path, "pass", env={"FAKE_GH_STALE_HEAD_CALLS": "all",
                                    "FAKE_GH_RMTREE_ON_HEAD": str(tmp_path),
                                    "POLL_NOPIPE_MAX_WAIT": "20", "POLL_MAX_WAIT": "20"})
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cannot run gh in worktree" in r.stderr
    assert "Traceback" not in r.stderr, r.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores mode 000")
def test_unusable_worktree_mid_poll_ends_with_rc1_not_a_traceback(tmp_path):
    """Mode 000 on the worktree: the next gh call's chdir raises PermissionError, which
    a FileNotFoundError-only handler would let through as a traceback."""
    wt = tmp_path / "wt"
    wt.mkdir()
    _init_repo(wt)
    try:
        r = _run(tmp_path, "pass", args=[PR, str(wt)],
                 env={"FAKE_GH_STALE_HEAD_CALLS": "all",
                      "FAKE_GH_CHMOD000_ON_HEAD": str(wt),
                      "POLL_NOPIPE_MAX_WAIT": "20", "POLL_MAX_WAIT": "20"})
    finally:
        os.chmod(wt, 0o755)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cannot run gh in worktree" in r.stderr
    assert "Permission denied" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the missing exec bit")
def test_git_that_cannot_run_refuses_to_start_without_a_traceback(tmp_path):
    """PATH holds only a non-executable `git`: rev-parse raises PermissionError, which
    must read as an unresolvable HEAD (rc 1), not a traceback."""
    wt = tmp_path / "wt"
    wt.mkdir()
    bindir = tmp_path / "nogit"
    bindir.mkdir()
    (bindir / "git").write_text("#!/bin/sh\n")
    (bindir / "git").chmod(0o644)
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env["PATH"] = str(bindir)
    r = subprocess.run([sys.executable, str(SCRIPT)] + [PR, str(wt)], capture_output=True,
                       text=True, timeout=TIMEOUT, env=env, cwd=str(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cannot resolve HEAD" in r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_pr_number_reaches_gh(tmp_path):
    r = _run(tmp_path, "pass", pr="9999", expect_pr=PR)
    assert r.returncode != 0, "the fake accepted a PR number the script should not have sent"


@pytest.mark.parametrize("interval", ["abc", "0"])
def test_unusable_interval_is_coerced_not_busy_spun(tmp_path, interval):
    """A non-digit or zero POLL_INTERVAL must not turn the loop into a gh hammer --
    coerced to 30s, at most one call fits in the 4s window below."""
    counter = tmp_path / "calls"
    with pytest.raises(subprocess.TimeoutExpired):
        _run(tmp_path, "pending", env={"POLL_INTERVAL": interval, "POLL_MAX_WAIT": "9999",
                                       "POLL_NOPIPE_MAX_WAIT": "9999"},
             timeout=4, counter=counter)
    calls = counter.read_text().split().count("checks")
    assert calls <= 2, "busy-spin: gh was called {} times".format(calls)


@pytest.mark.parametrize("var,mode,other", [
    ("POLL_MAX_WAIT", "pending", {"POLL_NOPIPE_MAX_WAIT": "9999"}),
    ("POLL_NOPIPE_MAX_WAIT", "nochecks", {"POLL_MAX_WAIT": "9999"}),
])
def test_unusable_ceiling_is_coerced_to_its_default(tmp_path, var, mode, other):
    """A non-digit ceiling must not disable the ceiling outright -- coerced to its
    (large) default, so this times out within the test window without erroring."""
    env = {var: "abc", "POLL_INTERVAL": "1"}
    env.update(other)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run(tmp_path, mode, env=env, timeout=4)
    err = (caught.value.stderr or b"").decode()
    assert not err.strip(), "ceiling comparison is erroring, so it can never come due: " + err


@pytest.mark.parametrize("var,mode,other", [
    ("POLL_MAX_WAIT", "pending", {"POLL_NOPIPE_MAX_WAIT": "9999"}),
    ("POLL_NOPIPE_MAX_WAIT", "nochecks", {"POLL_MAX_WAIT": "9999"}),
    ("POLL_INTERVAL", "pending", {"POLL_MAX_WAIT": "9999", "POLL_NOPIPE_MAX_WAIT": "9999"}),
])
def test_oversized_override_is_coerced_to_its_default(tmp_path, var, mode, other):
    """All-digits is not enough: an oversized override is coerced to its (large)
    default, so this still times out within the test window -- exactly like an
    UNCOERCED value would. The assertion is on stderr, not the exit code."""
    env = {"POLL_INTERVAL": "1"}
    env.update(other)
    env[var] = "9" * 20
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run(tmp_path, mode, env=env, timeout=4)
    err = (caught.value.stderr or b"").decode()
    assert not err.strip(), "ceiling comparison is erroring, so it can never come due: " + err


def test_ceilings_count_wall_clock_not_sleeps(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "pending", counter=counter, timeout=20,
             env={"POLL_MAX_WAIT": "2", "POLL_NOPIPE_MAX_WAIT": "2", "POLL_INTERVAL": "1",
                  "FAKE_GH_DELAY": "2"})
    assert r.returncode == 4, r.stdout + r.stderr
    calls = counter.read_text().split().count("checks")
    assert calls == 1, "ceiling is counting sleeps, not elapsed time: {} calls".format(calls)


def test_interval_larger_than_the_ceiling_still_terminates_within_the_bound(tmp_path):
    counter = tmp_path / "calls"
    started = time.monotonic()
    r = _run(tmp_path, "pending", timeout=20, counter=counter,
             env={"POLL_MAX_WAIT": "2", "POLL_NOPIPE_MAX_WAIT": "2", "POLL_INTERVAL": "3"})
    took = time.monotonic() - started
    assert r.returncode == 4, r.stdout + r.stderr
    calls = counter.read_text().split().count("checks")
    assert calls == 2, "polled {} times; the ceiling is late by an interval".format(calls)
    assert took < 2 + 3 + 2, "verdict took {:.1f}s, past ceiling + interval + slack".format(took)


def test_lowered_max_wait_does_not_outrun_the_no_checks_ceiling(tmp_path):
    r = _run(tmp_path, "nochecks", env={"POLL_MAX_WAIT": "1", "POLL_NOPIPE_MAX_WAIT": "60"})
    assert r.returncode == 3, r.stdout + r.stderr
