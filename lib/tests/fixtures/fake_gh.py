#!/usr/bin/env python3
"""Fake `gh` for test_pipeline_poll_github.py.

Only `pr checks <n> --json …` and `pr view <n> --json number` are emulated — the
two calls the poll makes. Behaviour is chosen by FAKE_GH_MODE:

  pass | fail | pending | skipping | cancel — one check carrying that `bucket`, rc 0
             with a JSON array on stdout. `pending` is what never concludes; `fail`
             exists to pin that a red build is still rc 0 from the poll.
  nochecks — what real gh does for a PR with no checks: rc 1, EMPTY stdout, and the
             `no checks reported on the '<branch>' branch` line on stderr. The
             liveness probe still SUCCEEDS in this mode — the forge is fine, the PR
             simply has no checks. That asymmetry is the whole point of the fixture.
  unreachable — rc 1 with empty stdout for BOTH subcommands (bad token, network down).
  garbage  — rc 0 with non-JSON on stdout. A read that cannot be parsed is not a read.
  blip     — `unreachable` for the first two checks calls, then `pass`
  dying    — `nochecks` on the first checks call, then `unreachable`
  pending_then_dead — `pending` on the first checks call, then `unreachable`
  pending_then_nochecks — `pending` on the first checks call, then `nochecks`

FAKE_GH_EXPECT_PR, when set, makes every call fail like an unreachable forge unless
the PR number in argv matches. The poll takes the number as its only argument and
passes it to both calls; without this nothing would fail if it stopped doing so.
There is no cwd equivalent here on purpose: unlike the GitLab body, this script does
not `cd` anywhere — that gap is a named residual in `lib/pipeline-scripts.md`.

FAKE_GH_DELAY, when set to a number of seconds, sleeps that long before answering.
The poll's ceilings are wall clock, so per-call latency must count against them; with
a delay this large a sleep-counted ELAPSED needs several more calls to reach the same
ceiling, which is what makes that property testable by counting calls.

FAKE_GH_COUNTER, when set, gets one line per invocation — `checks` or `view`. Tests
assert on both: the `checks` count proves the interval was honoured rather than
busy-spun, and the `view` count proves the probe runs only when a ceiling is due.
Ordered modes advance on `checks` lines only, so a probe never skips a step.
"""
import os
import sys
import time

BUCKET_MODES = ("pass", "fail", "pending", "skipping", "cancel")
STATES = {"pass": "SUCCESS", "fail": "FAILURE", "pending": "IN_PROGRESS",
          "skipping": "SKIPPED", "cancel": "CANCELLED"}
ORDERED = ("blip", "dying", "pending_then_dead", "pending_then_nochecks")

MODE = os.environ.get("FAKE_GH_MODE", "nochecks")
PR = os.environ.get("FAKE_GH_PR", "42")
EXPECT_PR = os.environ.get("FAKE_GH_EXPECT_PR")
COUNTER = os.environ.get("FAKE_GH_COUNTER")

argv = sys.argv[1:]
if argv[:2] == ["pr", "checks"]:
    sub = "checks"
elif argv[:2] == ["pr", "view"]:
    sub = "view"
else:
    sys.stderr.write("fake_gh: only `pr checks` and `pr view` are emulated\n")
    sys.exit(64)

checks_calls = 0
if COUNTER:
    with open(COUNTER, "a") as fh:
        fh.write(sub + "\n")
    with open(COUNTER) as fh:
        checks_calls = sum(1 for line in fh if line.strip() == "checks")

DELAY = os.environ.get("FAKE_GH_DELAY")
if DELAY:
    time.sleep(float(DELAY))

if EXPECT_PR is not None and (len(argv) < 3 or argv[2] != EXPECT_PR):
    sys.stderr.write("fake_gh: wrong PR number {!r}, expected {!r}\n".format(argv[2:3], EXPECT_PR))
    sys.exit(1)

mode = MODE
if mode in ORDERED and not COUNTER:
    # These are call-ordered; without the counter they would silently degenerate into a
    # single constant mode and the test asserting the transition could never fail.
    sys.stderr.write("fake_gh: mode {!r} needs FAKE_GH_COUNTER\n".format(MODE))
    sys.exit(66)
if mode == "blip":
    mode = "pass" if checks_calls > 2 else "unreachable"
elif mode == "dying":
    mode = "nochecks" if checks_calls <= 1 else "unreachable"
elif mode == "pending_then_dead":
    mode = "pending" if checks_calls <= 1 else "unreachable"
elif mode == "pending_then_nochecks":
    mode = "pending" if checks_calls <= 1 else "nochecks"

if mode == "unreachable":
    # Both subcommands fail: the forge itself is unreachable.
    sys.stderr.write("fake_gh: simulated forge failure\n")
    sys.exit(1)

if sub == "view":
    # Reached only when the mode is not `unreachable`, i.e. the forge answers. That is
    # true for `nochecks` too, which is exactly what lets the poll say rc 3 there.
    print('{{"number":{}}}'.format(PR))
    sys.exit(0)

if mode == "nochecks":
    sys.stderr.write("no checks reported on the 'fake-branch' branch\n")
    sys.exit(1)
if mode == "garbage":
    print("not json at all")
    sys.exit(0)
if mode in BUCKET_MODES:
    print('[{{"bucket":"{}","name":"build","state":"{}"}}]'.format(mode, STATES[mode]))
    sys.exit(0)

sys.stderr.write("fake_gh: unknown FAKE_GH_MODE {!r}\n".format(MODE))
sys.exit(65)
