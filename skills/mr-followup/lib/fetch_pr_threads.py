#!/usr/bin/env python3
"""
Fetch unresolved review comments from a GitHub PR.

Usage:
    python3 fetch_pr_threads.py <slug> <pr_number>

    slug        owner/repo (e.g. octocat/hello-world)
    pr_number   PR number (integer)

Auth is delegated to the `gh` CLI. No token plumbing here.
Output format mirrors fetch_discussions.py:

    ## <author> — <path> line <N>
    <body>

    ...

    No open review comments found.    (when empty)

A fetch that could not complete never prints that sentinel. The failure paths
this script converts print the UNAVAILABLE marker on stdout, a message on
stderr, and exit 2; the marker is appended, not substituted -- the threads
query prints its comments before the reviews query runs, so a failure there
leaves comments above the marker. Not every failure is converted: a payload
whose shape the queries do not expect (measured on a GraphQL errors response
with `"repository": null`) still raises, giving rc 1, empty stdout and a
traceback. Observed cases, not a closed set -- the consumer contract in
SKILL.md step 5 is "any non-zero rc is a stop", not "rc 2".
"""
import json
import shutil
import subprocess
import sys
from typing import NoReturn

# Fail-closed contract, same rc as lib/check_mr_state.py and the GitLab twin in
# this directory: a fetch that could not complete must never be readable as
# "zero review comments". Signal on BOTH channels -- rc 2 and a stdout marker.
# ASCII-only: an em dash here raises UnicodeEncodeError under an ascii stdout,
# losing the marker exactly when it is needed.
UNAVAILABLE = "REVIEW COMMENTS UNAVAILABLE - fetch failed, see stderr"
RC_UNVERIFIABLE = 2


def unverifiable(message: str) -> NoReturn:
    print(UNAVAILABLE)
    print(message, file=sys.stderr)
    sys.exit(RC_UNVERIFIABLE)


THREADS_QUERY = """
query($owner:String!, $repo:String!, $num:Int!, $cursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$num) {
      reviewThreads(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          isOutdated
          path
          line
          comments(first:50) {
            nodes { author { login } body }
          }
        }
      }
    }
  }
}
"""

REVIEWS_QUERY = """
query($owner:String!, $repo:String!, $num:Int!) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$num) {
      reviews(first:50) {
        nodes { state body author { login } }
      }
    }
  }
}
"""


def run_gh_graphql(query: str, owner: str, repo: str, num: int, cursor: str | None = None) -> dict:
    if shutil.which("gh") is None:
        unverifiable("Error: `gh` CLI not found. Install from https://cli.github.com/ and run `gh auth login`.")
    cmd = [
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-F", f"owner={owner}",
        "-F", f"repo={repo}",
        "-F", f"num={num}",
    ]
    if cursor is not None:
        cmd.extend(["-F", f"cursor={cursor}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        unverifiable(f"Error: gh api graphql failed (exit {result.returncode}).")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        unverifiable(f"Error: gh api graphql returned non-JSON output - {exc}")


def fetch_unresolved_threads(owner: str, repo: str, num: int) -> list:
    threads = []
    cursor = None
    while True:
        data = run_gh_graphql(THREADS_QUERY, owner, repo, num, cursor)
        pr = data["data"]["repository"]["pullRequest"]
        if pr is None:
            unverifiable(f"Error: PR #{num} not found in {owner}/{repo}.")
        block = pr["reviewThreads"]
        for node in block["nodes"]:
            if node.get("isResolved") or node.get("isOutdated"):
                continue
            threads.append(node)
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]
    return threads


def fetch_review_bodies(owner: str, repo: str, num: int) -> list:
    data = run_gh_graphql(REVIEWS_QUERY, owner, repo, num)
    pr = data["data"]["repository"]["pullRequest"]
    if pr is None:
        unverifiable(f"Error: PR #{num} returned no pullRequest node for the reviews query in {owner}/{repo}.")
    out = []
    for r in pr["reviews"]["nodes"]:
        body = (r.get("body") or "").strip()
        state = r.get("state") or ""
        if not body or state == "PENDING":
            continue
        out.append(r)
    return out


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <slug> <pr_number>", file=sys.stderr)
        sys.exit(1)

    slug, pr_str = sys.argv[1], sys.argv[2]
    if "/" not in slug:
        sys.exit(f"Error: invalid slug '{slug}', expected owner/repo.")
    owner, repo = slug.split("/", 1)
    try:
        num = int(pr_str)
    except ValueError:
        sys.exit(f"Error: pr_number must be an integer, got '{pr_str}'.")

    count = 0

    for thread in fetch_unresolved_threads(owner, repo, num):
        path = thread.get("path") or "(general comment)"
        line = thread.get("line") or "?"
        for c in thread["comments"]["nodes"]:
            author = (c.get("author") or {}).get("login") or "unknown"
            body = c.get("body") or ""
            print(f"## {author} — {path} line {line}")
            print(body)
            print()
            count += 1

    for review in fetch_review_bodies(owner, repo, num):
        author = (review.get("author") or {}).get("login") or "unknown"
        state = review.get("state") or ""
        print(f"## {author} — review summary ({state})")
        print(review["body"])
        print()
        count += 1

    if count == 0:
        print("No open review comments found.")


if __name__ == "__main__":
    main()
