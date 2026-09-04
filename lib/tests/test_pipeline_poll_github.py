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


def _run(tmp_path, mode, pr=PR, env=None, timeout=TIMEOUT, counter=None, args=None,
         expect_pr=PR):
    """Run the real script with the fake `gh` first on PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "gh"
    shim.write_text("#!/bin/sh\nexec {} {} \"$@\"\n".format(sys.executable, FIXTURES / "fake_gh.py"))
    shim.chmod(0o755)

    run_env = dict(os.environ)
    run_env.update(ENV_FAST)
    run_env.update(env or {})
    run_env["PATH"] = "{}{}{}".format(bindir, os.pathsep, run_env["PATH"])
    run_env["FAKE_GH_MODE"] = mode
    run_env["FAKE_GH_PR"] = PR
    if expect_pr is not None:
        run_env["FAKE_GH_EXPECT_PR"] = expect_pr
    if counter is not None:
        run_env["FAKE_GH_COUNTER"] = str(counter)

    argv = [sys.executable, str(SCRIPT)] + (args if args is not None else [pr])
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=run_env, cwd=str(tmp_path))


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
