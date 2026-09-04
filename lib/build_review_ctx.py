#!/usr/bin/env python3
"""Build the dev-mr-review context JSON from review_meta.json.

Emits the fields mr_presentation.py consumes (ticket_summary, commit_log, file_stat,
branch_meta) plus jira_scope (light summary+description) and jira_key. Replaces the
inline heredoc that SKILL.md step 4 used to write into the per-session tmp dir.

Usage:
    build_review_ctx.py <meta.json> [--repo PATH]

--repo defaults to cwd (pass the worktree abs path -- the Bash cwd does not
follow a worktree, see check_base_freshness.py).
"""
import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent


def run(args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=30)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return ""


def jira_key_from(branch, repo):
    # Delegate to the shared helper: honors DEV_FLOW_NO_TRACKER / .claude/no-tracker and
    # anchors the match so slug year/number suffixes (…-native-2022) don't false-match.
    # repo (== --repo) anchors the marker walk instead of trusting ambient cwd.
    sys.path.insert(0, str(HERE))
    from derive_ticket_key import derive
    return derive(branch, repo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meta_file")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()

    meta = json.loads(pathlib.Path(args.meta_file).read_text())
    target = meta.get("target_branch") or meta.get("baseRefName", "")
    head = meta.get("diff_refs", {}).get("head_sha") or meta.get("headRefOid", "") or meta.get("head_sha", "")
    source = meta.get("source_branch") or meta.get("headRefName", "")
    rev = f"origin/{target}...{head}" if head else f"origin/{target}...HEAD"
    log = run(["git", "-C", args.repo, "log", "--oneline", rev])
    stat = run(["git", "-C", args.repo, "diff", "--stat", rev])
    key = jira_key_from(source, args.repo)
    jira_scope = ""
    if key:
        jira_scope = run([sys.executable, str(HERE / "jira_scope.py"),
                          "--mode", "light", "--key", key, "--repo-root", args.repo])
    print(json.dumps({
        "ticket_summary": (meta.get("title", "") + "\n" + (meta.get("description") or meta.get("body", ""))).strip(),
        "commit_log": log,
        "file_stat": stat,
        "branch_meta": {"source": source, "target": target, "head_sha": head},
        "jira_scope": jira_scope,
        "jira_key": key,
    }))


if __name__ == "__main__":
    main()
