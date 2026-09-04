#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Poll GitLab pipelines for <branch> until a terminal status or a ceiling comes
due. Python port of the GitLab poll body formerly embedded in
skills/mr-followup/lib/pipeline-scripts.md (OSS migration step 7c) -- same env
vars, same exit-code contract, same sticky SEEN flag, same unanchored -- but now
literal, not regex -- substring match against <branch> (the bash predecessor used
`grep`, a BRE, so a branch containing `.`/`+` matched as a regex wildcard; this
port narrows that to a plain substring check, deliberately, since the regex
reading only ever produced unwanted extra matches).

Unlike the bash predecessor, this is a static file taking <worktree-abs>/<branch>
as argv instead of being rewritten per-task with substituted literals (approved
design, Decision 2) -- matching how pipeline_poll_github.py is invoked.

Usage: pipeline_poll_gitlab.py <worktree-abs> <branch>
Exit codes (the verdict):
  0  a pipeline reached a terminal status -- a `failed` one is still 0: the poll
     reports, it does not judge
  1  refused to start (missing argv, empty <branch>, or <worktree-abs> does not
     resolve to a real directory)
  3  no pipeline exists for <branch>, and a reachable forge said so
  4  a ceiling came due with no terminal status -- still transitional, a pipeline
     that vanished from the list, or a line matched with an unparseable status
  5  the read that hit the ceiling could not be verified, so the state is
     UNVERIFIED -- never treated as green

Two ceilings: POLL_NOPIPE_MAX_WAIT (default 300s) bounds "nothing matches <branch>
yet"; POLL_MAX_WAIT (default 1800s) bounds "a pipeline is visible but has not
concluded" -- these are different waits, gated on a sticky SEEN flag. POLL_INTERVAL
(default 30s) is the gap between polls. Both ceilings and the interval are wall
clock (time.monotonic()), not a count of sleeps, so per-poll `glab` latency counts
against them.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

TRANSITIONAL = {"running", "pending", "created", "preparing",
                "waiting_for_resource", "scheduled", "canceling"}
STATUS_RE = re.compile(r"\(([a-z_]+)\)")


def _coerce_int(value: str, default: int) -> int:
    """Digit-only, length-capped at 9 (a deliberate margin under the 19-digit wall
    where a signed 64-bit comparison would otherwise misbehave). `isascii()`
    matters: `str.isdigit()` alone also accepts non-ASCII digit characters (e.g.
    the superscript '²') that `int()` cannot parse, which would crash instead of
    falling back to the default -- bash's `[[ =~ ^[0-9]+$ ]]` is ASCII-only, so
    this keeps the same, narrower acceptance set."""
    if not (value.isascii() and value.isdigit()) or len(value) > 9:
        return default
    return int(value)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("pipeline_poll_gitlab: usage: pipeline_poll_gitlab.py <worktree-abs> <branch>",
              file=sys.stderr)
        return 1
    worktree, branch = argv[0], argv[1]
    if not branch:
        print("pipeline_poll_gitlab: empty <branch> literal — refusing to poll: "
              "an unanchored substring match against it would match every pipeline",
              file=sys.stderr)
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
        try:
            result = subprocess.run(["glab", "pipeline", "list"], cwd=worktree,
                                    capture_output=True, text=True)
        except (FileNotFoundError, NotADirectoryError):
            print("pipeline_poll_gitlab: cannot reach worktree '{}' — refusing to poll".format(worktree),
                  file=sys.stderr)
            return 1
        elapsed = int(time.monotonic() - start)
        out, rc, err = result.stdout, result.returncode, result.stderr

        # Literal substring, not the bash predecessor's `grep "<branch>"` (BRE) --
        # deliberately narrower: a branch containing `.`/`+` no longer matches as a
        # regex wildcard, which only ever caused unwanted extra matches there.
        line = ""
        for candidate in out.splitlines():
            if branch in candidate:
                line = candidate
                break
        if line:
            seen = True
        match = STATUS_RE.search(line)
        status = match.group(1) if match else ""

        print("{} status={} glab_rc={} elapsed={}s | {}".format(
            time.strftime("%H:%M:%S"), status or "none", rc, elapsed, line))

        if status in TRANSITIONAL:
            pass
        elif status == "":
            if line:
                print("Pipeline line matched but its status did not parse — retrying")
        else:
            print("Pipeline done: {}".format(status))
            return 0

        if not seen and elapsed >= nopipe_max_wait:
            due = "nopipe"
        elif elapsed >= max_wait:
            due = "maxwait"
        else:
            due = ""

        if due:
            if rc != 0:
                cause = " ".join(err.split())
                print("UNKNOWN after {}s — glab could not list pipelines (rc={}), so the "
                      "pipeline state for '{}' is unverified. Not a green pipeline. "
                      "glab said: {}".format(elapsed, rc, branch, cause))
                return 5
            if due == "nopipe":
                print("NO PIPELINE for '{}' after {}s — glab listed pipelines and none "
                      "matched. Nothing to wait for: this is not a pipeline result.".format(
                          branch, elapsed))
                return 3
            print("TIMED OUT after {}s — last status={}, pipeline_seen={}. No terminal "
                  "status was read; go look at the MR.".format(
                      elapsed, status or "none", int(seen)))
            return 4

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
