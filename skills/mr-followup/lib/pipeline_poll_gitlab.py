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
  3  no pipeline exists for <branch>, and a reachable forge said so -- after the
     POLL_NOPIPE_MAX_WAIT ceiling, or at once (a `NO CI CONFIG` line) when the no-CI
     probe below finds no CI config for <branch>
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

No-CI probe: on the first successful `glab pipeline list` read that shows nothing for
<branch> while nothing has been seen yet, the poll asks GitLab once whether a pipeline
can exist at all (Auto DevOps off, ci_config_path empty or local, config file missing
on <branch>). When it finds no CI config, the poll ends with rc 3 at once instead of waiting the
POLL_NOPIPE_MAX_WAIT ceiling; any other answer keeps the ceiling. Known cases the
probe cannot see, each an early (never green) rc 3 -- an observed list, not a closed
one: pipeline/scan execution policies (GitLab Ultimate), `external` pipelines a CI
outside GitLab creates by posting a commit status, and merged-results pipelines whose
merge ref carries a config this branch lacks. See lib/pipeline-scripts.md.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote

TRANSITIONAL = {"running", "pending", "created", "preparing",
                "waiting_for_resource", "scheduled", "canceling"}
STATUS_RE = re.compile(r"\(([a-z_]+)\)")
NO_FILE_MESSAGE = "404 File Not Found"


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


def _glab_api(worktree: str, endpoint: str) -> tuple[int, object]:
    try:
        result = subprocess.run(["glab", "api", endpoint], cwd=worktree, capture_output=True, text=True)
    except (FileNotFoundError, NotADirectoryError):
        return 1, None  # ambiguous; the next `pipeline list` reports the vanished worktree
    try:
        body = json.loads(result.stdout)
    except ValueError:
        body = None
    return result.returncode, body


def _no_ci_reason(worktree: str, branch: str) -> str:
    """Non-empty only when the probe finds no CI config for <branch>:
    Auto DevOps off, ci_config_path empty or local, and the config file answering
    `404 File Not Found` on <branch> (glab 1.119.0, measured 2026-09-30; a missing ref
    answers `404 Commit Not Found` instead). Every other outcome -- an api error, a
    remote/cross-project config path, a response without the ci_config_path key, any
    other 404 -- returns "" so the caller keeps the ceiling."""
    rc, project = _glab_api(worktree, "projects/:id")
    if rc != 0 or not isinstance(project, dict) or project.get("auto_devops_enabled") is not False:
        return ""
    if "ci_config_path" not in project:
        return ""
    path = project["ci_config_path"] or ".gitlab-ci.yml"
    if "@" in path or "://" in path:
        return ""
    rc, body = _glab_api(worktree, "projects/:id/repository/files/{}?ref={}".format(
        quote(path, safe=""), quote(branch, safe="")))
    if rc != 0 and isinstance(body, dict) and body.get("message") == NO_FILE_MESSAGE:
        return "no {} on the branch, no remote ci_config_path, Auto DevOps off".format(path)
    return ""


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
    probed = False
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

        if rc == 0 and not line and not seen and not probed:
            probed = True
            reason = _no_ci_reason(worktree, branch)
            if reason:
                print("NO CI CONFIG for '{}' after {}s — {}. No pipeline is expected: "
                      "this is not a pipeline result.".format(
                          branch, int(time.monotonic() - start), reason))
                return 3

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
