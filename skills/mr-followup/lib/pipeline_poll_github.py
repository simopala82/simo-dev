#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Poll GitHub PR checks until every check has concluded, or one of two ceilings
comes due. Python port of pipeline_poll_github.sh (OSS migration step 7c) -- same
env vars, same exit-code contract, identical semantics to the GitLab twin
(pipeline_poll_gitlab.py).

Usage: pipeline_poll_github.py <pr_number>
Exit codes (the verdict):
  0  every check concluded -- a FAILING check is still 0: the poll reports, it
     does not judge
  1  refused to start (no <pr_number> argument)
  3  the PR has no checks at all, and a reachable forge said so
  4  a ceiling came due with no conclusion -- checks still pending, or checks
     that vanished
  5  the read that hit the ceiling could not be verified, so the check state is
     UNVERIFIED

Notes on `gh pr checks` (unchanged from the bash predecessor): the `conclusion`
JSON field is not available on all gh builds; `bucket` is gh's always-present
normalized category, `state` is the fallback. The exit code of `gh pr checks` is
IGNORED -- gh returns non-zero for failing/pending/absent checks alike, so a
liveness probe (`gh pr view --json number`) separates "no checks" from "gh could
not answer", run only when a ceiling is due and the checks read produced no
parseable JSON. Deliberately NOT cwd-scoped (a residual asymmetry from the bash
era, carried over as-is): `gh` resolves its repo from the PR number, not the cwd.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

RUNNING = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING"}
FAILED_STATES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def _coerce_int(value: str, default: int) -> int:
    """`isascii()` matters: `str.isdigit()` alone also accepts non-ASCII digit
    characters (e.g. the superscript '²') that `int()` cannot parse, which would
    crash instead of falling back to the default -- bash's `[[ =~ ^[0-9]+$ ]]` is
    ASCII-only, so this keeps the same, narrower acceptance set."""
    if not (value.isascii() and value.isdigit()) or len(value) > 9:
        return default
    return int(value)


def _is_pending(check: dict) -> bool:
    bucket = (check.get("bucket") or "").lower()
    if bucket:
        return bucket == "pending"
    return (check.get("state") or "").upper() in RUNNING


def _is_failed(check: dict) -> bool:
    bucket = (check.get("bucket") or "").lower()
    if bucket:
        return bucket in ("fail", "cancel")
    return (check.get("state") or "").upper() in FAILED_STATES


def _parse(raw: str) -> tuple[bool, int, int, int]:
    """Mirrors the bash predecessor's embedded `parse()` python3 -c helper:
    (read_ok, total, pending, failed)."""
    try:
        data = json.loads(raw)
    except Exception:
        data = None
    if not isinstance(data, list):
        return False, 0, 0, 0
    pending = sum(1 for c in data if _is_pending(c))
    failed = sum(1 for c in data if _is_failed(c))
    return True, len(data), pending, failed


def _dump(raw: str) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        data = []
    return "\n".join("  {}: state={} bucket={}".format(c.get("name"), c.get("state"), c.get("bucket"))
                     for c in data)


def main(argv: list[str]) -> int:
    if not argv:
        print("pipeline_poll_github: usage: pipeline_poll_github.py <pr_number>", file=sys.stderr)
        return 1
    pr_num = argv[0]

    interval = _coerce_int(os.environ.get("POLL_INTERVAL", ""), 30)
    max_wait = _coerce_int(os.environ.get("POLL_MAX_WAIT", ""), 1800)
    nopipe_max_wait = _coerce_int(os.environ.get("POLL_NOPIPE_MAX_WAIT", ""), 300)
    if interval < 1:
        interval = 30
    if nopipe_max_wait > max_wait:
        nopipe_max_wait = max_wait

    start = time.monotonic()
    seen = False
    while True:
        result = subprocess.run(["gh", "pr", "checks", pr_num, "--json", "name,state,bucket"],
                                capture_output=True, text=True)
        elapsed = int(time.monotonic() - start)
        read_ok, total, pending, failed = _parse(result.stdout)
        if total != 0:
            seen = True

        if total == 0:
            cur_ceil = nopipe_max_wait if not seen else max_wait
            print("{} no checks reported yet read_ok={} ({}s/{}s)".format(
                time.strftime("%H:%M:%S"), int(read_ok), elapsed, cur_ceil))
        else:
            print("{} checks={} pending={} failed={} ({}s/{}s)".format(
                time.strftime("%H:%M:%S"), total, pending, failed, elapsed, max_wait))

        if total != 0 and pending == 0:
            print("All checks concluded. failed={}".format(failed))
            print(_dump(result.stdout))
            return 0

        if not seen and elapsed >= nopipe_max_wait:
            due = "nochecks"
        elif elapsed >= max_wait:
            due = "maxwait"
        else:
            due = ""

        if due:
            if not read_ok:
                probe = subprocess.run(["gh", "pr", "view", pr_num, "--json", "number"],
                                       capture_output=True, text=True)
                if probe.returncode != 0:
                    cause = " ".join(result.stderr.split())
                    print("UNKNOWN after {}s — gh could not read the checks for PR #{} and "
                          "could not reach the PR either, so the check state is unverified. "
                          "Not a green build. gh said: {}".format(elapsed, pr_num, cause))
                    return 5
            if due == "nochecks":
                print("NO CHECKS on PR #{} after {}s — gh answered and this PR has none. "
                      "Nothing to wait for: this is not a build result.".format(pr_num, elapsed))
                return 3
            print("TIMED OUT after {}s — checks={} pending={} failed={}, checks_seen={}. "
                  "No conclusion was read; go look at the PR.".format(
                      elapsed, total, pending, failed, int(seen)))
            print(_dump(result.stdout))
            return 4

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
