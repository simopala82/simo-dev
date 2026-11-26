"""pipeline_poll_gitlab.py is executed directly, so it is code and gets tested like
code: we run the real file against a fake `glab`. Python port of the tests for the
GitLab poll body formerly embedded in skills/mr-followup/lib/pipeline-scripts.md
(OSS migration step 7c) -- same env vars, same exit-code contract, same sticky SEEN
flag. Dropped from the bash-era suite: cross-shell parametrization and, suite-wide,
the injected virtual clock (both moot with a single Python interpreter -- approved
design, Decision 3), and the unwritable-stderr-scratch-file test (the scratch file
itself no longer exists -- subprocess.run captures stderr in-memory). One test brings
a virtual clock back, in-process: a bound that must hold to the second cannot be
measured on the wall clock of a loaded machine.

What each case pins down (see the design spec's exit-code contract):
  0 terminal status | 1 refused to start |
  3 no pipeline exists (after the ceiling, or at once as NO CI CONFIG) |
  4 timed out with a pipeline still transitional |
  5 the read that hit the ceiling failed, so the state is unverified
"""
import importlib.util
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
SIMO = TESTS.parents[1]                        # lib/tests -> lib -> simo-dev
SCRIPT = SIMO / "skills" / "mr-followup" / "lib" / "pipeline_poll_gitlab.py"
BRANCH = "fix/some-branch"

ENV_FAST = {"POLL_INTERVAL": "1", "POLL_MAX_WAIT": "3", "POLL_NOPIPE_MAX_WAIT": "2"}
TIMEOUT = 30


def _init_repo(path):
    """A real one-commit repo, so the script's `git rev-parse HEAD` has an answer."""
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def _fake_glab_env(tmp_path, mode, worktree, env, counter, init_git):
    """The environment that puts the fake `glab` first on PATH and configures it."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "glab"
    shim.write_text("#!/bin/sh\nexec {} {} \"$@\"\n".format(sys.executable, FIXTURES / "fake_glab.py"))
    shim.chmod(0o755)

    run_env = dict(os.environ)
    for var in ("GIT_DIR", "GIT_WORK_TREE"):
        run_env.pop(var, None)
    run_env.update(ENV_FAST)
    run_env.update(env or {})
    run_env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    if init_git:
        run_env["FAKE_GLAB_HEAD_SHA"] = _init_repo(worktree)
    run_env["PATH"] = "{}{}{}".format(bindir, os.pathsep, run_env["PATH"])
    run_env["FAKE_GLAB_MODE"] = mode
    run_env["FAKE_GLAB_BRANCH"] = BRANCH
    run_env["FAKE_GLAB_EXPECT_CWD"] = str(worktree)
    if counter is not None:
        run_env["FAKE_GLAB_COUNTER"] = str(counter)
    return run_env


def _run(tmp_path, mode, worktree=None, branch=BRANCH, env=None, timeout=TIMEOUT, counter=None,
         init_git=True):
    """Run the real script against a fake `glab` first on PATH. The outer process cwd
    is deliberately NOT <worktree-abs>: the fake refuses to answer unless its own cwd
    is <worktree-abs>, which is what makes the script's internal `cwd=` passthrough
    (replacing the bash body's `cd`) observable -- forgetting it leaves every case
    green. With init_git the worktree becomes a real repo and the fake answers
    `--sha` only for its HEAD, so a poll that filters on any other SHA sees nothing."""
    worktree = tmp_path if worktree is None else worktree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    run_env = _fake_glab_env(tmp_path, mode, worktree, env, counter, init_git)

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


NOCI_SLOW = {"POLL_NOPIPE_MAX_WAIT": "60", "POLL_MAX_WAIT": "60"}


def test_no_ci_config_exits_rc3_on_the_first_read(tmp_path):
    """The old path would wait the 60s ceiling and blow the 10s timeout."""
    counter = tmp_path / "calls"
    r = _run(tmp_path, "empty", counter=counter, timeout=10,
             env=dict(NOCI_SLOW, FAKE_GLAB_API_MODE="noconfig"))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO CI CONFIG" in r.stdout
    assert "NO PIPELINE for" not in r.stdout
    assert counter.read_text().splitlines().count("call") == 1


def test_custom_local_ci_config_path_and_branch_are_url_encoded(tmp_path):
    """The fake only answers `404 File Not Found` for the exactly-encoded endpoint;
    anything else is `404 Commit Not Found`, which must not fast-path."""
    api_log = tmp_path / "api.log"
    r = _run(tmp_path, "empty", timeout=10,
             env=dict(NOCI_SLOW, FAKE_GLAB_API_MODE="noconfig",
                      FAKE_GLAB_CI_CONFIG_PATH="ci/main.yml", FAKE_GLAB_API_LOG=str(api_log)))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO CI CONFIG" in r.stdout
    assert "projects/:id/repository/files/ci%2Fmain.yml?ref=fix%2Fsome-branch" in api_log.read_text()


def test_empty_string_ci_config_path_falls_back_to_the_default_file(tmp_path):
    api_log = tmp_path / "api.log"
    r = _run(tmp_path, "empty", timeout=10,
             env=dict(NOCI_SLOW, FAKE_GLAB_API_MODE="noconfig",
                      FAKE_GLAB_CI_CONFIG_PATH="", FAKE_GLAB_API_LOG=str(api_log)))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO CI CONFIG" in r.stdout
    assert "repository/files/.gitlab-ci.yml?ref=" in api_log.read_text()


@pytest.mark.parametrize("api_mode,api_calls", [
    ("config_present", 2), ("commit_missing", 2), ("autodevops", 1), ("remote_config_path", 1),
    ("url_config_path", 1), ("no_config_key", 1), ("no_autodevops_key", 1), ("api_error", 1)])
def test_ambiguous_probe_keeps_the_ceiling_and_probes_once(tmp_path, api_mode, api_calls):
    counter = tmp_path / "calls"
    api_log = tmp_path / "api.log"
    started = time.monotonic()
    r = _run(tmp_path, "empty", counter=counter,
             env={"FAKE_GLAB_API_MODE": api_mode, "FAKE_GLAB_API_LOG": str(api_log)})
    took = time.monotonic() - started
    if api_mode in ("remote_config_path", "url_config_path"):
        probed = api_log.read_text() if api_log.exists() else ""
        assert "repository/files/" not in probed, "remote config path probed as a local file"
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO PIPELINE for" in r.stdout
    assert "NO CI CONFIG" not in r.stdout
    ceiling = int(ENV_FAST["POLL_NOPIPE_MAX_WAIT"])
    after = re.search(r"NO PIPELINE for .* after (\d+)s", r.stdout)
    assert after and int(after.group(1)) >= ceiling, "reported a verdict before the ceiling"
    assert took >= ceiling, "returned after {:.1f}s, before the ceiling".format(took)
    lines = counter.read_text().splitlines()
    assert lines.count("api") == api_calls, "probe must run exactly once, lazily"


def test_visible_pipeline_is_never_probed(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "running", counter=counter, env={"FAKE_GLAB_API_MODE": "noconfig"})
    assert r.returncode == 4, r.stdout + r.stderr
    assert "api" not in counter.read_text().splitlines()


def test_seen_pipeline_leaving_the_list_does_not_fast_path(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "running_then_empty", counter=counter, env={"FAKE_GLAB_API_MODE": "noconfig"})
    assert r.returncode == 4, r.stdout + r.stderr
    assert "NO CI CONFIG" not in r.stdout


def test_unreachable_glab_never_reaches_the_probe(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "unreachable", counter=counter, env={"FAKE_GLAB_API_MODE": "noconfig"})
    assert r.returncode == 5, r.stdout + r.stderr
    assert "api" not in counter.read_text().splitlines()


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
    r = _run(tmp_path, "success", worktree=tmp_path / "does-not-exist", init_git=False)
    assert r.returncode == 1, r.stdout + r.stderr


def test_older_commits_concluded_pipeline_is_not_reported(tmp_path):
    """The 2026-10-12 case: right after a push the newest pipeline on the branch
    belongs to the previous commit and is already `success`. Pre-fix this was rc 0."""
    r = _run(tmp_path, "success", env={"FAKE_GLAB_STALE_CALLS": "all"})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO PIPELINE" in r.stdout
    assert "Pipeline done" not in r.stdout


def test_head_pipeline_appearing_later_is_the_one_reported(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "success", counter=counter,
             env={"FAKE_GLAB_STALE_CALLS": "1", "POLL_NOPIPE_MAX_WAIT": "20", "POLL_MAX_WAIT": "20"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert counter.read_text().splitlines().count("call") == 2, "reported before HEAD's pipeline appeared"


def test_empty_worktree_literal_refuses_to_poll(tmp_path):
    r = _run(tmp_path, "success", worktree="", init_git=False)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "empty <worktree-abs>" in r.stderr, "rc 1 came from another path, not the guard"


def test_worktree_that_is_not_a_git_repo_refuses_to_poll(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _run(tmp_path, "success", worktree=plain, init_git=False)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "HEAD" in r.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores mode 000")
def test_unusable_worktree_mid_poll_ends_with_rc1_not_a_traceback(tmp_path):
    """Mode 000 on the worktree after the first read: the next `pipeline list` chdir
    raises PermissionError, which a FileNotFoundError-only handler lets through."""
    wt = tmp_path / "wt"
    wt.mkdir()
    try:
        r = _run(tmp_path, "running", worktree=wt,
                 env={"FAKE_GLAB_CHMOD000_ON_CALL": str(wt), "POLL_MAX_WAIT": "20"})
    finally:
        os.chmod(wt, 0o755)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cannot reach worktree" in r.stderr
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
    r = subprocess.run([sys.executable, str(SCRIPT)] + [str(wt), BRANCH], capture_output=True,
                       text=True, timeout=TIMEOUT, env=env, cwd=str(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cannot resolve HEAD" in r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_missing_arguments_refuses_to_poll(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=TIMEOUT)
    assert r.returncode == 1, r.stdout + r.stderr


def test_ceilings_count_wall_clock_not_sleeps(tmp_path):
    counter = tmp_path / "calls"
    r = _run(tmp_path, "running", counter=counter, timeout=20,
             env={"POLL_MAX_WAIT": "2", "POLL_INTERVAL": "1", "FAKE_GLAB_DELAY": "2"})
    assert r.returncode == 4, r.stdout + r.stderr
    assert counter.read_text().count("call") == 1, "ceiling is counting sleeps, not elapsed time"


@pytest.mark.parametrize("latency,expected_calls", [(0.0, 2), (1.5, 2), (2.5, 1)])
def test_interval_larger_than_the_ceiling_still_terminates_within_the_bound(tmp_path, monkeypatch, latency,
                                                                            expected_calls):
    """Runs main() in-process on a virtual clock that moves only by the poll's sleeps
    plus `latency` per `glab` call. On the real clock this flaked under load: a first
    `glab` call slow enough to cover the 2s ceiling on its own ends the poll after one
    call (correct behaviour, the 2.5 case, which failed the old `calls == 2`), and git
    init plus two Python startups could eat the 2s slack of the wall-clock bound. The
    1.5 case pins that a slow call under the ceiling still leaves room for the second."""
    counter = tmp_path / "calls"
    max_wait, interval = 2, 3
    run_env = _fake_glab_env(tmp_path, "running", tmp_path, counter=counter, init_git=True,
                             env={"POLL_MAX_WAIT": str(max_wait), "POLL_NOPIPE_MAX_WAIT": str(max_wait),
                                  "POLL_INTERVAL": str(interval)})
    for var in ("GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    for key, value in run_env.items():
        monkeypatch.setenv(key, value)

    spec = importlib.util.spec_from_file_location("pipeline_poll_gitlab", SCRIPT)
    poll = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(poll)
    clock = [0.0]

    def run_with_latency(argv, **kwargs):
        result = subprocess.run(argv, **kwargs)
        if argv[0] == "glab":
            clock[0] += latency
        return result

    def advance(seconds):
        clock[0] += seconds

    # Patches module attributes: relies on the script's `import time` / `import subprocess`.
    monkeypatch.setattr(poll, "subprocess", types.SimpleNamespace(run=run_with_latency))
    monkeypatch.setattr(poll, "time", types.SimpleNamespace(monotonic=lambda: clock[0], sleep=advance,
                                                            strftime=time.strftime))

    assert poll.main([str(tmp_path), BRANCH]) == 4
    calls = counter.read_text().count("call")
    assert calls == expected_calls, "polled {} times, expected {}".format(calls, expected_calls)
    bound = max_wait + interval + latency
    assert clock[0] <= bound, "verdict took {:.1f}s, past ceiling + interval + one call's latency".format(clock[0])


def test_lowered_max_wait_does_not_outrun_the_no_pipeline_ceiling(tmp_path):
    r = _run(tmp_path, "empty", env={"POLL_MAX_WAIT": "1", "POLL_NOPIPE_MAX_WAIT": "60"})
    assert r.returncode == 3, r.stdout + r.stderr
