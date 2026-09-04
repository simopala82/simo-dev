#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Collect the mechanical facts needed to judge whether a plan's `Depends on`
assumption about another ticket is still current.

Incident 2026-08-05: a worktree resumed after its blocking
sub-task merged with a different API shape than the plan assumed. The worktree
still compiled -- no touched symbol had been removed -- so a green build was
mistaken for proof the dependency was current. This script never makes that
judgment itself: it only reports the dependency ticket's JIRA status and, when
resolved, the diff that actually merged. The caller (plan-execute step 1)
decides whether that diff still matches the plan's assumption.

Usage:
    check_dependency_ticket.py <TICKET> [--repo PATH] [--base BRANCH]

<TICKET> is the JIRA key from the plan's `Depends on` field.
--repo defaults to cwd (pass the worktree abs path -- see CLAUDE.md's
"Worktree cwd gotcha"). --base defaults to the remote default branch, same
resolution as check_base_freshness.default_base.

Resolution order for the merged diff, once the ticket's JIRA status is Done/
Closed/Resolved:
  1. task-intake names branches `<type>/<jira-key-lower>-<desc>` -- look for a
     matching ref under `origin/*` and diff it against the merge-base with
     <base> (no forge auth needed, works even after the MR UI has moved on).
  2. Fall back to `git log --all --grep=<TICKET>` on the fetched history --
     catches a squash-merge commit whose branch was deleted by GitLab's
     force_remove_source_branch.
  3. Last resort: search the forge for an MR/PR mentioning `<TICKET>` (`glab
     mr list --search` then `gh pr list --search`) and diff it directly, for
     the case where the local clone never fetched the merging branch.
  Each step only runs if the previous one found nothing -- best-effort, never
  an error: this script has no judgment to protect, only data to report.

Exit codes:
    0  ran to completion; result printed on stdout -- one of:
       open                       ticket not yet Done/Closed/Resolved
       resolved-with-diff         ticket resolved, merged diff follows on
                                   stdout after the status line
       resolved-no-diff-found     ticket resolved, no diff could be recovered
       unverifiable               JIRA status itself could not be read
       This script never blocks: judgment stays with the caller.
    2  usage/argument error (argparse's own exit code)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

_DONE_STATUSES = ("done", "closed", "resolved")


def _jira_fields(key: str) -> dict:
    """Read a ticket's `fields` dict via `jira issue view <key> --raw`."""
    out = subprocess.check_output(
        ["jira", "issue", "view", key, "--raw"], stderr=subprocess.PIPE
    )
    return json.loads(out).get("fields", {})


def ticket_status(key: str) -> str | None:
    """Return the ticket's JIRA status name, or None if it could not be read."""
    try:
        fields = _jira_fields(key)
        return (fields.get("status") or {}).get("name")
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, AttributeError):
        # AttributeError covers a successful-but-non-dict JSON payload (a rare
        # jira-cli error path can exit 0 with e.g. `null` or a bare array) --
        # this script's "never blocks" contract must degrade to unverifiable
        # here too, not crash.
        return None


BRANCH_TYPES = ("feat", "fix", "chore", "refactor", "docs", "test", "perf")


def _git(repo: str, args: list[str]) -> str:
    """Best-effort git call -- returns "" on any failure. Unlike
    check_base_freshness's strict _git, every caller here is one step in a
    fallback chain, so a failure just means "try the next source", not an
    error to surface."""
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def default_base(repo: str) -> str:
    ref = _git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if ref:
        return ref.replace("refs/remotes/origin/", "")
    for cand in ("main", "master"):
        if _git(repo, ["rev-parse", "--verify", "--quiet", f"origin/{cand}"]):
            return cand
    return "master"


def find_branch_diff(repo: str, key: str, base: str) -> str:
    """Diff of the newest task-intake-named branch for `key` against its
    merge-base with <base>, or "" if no matching ref exists."""
    key_lower = key.lower()
    refs = _git(repo, ["for-each-ref", "refs/remotes/origin", "--sort=-committerdate", "--format=%(refname:short)"])
    for ref in refs.splitlines():
        name = ref.removeprefix("origin/")
        if any(name.startswith(f"{t}/{key_lower}-") for t in BRANCH_TYPES):
            merge_base = _git(repo, ["merge-base", f"origin/{base}", ref])
            if merge_base:
                return _git(repo, ["diff", f"{merge_base}..{ref}"])
    return ""


def find_log_diff(repo: str, key: str) -> str:
    """Diff of the most recent commit whose message mentions `key`, or "" if
    none is found in the fetched history (catches a squash-merge whose branch
    was already deleted by force_remove_source_branch). Diffs against the
    first parent rather than `git show`: on a clean 2-parent merge commit (a
    non-squash --no-ff merge, or GitLab's "merge commit" strategy) `git show`
    prints only the commit metadata -- no diff at all, since git suppresses
    the combined diff when there are no conflicts."""
    sha = _git(repo, ["log", "--all", "-i", f"--grep={key}", "--format=%H", "-1"])
    if not sha:
        return ""
    return _git(repo, ["diff", f"{sha}^", sha])


def _mentions_key(item: dict, key: str) -> bool:
    """glab/gh --search is a fuzzy text search, not an exact-key filter --
    empirically it can return dozens of unrelated results (incident found
    during review: searching a short numeric substring matched 30 MRs with no
    visible relation to it). Keep only hits whose title or description
    actually names the ticket, so a search result on some OTHER ticket is
    never mistaken for this one's diff."""
    key_upper = key.upper()
    return key_upper in (item.get("title") or "").upper() or key_upper in (item.get("description") or "").upper()


def find_forge_diff(repo: str, key: str) -> str:
    """Best-effort diff straight from the forge, for the case where the local
    clone never fetched the merging branch. Searches for the MR/PR by ticket
    key rather than guessing a branch name -- task-intake's branch grammar
    always carries a `-<short-desc>` suffix this script cannot reconstruct,
    so a guessed branch name would never match. Mirrors check_mr_state.py's
    glab-then-gh fallback; a failure here just means "no diff found". `cwd`
    is threaded through explicitly (never the ambient process cwd) for the
    same reason every other lookup here takes `repo` -- see CLAUDE.md's
    "Worktree cwd gotcha"."""
    try:
        out = subprocess.run(
            ["glab", "mr", "list", "--search", key, "--all", "-F", "json"],
            capture_output=True, text=True, check=True, cwd=repo,
        )
        match = next((m for m in json.loads(out.stdout) if _mentions_key(m, key)), None)
        if match:
            diff = subprocess.run(
                ["glab", "mr", "diff", str(match["iid"]), "--raw"], capture_output=True, text=True, check=True, cwd=repo
            )
            if diff.stdout.strip():
                return diff.stdout
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--search", key, "--state", "all", "--json", "number,title,body"],
            capture_output=True, text=True, check=True, cwd=repo,
        )
        match = next(
            (p for p in json.loads(out.stdout) if _mentions_key({**p, "description": p.get("body")}, key)), None
        )
        if match:
            diff = subprocess.run(
                ["gh", "pr", "diff", str(match["number"])], capture_output=True, text=True, check=True, cwd=repo
            )
            if diff.stdout.strip():
                return diff.stdout
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return ""


def resolve_diff(repo: str, key: str, base: str) -> str:
    return find_branch_diff(repo, key, base) or find_log_diff(repo, key) or find_forge_diff(repo, key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base")
    args = parser.parse_args(argv)

    status = ticket_status(args.ticket)
    if status is None:
        print("unverifiable")
        return 0
    if status.lower() not in _DONE_STATUSES:
        print("open")
        print(f"status: {status}")
        return 0

    base = args.base or default_base(args.repo)
    _git(args.repo, ["fetch", "origin", "--quiet"])  # best-effort; a stale local clone just falls through the chain
    diff = resolve_diff(args.repo, args.ticket, base)
    if diff:
        print("resolved-with-diff")
        print(diff)
        return 0
    print("resolved-no-diff-found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
