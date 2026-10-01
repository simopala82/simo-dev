#!/usr/bin/env python3
"""Fake `glab` for test_pipeline_poll_gitlab.py.

`pipeline list` and `api <endpoint>` are emulated. `pipeline list` behaviour is chosen
by FAKE_GLAB_MODE:

  any status word in STATUS_MODES below — one line for FAKE_GLAB_BRANCH carrying that
             status in parentheses, rc 0. Covers the terminal words (`success`, `failed`,
             `canceled`) and every transitional one the poll's `case` lists, so a test can
             fail if one of them is dropped from that list. The set is explicit, not
             free-form: a typo in FAKE_GLAB_MODE must exit 65, not print a plausible line.
  empty    — an empty pipeline list, rc 0. The real glab 1.110.0 prints
             `No pipelines available on <slug>.` here; what the poll relies on is only
             rc 0 with no row matching the branch, so the exact text is not emulated.
  nostatus — a line matching FAKE_GLAB_BRANCH with NO parenthesised status word, rc 0
  unreachable — rc 1 with empty stdout, every call (bad token, network down)
  blip     — `unreachable` for the first two calls, then behaves as `success`
  dying    — `empty` on the first call, then `unreachable` for every call after it
  running_then_dead — `running` on the first call, then `unreachable` after it
  running_then_empty — `running` on the first call, then an empty list (rc 0) after it

FAKE_GLAB_DELAY, when set to a number of seconds, sleeps that long before answering. The
poll's ceilings are wall clock, so per-call latency must count against them; with a delay
this large a sleep-counted ELAPSED needs several more calls to reach the same ceiling,
which is what makes that property testable by counting calls.

FAKE_GLAB_EXPECT_CWD, when set, makes every call fail like an unreachable forge unless
the process cwd is that directory. The real `glab` resolves its project from the cwd,
so without this the poll body's `cd` has no observable effect and no test could fail on
its removal.

Every invocation appends one line to FAKE_GLAB_COUNTER — `call` for `pipeline list`,
`api` for `api` — so a test can assert how many times the poll actually called out: the
`call` count proves the interval was honoured rather than busy-spun, the `api` count
that the no-CI probe ran exactly once. Ordered modes advance on `call` lines only.

`api` behaviour is chosen by FAKE_GLAB_API_MODE, answering `projects/:id` and
`projects/:id/repository/files/<path>?ref=<branch>` only:

  config_present (default) — the CI config file exists on the branch. Keeps every test
             that predates the probe on the old ceiling path.
  noconfig — Auto DevOps off, no config file: `404 File Not Found`, rc 1 (the shape glab
             1.119.0 prints, measured 2026-09-30).
  autodevops — Auto DevOps on.
  remote_config_path / url_config_path — ci_config_path names another project
             (`…@group/other`) or a URL; any files lookup answers `404 File Not Found`,
             so a caller that probed it as a local file would wrongly fast-path.
  no_config_key — the project response lacks the ci_config_path key entirely.
  no_autodevops_key — the project response lacks the auto_devops_enabled key.
  api_error — every api call fails with `401 Unauthorized`, rc 1.
  commit_missing — the files lookup answers `404 Commit Not Found`: a 404 that is not
             "no file".

The files lookup answers `404 File Not Found` only for the exactly-encoded endpoint
(path = FAKE_GLAB_CI_CONFIG_PATH or `.gitlab-ci.yml`, ref = FAKE_GLAB_BRANCH, both
`quote(safe="")`); any other endpoint gets `404 Commit Not Found`, so wrong encoding
cannot fast-path. FAKE_GLAB_API_LOG, when set, gets one line per api endpoint.
"""
import json
import os
import sys
import time
from urllib.parse import quote

STATUS_MODES = ("success", "failed", "canceled",
                "running", "pending", "created", "preparing",
                "waiting_for_resource", "scheduled", "canceling")

MODE = os.environ.get("FAKE_GLAB_MODE", "empty")
BRANCH = os.environ.get("FAKE_GLAB_BRANCH", "some-branch")
COUNTER = os.environ.get("FAKE_GLAB_COUNTER")
EXPECT_CWD = os.environ.get("FAKE_GLAB_EXPECT_CWD")

argv = sys.argv[1:]
if argv[:2] == ["pipeline", "list"]:
    sub = "call"
elif argv[:1] == ["api"] and len(argv) == 2:
    sub = "api"
else:
    sys.stderr.write("fake_glab: only `pipeline list` and `api <endpoint>` are emulated\n")
    sys.exit(64)

calls = 0
if COUNTER:
    with open(COUNTER, "a") as fh:
        fh.write(sub + "\n")
    with open(COUNTER) as fh:
        calls = sum(1 for line in fh if line.strip() == "call")

DELAY = os.environ.get("FAKE_GLAB_DELAY")
if DELAY:
    time.sleep(float(DELAY))

if EXPECT_CWD and os.path.realpath(os.getcwd()) != os.path.realpath(EXPECT_CWD):
    sys.stderr.write("fake_glab: wrong cwd {!r}, expected {!r}\n".format(os.getcwd(), EXPECT_CWD))
    sys.exit(1)

if sub == "api":
    endpoint = argv[1]
    api_log = os.environ.get("FAKE_GLAB_API_LOG")
    if api_log:
        with open(api_log, "a") as fh:
            fh.write(endpoint + "\n")
    api_mode = os.environ.get("FAKE_GLAB_API_MODE", "config_present")
    local_path = os.environ.get("FAKE_GLAB_CI_CONFIG_PATH")
    if api_mode == "api_error":
        print('{"message":"401 Unauthorized"}')
        sys.stderr.write("glab: 401 Unauthorized (HTTP 401)\n")
        sys.exit(1)
    if endpoint == "projects/:id":
        ci_path = {"remote_config_path": ".gitlab-ci.yml@group/other",
                   "url_config_path": "https://example.com/ci.yml"}.get(api_mode, local_path)
        project = {"id": 1, "ci_config_path": ci_path, "auto_devops_enabled": api_mode == "autodevops"}
        if api_mode == "no_config_key":
            del project["ci_config_path"]
        if api_mode == "no_autodevops_key":
            del project["auto_devops_enabled"]
        print(json.dumps(project))
        sys.exit(0)
    expected = "projects/:id/repository/files/{}?ref={}".format(
        quote(local_path or ".gitlab-ci.yml", safe=""), quote(BRANCH, safe=""))
    if endpoint.startswith("projects/:id/repository/files/"):
        if api_mode in ("remote_config_path", "url_config_path"):
            # Would fast-path if the caller wrongly probed a remote path as a local file.
            print('{"message":"404 File Not Found"}')
            sys.exit(1)
        if endpoint == expected and api_mode == "config_present":
            print(json.dumps({"file_path": local_path or ".gitlab-ci.yml"}))
            sys.exit(0)
        if endpoint == expected and api_mode != "commit_missing":
            print('{"message":"404 File Not Found"}')
            sys.stderr.write("glab: 404 File Not Found (HTTP 404)\n")
            sys.exit(1)
        # Wrong encoding/path/ref, or commit_missing: a 404 that is NOT "no file".
        print('{"message":"404 Commit Not Found"}')
        sys.stderr.write("glab: 404 Commit Not Found (HTTP 404)\n")
        sys.exit(1)
    sys.stderr.write("fake_glab: unexpected api endpoint {!r}\n".format(endpoint))
    sys.exit(64)

mode = MODE
if mode in ("blip", "dying", "running_then_dead", "running_then_empty") and not COUNTER:
    # These are call-ordered; without the counter they would silently degenerate into a
    # single constant mode and the test asserting the transition could never fail.
    sys.stderr.write("fake_glab: mode {!r} needs FAKE_GLAB_COUNTER\n".format(MODE))
    sys.exit(66)
if mode == "blip":
    mode = "success" if calls > 2 else "unreachable"
elif mode == "dying":
    mode = "empty" if calls <= 1 else "unreachable"
elif mode == "running_then_dead":
    mode = "running" if calls <= 1 else "unreachable"
elif mode == "running_then_empty":
    mode = "running" if calls <= 1 else "empty"

if mode == "unreachable":
    sys.stderr.write("fake_glab: simulated forge failure\n")
    sys.exit(1)
if mode == "empty":
    print("[]")
    sys.exit(0)
if mode == "nostatus":
    print("• #123 (#45) {} (1 minute ago)".format(BRANCH))
    sys.exit(0)
if mode in STATUS_MODES:
    print("({}) • #123 (#45) {} (1 minute ago)".format(mode, BRANCH))
    sys.exit(0)

sys.stderr.write("fake_glab: unknown FAKE_GLAB_MODE {!r}\n".format(MODE))
sys.exit(65)
