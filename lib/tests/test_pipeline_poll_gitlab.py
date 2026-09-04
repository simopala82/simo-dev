"""pipeline_poll_gitlab.py is executed directly, so it is code and gets tested like
code: we run the real file against a fake `glab`. Python port of the tests for the
GitLab poll body formerly embedded in skills/mr-followup/lib/pipeline-scripts.md
(OSS migration step 7c) -- same env vars, same exit-code contract, same sticky SEEN
flag. Dropped from the bash-era suite: cross-shell parametrization and the injected
virtual clock (both moot with a single Python interpreter -- approved design,
Decision 3), and the unwritable-stderr-scratch-file test (the scratch file itself no
longer exists -- subprocess.run captures stderr in-memory).

What each case pins down (see the design spec's exit-code contract):
  0 terminal status | 1 refused to start | 3 no pipeline exists |
  4 timed out with a pipeline still transitional |
  5 the read that hit the ceiling failed, so the state is unverified
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
SCRIPT = SIMO / "skills" / "mr-followup" / "lib" / "pipeline_poll_gitlab.py"
BRANCH = "fix/some-branch"

ENV_FAST = {"POLL_INTERVAL": "1", "POLL_MAX_WAIT": "3", "POLL_NOPIPE_MAX_WAIT": "2"}
TIMEOUT = 30


def _run(tmp_path, mode, worktree=None, branch=BRANCH, env=None, timeout=TIMEOUT, counter=None):
    """Run the real script against a fake `glab` first on PATH. The outer process cwd
    is deliberately NOT <worktree-abs>: the fake refuses to answer unless its own cwd
    is <worktree-abs>, which is what makes the script's internal `cwd=` passthrough
    (replacing the bash body's `cd`) observable -- forgetting it leaves every case
    green."""
    worktree = tmp_path if worktree is None else worktree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)

    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "glab"
    shim.write_text("#!/bin/sh\nexec {} {} \"$@\"\n".format(sys.executable, FIXTURES / "fake_glab.py"))
    shim.chmod(0o755)

    run_env = dict(os.environ)
    run_env.update(ENV_FAST)
    run_env.update(env or {})
    run_env["PATH"] = "{}{}{}".format(bindir, os.pathsep, run_env["PATH"])
    run_env["FAKE_GLAB_MODE"] = mode
    run_env["FAKE_GLAB_BRANCH"] = BRANCH
    run_env["FAKE_GLAB_EXPECT_CWD"] = str(worktree)
    if counter is not None:
        run_env["FAKE_GLAB_COUNTER"] = str(counter)

    argv = [sys.executable, str(SCRIPT), str(worktree), branch]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=run_env, cwd=str(elsewhere))


@pytest.mark.parametrize("mode", ["success", "failed", "canceled"])
def test_terminal_status_exits_zero(tmp_path, mode):
    r = _run(tmp_path, mode)
    assert r.returncode == 0, r.stdout + r.stderr
    assert mode in r.stdout


def test_running_forever_times_out_with_rc4(tmp_path):
    r = _run(tmp_path, "running")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "TIMED OUT" in r.stdout


@pytest.mark.parametrize("status", ["running", "pending", "created", "preparing",
                                    "waiting_for_resource", "scheduled", "canceling"])
def test_every_transitional_state_keeps_polling(tmp_path, status):
    r = _run(tmp_path, status, env={"POLL_MAX_WAIT": "1"})
    assert r.returncode == 4, r.stdout + r.stderr
    assert "Pipeline done" not in r.stdout


def test_seen_stays_sticky_when_the_pipeline_leaves_the_list(tmp_path):
    """SEEN must not un-set. A pipeline listed once and then absent from a
    successful read is not "no pipeline exists": the branch had one."""
    counter = tmp_path / "calls"
    r = _run(tmp_path, "running_then_empty", counter=counter)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "pipeline_seen=1" in r.stdout
    assert "NO PIPELINE" not in r.stdout


def test_forge_dying_while_a_pipeline_runs_exits_rc5_not_rc4(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "running_then_dead", counter=counter)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "TIMED OUT" not in r.stdout


def test_no_pipeline_exits_rc3(tmp_path):
    r = _run(tmp_path, "empty")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO PIPELINE" in r.stdout


def test_unreachable_glab_exits_rc5_not_rc3(tmp_path):
    r = _run(tmp_path, "unreachable")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "NO PIPELINE" not in r.stdout


def test_rc5_carries_the_cause_glab_printed(tmp_path):
    r = _run(tmp_path, "unreachable")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "simulated forge failure" in r.stdout, "rc 5 dropped the cause: " + r.stdout


@pytest.mark.parametrize("interval", ["abc", "0"])
def test_unusable_interval_is_coerced_not_busy_spun(tmp_path, interval):
    """A non-digit or zero POLL_INTERVAL must not turn the loop into a glab hammer
    -- coerced to 30s, at most one call fits in the 4s window below."""
    counter = tmp_path / "calls"
    with pytest.raises(subprocess.TimeoutExpired):
        _run(tmp_path, "running", env={"POLL_INTERVAL": interval, "POLL_MAX_WAIT": "9999"},
             timeout=4, counter=counter)
    assert counter.read_text().count("call") <= 2, "busy-spin: glab was called repeatedly"


@pytest.mark.parametrize("var,mode,other", [
    ("POLL_MAX_WAIT", "running", {}),
    ("POLL_NOPIPE_MAX_WAIT", "empty", {"POLL_MAX_WAIT": "9999"}),
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
    ("POLL_MAX_WAIT", "running", {}),
    ("POLL_NOPIPE_MAX_WAIT", "empty", {"POLL_MAX_WAIT": "9999"}),
    ("POLL_INTERVAL", "running", {"POLL_MAX_WAIT": "9999"}),
])
def test_oversized_override_is_coerced_to_its_default(tmp_path, var, mode, other):
    """All-digits is not enough: an oversized override is coerced to its (large)
    default, so this still times out within the test window -- exactly like an
    UNCOERCED value would (which would error on every comparison instead and also
    never come due). The assertion is on stderr, not the exit code: a clean stderr
    proves the coercion accepted the value silently rather than erroring on it."""
    env = {"POLL_INTERVAL": "1"}
    env.update(other)
    env[var] = "9" * 20
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run(tmp_path, mode, env=env, timeout=4)
    err = (caught.value.stderr or b"").decode()
    assert not err.strip(), "ceiling comparison is erroring, so it can never come due: " + err


def test_matched_line_without_status_is_not_no_pipeline(tmp_path):
    r = _run(tmp_path, "nostatus")
    assert r.returncode == 4, r.stdout + r.stderr


def test_transient_glab_failure_does_not_end_the_poll(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "blip", counter=counter,
             env={"POLL_NOPIPE_MAX_WAIT": "20", "POLL_MAX_WAIT": "20"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "success" in r.stdout


def test_forge_dying_after_first_reply_exits_rc5_not_rc3(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "dying", counter=counter)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "NO PIPELINE" not in r.stdout


def test_empty_branch_literal_refuses_to_poll(tmp_path):
    r = _run(tmp_path, "success", branch="")
    assert r.returncode == 1, r.stdout + r.stderr


def test_unusable_worktree_literal_refuses_to_poll(tmp_path):
    r = _run(tmp_path, "success", worktree=tmp_path / "does-not-exist")
    assert r.returncode == 1, r.stdout + r.stderr


def test_missing_arguments_refuses_to_poll(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=TIMEOUT)
    assert r.returncode == 1, r.stdout + r.stderr


def test_ceilings_count_wall_clock_not_sleeps(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "running", counter=counter, timeout=20,
             env={"POLL_MAX_WAIT": "2", "POLL_INTERVAL": "1", "FAKE_GLAB_DELAY": "2"})
    assert r.returncode == 4, r.stdout + r.stderr
    assert counter.read_text().count("call") == 1, "ceiling is counting sleeps, not elapsed time"


def test_interval_larger_than_the_ceiling_still_terminates_within_the_bound(tmp_path):
    counter = tmp_path / "calls"
    started = time.monotonic()
    r = _run(tmp_path, "running", timeout=20, counter=counter,
             env={"POLL_MAX_WAIT": "2", "POLL_NOPIPE_MAX_WAIT": "2", "POLL_INTERVAL": "3"})
    took = time.monotonic() - started
    assert r.returncode == 4, r.stdout + r.stderr
    calls = counter.read_text().count("call")
    assert calls == 2, "polled {} times; the ceiling is late by an interval".format(calls)
    assert took < 2 + 3 + 2, "verdict took {:.1f}s, past ceiling + interval + slack".format(took)


def test_lowered_max_wait_does_not_outrun_the_no_pipeline_ceiling(tmp_path):
    r = _run(tmp_path, "empty", env={"POLL_MAX_WAIT": "1", "POLL_NOPIPE_MAX_WAIT": "60"})
    assert r.returncode == 3, r.stdout + r.stderr
