#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Build the context JSON for mr_presentation.py --mode create.

Collects branch metadata, commit log, file stat, and (when a JIRA key is
derivable from the branch) a light-mode ticket-view summary via jira_scope.py
(char-capped, not line-capped). Prints a single JSON object to stdout.
Stdlib-only; safe with branch-derived values (list-form subprocess, no shell).

Usage:
    build_mr_ctx.py [--repo PATH]

--repo defaults to cwd (pass the worktree abs path -- the Bash cwd does not
follow a worktree, see check_base_freshness.py).
"""
import argparse
import json
import os
import subprocess


def run(args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def git(repo, args):
    return run(["git", "-C", repo, *args])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()
    repo = args.repo

    source = git(repo, ["branch", "--show-current"]).strip()
    target = git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"]).strip().replace("refs/remotes/origin/", "")
    base = git(repo, ["merge-base", "HEAD", f"origin/{target}"]).strip()
    head = git(repo, ["rev-parse", "HEAD"]).strip()
    # JIRA key via the shared helper: honors DEV_FLOW_NO_TRACKER / .claude/no-tracker marker,
    # anchored match (slug suffixes like ...-native-2022 don't false-match). --cwd is
    # --repo, not the ambient cwd, so the marker walk-up follows the target repo.
    derive = os.path.join(os.path.dirname(os.path.abspath(__file__)), "derive_ticket_key.py")
    jira = run(["python3", derive, source, "--cwd", repo]).strip()
    ticket = ""
    if jira:
        scope = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jira_scope.py")
        ticket = run(["python3", scope, "--mode", "light", "--key", jira, "--repo-root", repo])
    print(json.dumps({
        "ticket_summary": ticket,
        "commit_log": git(repo, ["log", "--oneline", f"{base}..HEAD"]),
        "file_stat": git(repo, ["diff", "--stat", f"{base}..HEAD"]),
        "branch_meta": {"source": source, "target": target, "head_sha": head, "jira_key": jira},
    }))


if __name__ == "__main__":
    main()
