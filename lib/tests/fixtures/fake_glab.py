#!/usr/bin/env python3
"""Fake `glab` for test_pipeline_poll_gitlab.py.

Only `pipeline list` is emulated. Behaviour is chosen by FAKE_GLAB_MODE:

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

Every invocation appends one line to FAKE_GLAB_COUNTER, so a test can assert how
many times the poll actually called out — that is what proves the interval was
honoured rather than busy-spun.
"""
import os
import sys
import time

STATUS_MODES = ("success", "failed", "canceled",
                "running", "pending", "created", "preparing",
                "waiting_for_resource", "scheduled", "canceling")

MODE = os.environ.get("FAKE_GLAB_MODE", "empty")
BRANCH = os.environ.get("FAKE_GLAB_BRANCH", "some-branch")
COUNTER = os.environ.get("FAKE_GLAB_COUNTER")
EXPECT_CWD = os.environ.get("FAKE_GLAB_EXPECT_CWD")

calls = 0
if COUNTER:
    with open(COUNTER, "a") as fh:
        fh.write("call\n")
    with open(COUNTER) as fh:
        calls = sum(1 for _ in fh)

DELAY = os.environ.get("FAKE_GLAB_DELAY")
if DELAY:
    time.sleep(float(DELAY))

if sys.argv[1:3] != ["pipeline", "list"]:
    sys.stderr.write("fake_glab: only `pipeline list` is emulated\n")
    sys.exit(64)

if EXPECT_CWD and os.path.realpath(os.getcwd()) != os.path.realpath(EXPECT_CWD):
    sys.stderr.write("fake_glab: wrong cwd {!r}, expected {!r}\n".format(os.getcwd(), EXPECT_CWD))
    sys.exit(1)

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
