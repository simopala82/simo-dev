#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Poll GitHub PR checks until every check has concluded, or one of two ceilings
comes due. Python port of pipeline_poll_github.sh (OSS migration step 7c) -- same
env vars, same exit-code contract, identical semantics to the GitLab twin
(pipeline_poll_gitlab.py).

Usage: pipeline_poll_github.py <pr_number> <worktree-abs>
Exit codes (the verdict):
  0  every check concluded -- a FAILING check is still 0: the poll reports, it
     does not judge
  1  refused to start (no <pr_number> or <worktree-abs> argument, an empty
     <worktree-abs>, or HEAD does not resolve there), or gh cannot run in
     <worktree-abs> (worktree gone mid-poll, gh missing or not executable)
  3  no checks for HEAD, and a reachable forge said so -- the PR has none, or its
     head never became HEAD before the ceiling
  4  no conclusion -- a ceiling came due with checks still pending or vanished,
     or the PR head moved off HEAD after its checks were seen (at once)
  5  the read that hit the ceiling could not be verified, so the check state is
     UNVERIFIED -- including a PR head that could not be read

Notes on `gh pr checks` (unchanged from the bash predecessor): the `conclusion`
JSON field is not available on all gh builds; `bucket` is gh's always-present
normalized category, `state` is the fallback. The exit code of `gh pr checks` is
IGNORED -- gh returns non-zero for failing/pending/absent checks alike, so a
liveness probe (`gh pr view --json number`) separates "no checks" from "gh could
not answer", run only when a ceiling is due, the PR head read equalled HEAD, and the
checks read produced no parseable JSON (an unreadable head is rc 5 when the ceiling comes due).

Pinned to HEAD: the SHA of `<worktree-abs>`'s HEAD is read once at startup, and every
`gh` call runs with `<worktree-abs>` as its cwd -- `gh` resolves the repo, and so which
PR the number names, from the cwd's git remotes. Every poll first reads the
PR head (`gh pr view --json headRefOid`). Right after a push GitHub can still report
the previous head, whose checks may have concluded already; until the head is HEAD the
checks are not read and nothing counts as seen, so POLL_NOPIPE_MAX_WAIT bounds the
wait. A head that moves off HEAD after checks were seen (another push) ends the
poll at once with rc 4: HEAD's checks can no longer be read.
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


def _head_sha(worktree: str) -> str:
    """The commit the poll is about: the worktree's HEAD, just pushed. "" when it
    cannot be resolved (not a repo, unborn HEAD, missing directory)."""
    try:
        result = subprocess.run(["git", "-C", worktree, "rev-parse", "--verify", "--quiet", "HEAD"],
                                capture_output=True, text=True)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _gh(args: list[str], worktree: str) -> subprocess.CompletedProcess:
    """Run gh in <worktree-abs>. A worktree that vanished mid-poll, or a gh that is
    missing or not executable, ends the poll with rc 1 and a one-line reason."""
    try:
        return subprocess.run(["gh"] + args, capture_output=True, text=True, cwd=worktree)
    except OSError as exc:
        print("pipeline_poll_github: cannot run gh in worktree '{}' (worktree gone or "
              "unusable, or gh missing) — refusing to poll: {}".format(worktree, exc),
              file=sys.stderr)
        raise SystemExit(1)


def _pr_head(pr_num: str, worktree: str) -> tuple[str, str]:
    """(head commit as GitHub currently sees it, gh's stderr); head is "" when gh
    cannot answer, and the stderr then carries the cause rc 5 must report."""
    result = _gh(["pr", "view", pr_num, "--json", "headRefOid"], worktree)
    try:
        data = json.loads(result.stdout) if result.returncode == 0 else {}
        return str(data.get("headRefOid") or ""), result.stderr
    except (ValueError, AttributeError):
        return "", result.stderr


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
    if len(argv) < 2:
        print("pipeline_poll_github: usage: pipeline_poll_github.py <pr_number> <worktree-abs>",
              file=sys.stderr)
        return 1
    pr_num, worktree = argv[0], argv[1]
    if not worktree:
        print("pipeline_poll_github: empty <worktree-abs> literal — refusing to poll: "
              "git -C \"\" would read the process cwd's HEAD", file=sys.stderr)
        return 1
    sha = _head_sha(worktree)
    if not sha:
        print("pipeline_poll_github: cannot resolve HEAD in '{}' — refusing to poll: without "
              "the pushed commit's SHA the PR may still report the checks of an older "
              "head".format(worktree), file=sys.stderr)
        return 1

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
        head, head_err = _pr_head(pr_num, worktree)
        if head == sha:
            result = _gh(["pr", "checks", pr_num, "--json", "name,state,bucket"], worktree)
            out, err = result.stdout, result.stderr
            read_ok, total, pending, failed = _parse(out)
        else:
            out, err = "", head_err
            read_ok, total, pending, failed = bool(head), 0, 0, 0
        elapsed = int(time.monotonic() - start)
        if total != 0:
            seen = True

        if total == 0:
            cur_ceil = nopipe_max_wait if not seen else max_wait
            print("{} no checks reported yet read_ok={} head={} ({}s/{}s)".format(
                time.strftime("%H:%M:%S"), int(read_ok), head[:12] or "unknown", elapsed, cur_ceil))
        else:
            print("{} checks={} pending={} failed={} ({}s/{}s)".format(
                time.strftime("%H:%M:%S"), total, pending, failed, elapsed, max_wait))

        if total != 0 and pending == 0:
            print("All checks concluded. failed={}".format(failed))
            print(_dump(out))
            return 0

        if seen and head and head != sha:
            print("HEAD MOVED after {}s — the head of PR #{} is now {}, not {} whose checks "
                  "were seen but had not concluded. No conclusion was read for {}; go look at "
                  "the PR.".format(elapsed, pr_num, head[:12], sha[:12], sha[:12]))
            return 4

        if not seen and elapsed >= nopipe_max_wait:
            due = "nochecks"
        elif elapsed >= max_wait:
            due = "maxwait"
        else:
            due = ""

        if due:
            if not read_ok and not head:
                print("UNKNOWN after {}s — gh could not read the head of PR #{}, so the check "
                      "state for {} is unverified. Not a green build. gh said: {}".format(
                          elapsed, pr_num, sha[:12], " ".join(err.split())))
                return 5
            if not read_ok:
                probe = _gh(["pr", "view", pr_num, "--json", "number"], worktree)
                if probe.returncode != 0:
                    cause = " ".join(err.split())
                    print("UNKNOWN after {}s — gh could not read the checks for PR #{} and "
                          "could not reach the PR either, so the check state is unverified. "
                          "Not a green build. gh said: {}".format(elapsed, pr_num, cause))
                    return 5
            if due == "nochecks" and head != sha:
                print("NO CHECKS for {} on PR #{} after {}s — the PR head is {}, not HEAD: the "
                      "push has not reached the PR, HEAD was never pushed, or the remote branch "
                      "moved past the local HEAD. Nothing to wait for: this is not a build "
                      "result.".format(sha[:12], pr_num, elapsed, head[:12]))
                return 3
            if due == "nochecks":
                print("NO CHECKS on PR #{} at {} after {}s — gh answered and this PR has none. "
                      "Nothing to wait for: this is not a build result.".format(
                          pr_num, sha[:12], elapsed))
                return 3
            print("TIMED OUT after {}s — checks={} pending={} failed={}, checks_seen={}. "
                  "No conclusion was read; go look at the PR.".format(
                      elapsed, total, pending, failed, int(seen)))
            print(_dump(out))
            return 4

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
