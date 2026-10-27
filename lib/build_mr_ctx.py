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
    build_mr_ctx.py [--repo PATH] [--target BRANCH] [--print-target]

--repo defaults to cwd (pass the worktree abs path -- the Bash cwd does not
follow a worktree, see check_base_freshness.py).

The MR target (and the merge-base the logs/stats are computed from) is, in
order: --target (must exist as origin/<BRANCH>; empty = not given); else a
stacked parent -- the nearest origin/* branch whose tip is an ancestor of HEAD
and holds HEAD's oldest commit not yet in origin/<default> (assumes a linear
stack); else origin/HEAD. A candidate that shares commits but diverged (a
parent that advanced after the fork, or a branch stacked on HEAD) is never
picked: the default is used and a `note:` names it on stderr. A parent
rebased and force-pushed after the fork no longer contains HEAD's oldest
commit not in origin/<default>, so it is not a candidate at all: the
default is used with no `note:` (pass --target).
branch_meta.target_source says which (explicit|stacked|default).
--print-target prints only the resolved target name.

Exit codes: 0 ok; 2 STOP --target not on origin; 3 STOP tie between stacked
parents (re-run with --target).
"""
import argparse
import json
import os
import subprocess
import sys


def run(args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def git(repo, args):
    return run(["git", "-C", repo, *args])


def ok(repo, args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def detect_stacked(repo, default, source):
    """Nearest origin/* branch HEAD is stacked on: it shares HEAD's oldest commit not yet in origin/<default>."""
    own = git(repo, ["rev-list", "--reverse", "--topo-order", "HEAD", f"^origin/{default}"]).split()
    if not own:
        return ""
    head = git(repo, ["rev-parse", "HEAD"]).strip()
    names = git(repo, ["for-each-ref", "--contains", own[0], "--format=%(refname)", "refs/remotes/origin/"]).split()
    found, diverged = [], []
    for name in (n.removeprefix("refs/remotes/origin/") for n in names):
        if name == source:
            continue
        ref = f"origin/{name}"
        tip = git(repo, ["rev-parse", ref]).strip()
        if tip != head and ok(repo, ["merge-base", "--is-ancestor", "HEAD", ref]):
            continue  # stacked on top of HEAD, not under it
        if git(repo, ["merge-base", ref, "HEAD"]).strip() != tip:
            diverged.append(name)  # advanced parent, or HEAD is its parent: indistinguishable, never picked
            continue
        found.append((int(git(repo, ["rev-list", "--count", f"{tip}..HEAD"]).strip() or 0), name))
    if not found:
        if diverged:
            listed = sorted(diverged)
            shown = ", ".join(listed[:3]) + (f" (+{len(listed) - 3} more)" if len(listed) > 3 else "")
            sys.stderr.write(f"note: {shown} share commits with HEAD but diverged from it "
                             "(a parent that advanced, or a branch stacked on HEAD) — using the default branch; "
                             "pass --target <branch> if HEAD is stacked on one of them.\n")
        return ""
    best = min(d for d, _ in found)
    nearest = sorted(n for d, n in found if d == best)
    if len(nearest) > 1:
        sys.stderr.write(f"STOP: HEAD is stacked on more than one remote branch at the same distance: {', '.join(nearest)}"
                         " — re-run with --target <branch>.\n")
        sys.exit(3)
    return nearest[0]


def resolve_target(repo, explicit, source):
    if explicit:
        if not ok(repo, ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{explicit}"]):
            sys.stderr.write(f"STOP: --target '{explicit}' does not resolve to origin/{explicit} (fetch first, or check the name).\n")
            sys.exit(2)
        return explicit, "explicit"
    default = git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"]).strip().replace("refs/remotes/origin/", "")
    stacked = detect_stacked(repo, default, source) if default else ""
    return (stacked, "stacked") if stacked else (default, "default")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--target", default="")
    ap.add_argument("--print-target", action="store_true")
    args = ap.parse_args()
    repo = args.repo

    source = git(repo, ["branch", "--show-current"]).strip()
    target, target_source = resolve_target(repo, args.target, source)
    if args.print_target:
        print(target)
        return
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
        "changed_files": git(repo, ["diff", "--name-only", f"{base}..HEAD"]),
        "branch_meta": {"source": source, "target": target, "target_source": target_source, "head_sha": head, "jira_key": jira},
    }))


if __name__ == "__main__":
    main()
