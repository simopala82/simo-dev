#!/usr/bin/env python3
"""Fake `gh` for test_pipeline_poll_github.py.

Only `pr checks <n> --json …`, `pr view <n> --json headRefOid` and `pr view <n> --json
number` are emulated — the three calls the poll makes. Behaviour is chosen by FAKE_GH_MODE:

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
the PR number in argv matches. The poll takes the number as its first argument and
passes it to every gh call (its second argument, <worktree-abs>, only reaches `git`);
without this nothing would fail if it stopped doing so.
FAKE_GH_EXPECT_CWD, when set, makes every call fail like an unreachable forge unless
the process cwd is that directory. The real `gh` resolves the repo, and so the PR the
number names, from the cwd's git remotes; without this the poll's `cwd=<worktree-abs>`
would have no observable effect.

FAKE_GH_DELAY, when set to a number of seconds, sleeps that long before answering.
The poll's ceilings are wall clock, so per-call latency must count against them; with
a delay this large a sleep-counted ELAPSED needs several more calls to reach the same
ceiling, which is what makes that property testable by counting calls.

`pr view --json headRefOid` answers FAKE_GH_HEAD_SHA (unset → exit 67), or the older
SHA `"0"*40` while the push has not reached the PR: FAKE_GH_STALE_HEAD_CALLS=`all` for
every head read, or an integer N (needs FAKE_GH_COUNTER) for the first N. It fails like a
dead forge only for the constant mode `unreachable`, or when FAKE_GH_HEAD_FAIL is set —
then only the head read fails, the liveness probe still answers. The ordered modes model
the checks endpoint flapping and leave the head read alone: a failing head read would
stop `checks` from ever being called, and `blip` could never advance.
FAKE_GH_RMTREE_ON_HEAD=<dir> deletes that directory on the first head read, then
answers normally: the worktree vanishing mid-poll. FAKE_GH_CHMOD000_ON_HEAD=<dir> instead
makes it unreadable (mode 000) on every head read — in practice only the first, since
the next gh call fails before the fake runs: the next chdir into it raises PermissionError, not
FileNotFoundError.
FAKE_GH_HEAD_MOVED_AFTER=N (needs FAKE_GH_COUNTER) answers a newer SHA `"e"*40` from the
(N+1)th head read on: another push landed after HEAD's checks were seen.

FAKE_GH_COUNTER, when set, gets one line per invocation — `checks`, `head` or `view`. Tests
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
EXPECT_CWD = os.environ.get("FAKE_GH_EXPECT_CWD")
COUNTER = os.environ.get("FAKE_GH_COUNTER")

argv = sys.argv[1:]
if argv[:2] == ["pr", "checks"]:
    sub = "checks"
elif argv[:2] == ["pr", "view"] and argv[3:5] == ["--json", "headRefOid"]:
    sub = "head"
elif argv[:2] == ["pr", "view"]:
    sub = "view"
else:
    sys.stderr.write("fake_gh: only `pr checks` and `pr view` are emulated\n")
    sys.exit(64)

checks_calls = head_calls = 0
if COUNTER:
    with open(COUNTER, "a") as fh:
        fh.write(sub + "\n")
    with open(COUNTER) as fh:
        tokens = [line.strip() for line in fh]
    checks_calls = tokens.count("checks")
    head_calls = tokens.count("head")

DELAY = os.environ.get("FAKE_GH_DELAY")
if DELAY:
    time.sleep(float(DELAY))

if EXPECT_CWD and os.path.realpath(os.getcwd()) != os.path.realpath(EXPECT_CWD):
    sys.stderr.write("fake_gh: wrong cwd {!r}, expected {!r}\n".format(os.getcwd(), EXPECT_CWD))
    sys.exit(1)

if EXPECT_PR is not None and (len(argv) < 3 or argv[2] != EXPECT_PR):
    sys.stderr.write("fake_gh: wrong PR number {!r}, expected {!r}\n".format(argv[2:3], EXPECT_PR))
    sys.exit(1)

if sub == "head":
    if MODE == "unreachable":
        sys.stderr.write("fake_gh: simulated forge failure\n")
        sys.exit(1)
    if os.environ.get("FAKE_GH_HEAD_FAIL"):
        sys.stderr.write("fake_gh: head read failed\n")
        sys.exit(1)
    lock = os.environ.get("FAKE_GH_CHMOD000_ON_HEAD")
    if lock:
        os.chmod(lock, 0)
    rmtree = os.environ.get("FAKE_GH_RMTREE_ON_HEAD")
    if rmtree and os.path.isdir(rmtree):
        import shutil
        shutil.rmtree(rmtree)
    head = os.environ.get("FAKE_GH_HEAD_SHA")
    if not head:
        sys.stderr.write("fake_gh: FAKE_GH_HEAD_SHA is unset\n")
        sys.exit(67)
    stale = os.environ.get("FAKE_GH_STALE_HEAD_CALLS", "0")
    if stale != "all" and stale != "0" and not COUNTER:
        sys.stderr.write("fake_gh: FAKE_GH_STALE_HEAD_CALLS needs FAKE_GH_COUNTER\n")
        sys.exit(66)
    oid = "0" * 40 if stale == "all" or (stale != "0" and head_calls <= int(stale)) else head
    moved = os.environ.get("FAKE_GH_HEAD_MOVED_AFTER")
    if moved:
        if not COUNTER:
            sys.stderr.write("fake_gh: FAKE_GH_HEAD_MOVED_AFTER needs FAKE_GH_COUNTER\n")
            sys.exit(66)
        if head_calls > int(moved):
            oid = "e" * 40
    print('{{"headRefOid":"{}"}}'.format(oid))
    sys.exit(0)

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
