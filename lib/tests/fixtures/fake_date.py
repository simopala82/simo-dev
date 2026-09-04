#!/usr/bin/env python3
"""Fake `date` for test_pipeline_poll_gitlab.py — a clock indexed on poll iterations.

The poll body resolves `date` through PATH (`date +%s` for START and for ELAPSED, `date
'+%H:%M:%S'` for the per-poll log line), so it can be shimmed at the same seam the suite
already uses for `glab`.

Why it exists: the block samples ELAPSED *after* the read and tests the ceilings *before*
the sleep, so on a loaded host one slow `glab` call pushes ELAPSED past a small ceiling on
the FIRST iteration. The ceiling then comes due on a *successful* read instead of a failed
one and the verdict changes arm — measured 2026-08-22 by delaying the fake instead of
loading the host: `dying` returned rc 3 where the contract says 5, `running_then_dead`
returned rc 4 where it says 5. A test whose subject is *which read* the ceiling lands on
needs a clock the host cannot move.

The model — virtual elapsed advances by exactly one POLL_INTERVAL per completed poll, so it
reproduces the timeline of an unloaded run and nothing else:

    virtual_seconds = max(0, glab_calls - 1) * POLL_INTERVAL

`glab_calls` is the line count of FAKE_GLAB_COUNTER. The `- 1` is what puts START and the
first poll at the same instant: START runs before any glab call (0 calls) and the first
poll's `date` runs after one (1 call), so ELAPSED is 0 on the first poll, POLL_INTERVAL on
the second, and so on.

This shim READS the counter and never writes it, so both `date` calls inside one iteration
return the same instant and their order does not matter.

POLL_INTERVAL is read rather than passed as a step: the two have to agree or the model is a
lie. The shim does not replicate the block's own coercion, so it refuses anything the block
would not use verbatim.

Deliberately NOT the wall clock. The block's ceilings really are wall clock and that
property has its own test — `test_ceilings_count_wall_clock_not_sleeps` — which stays on the
real clock: an injected clock cannot see a slow `glab` eating the ceiling, which is exactly
what that test asserts.

If a regression stops calling glab, the virtual clock never advances, no ceiling comes due,
and the test hits its subprocess timeout. That is a visible red, not a false green.
"""
import os
import sys
import time

EPOCH = 1600000000  # fixed, so the logged timestamps are stable across runs

COUNTER = os.environ.get("FAKE_GLAB_COUNTER")
if not COUNTER:
    sys.stderr.write("fake_date: needs FAKE_GLAB_COUNTER — the clock is indexed on glab calls\n")
    sys.exit(66)

INTERVAL = os.environ.get("POLL_INTERVAL", "")
if not INTERVAL.isdigit() or int(INTERVAL) < 1:
    sys.stderr.write("fake_date: POLL_INTERVAL must be a positive integer, got {!r}\n".format(INTERVAL))
    sys.exit(67)

calls = 0
try:
    with open(COUNTER) as fh:
        calls = sum(1 for _ in fh)
except FileNotFoundError:
    pass  # START is read before the first glab call, so the counter need not exist yet

virtual = EPOCH + max(0, calls - 1) * int(INTERVAL)

fmt = sys.argv[1] if len(sys.argv) > 1 else ""
if not fmt.startswith("+"):
    sys.stderr.write("fake_date: only `date +FORMAT` is emulated, got {!r}\n".format(sys.argv[1:]))
    sys.exit(64)
if fmt == "+%s":
    # `%s` is not portable through strftime, and it is the one the ceilings read.
    sys.stdout.write("{}\n".format(virtual))
else:
    sys.stdout.write(time.strftime(fmt[1:], time.localtime(virtual)) + "\n")
