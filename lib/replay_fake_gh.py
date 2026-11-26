#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Fake `gh` for step_replay's mr-followup step: answers the GitHub calls of the flow from a frozen forge state.

step_replay copies it to <run dir>/bin/gh, first on the run's PATH. $SIMO_DEV_REPLAY_FORGE names a directory with
`state.json` (written by step_replay: pr, threads, reviews, ci, repo, origin, branch); every call is appended to
`calls.jsonl` there. No network: the pull request's head is the branch head in the local bare origin, and its
one check runs the fixture's CI command on a copy of that commit (cached per commit). Unknown calls exit 1.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FORGE = Path(os.environ.get("SIMO_DEV_REPLAY_FORGE", ""))


def _state() -> dict:
    return json.loads((FORGE / "state.json").read_text())


def _log(args: list[str], **extra) -> None:
    with open(FORGE / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "cwd": os.getcwd(), **extra}) + "\n")


def _flag(args: list[str], name: str) -> str | None:
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return None


def _query(value, expr: str):
    """The jq subset the flow uses: `.a`, `.a.b`, `.[].a`, `.[0].a`."""
    parts = [p for p in expr.strip().lstrip(".").replace("[]", ".[]").split(".") if p]
    values = [value]
    for part in parts:
        nxt = []
        for v in values:
            if part == "[]":
                nxt.extend(v if isinstance(v, list) else [])
            elif part.startswith("[") and part.endswith("]"):
                i = int(part[1:-1])
                nxt.append(v[i] if isinstance(v, list) and -len(v) <= i < len(v) else None)
            else:
                nxt.append(v.get(part) if isinstance(v, dict) else None)
        values = nxt
    return values


def _emit(data, args: list[str]) -> int:
    expr = _flag(args, "-q") or _flag(args, "--jq")
    if not expr:
        print(json.dumps(data))
        return 0
    for v in _query(data, expr):
        print(v if isinstance(v, str) else "" if v is None else json.dumps(v))
    return 0


def _git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def head(state: dict) -> str:
    return _git("--git-dir", state["origin"], "rev-parse", "--verify", "--quiet", f"refs/heads/{state['branch']}")


def _pr(state: dict) -> dict:
    pr = state["pr"]
    sha = head(state)
    return {"number": pr["number"], "title": pr.get("title", ""), "body": pr.get("body", ""), "state": "OPEN",
            "isDraft": False, "baseRefName": "master", "headRefName": state["branch"], "headRefOid": sha,
            "url": f"https://github.com/{pr['slug']}/pull/{pr['number']}", "mergeable": "MERGEABLE",
            "comments": [], "reviewDecision": "CHANGES_REQUESTED", "author": {"login": "replay"}}


def ci(state: dict, sha: str) -> dict:
    """Run the fixture's CI command on a copy of the commit; cached per commit."""
    cache = FORGE / f"ci-{sha}.json"
    if cache.is_file():
        return json.loads(cache.read_text())
    work = FORGE / f"ci-{sha}"
    work.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(["git", "--git-dir", state["origin"], "archive", sha], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(work)], stdin=archive.stdout, check=True)
    archive.wait()
    # Inside the run's sandbox pytest's default basetemp (under /tmp/claude-<uid>/pytest-of-<user>) is denied,
    # which kept the check red after every fix in the first mr-followup batch.
    tmp = FORGE / "tmp"
    tmp.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(tmp)}
    proc = subprocess.run(state["ci"]["command"], cwd=work, capture_output=True, text=True, env=env)
    out = {"sha": sha, "ok": proc.returncode == 0, "log": (proc.stdout + proc.stderr)[-6000:]}
    cache.write_text(json.dumps(out))
    return out


def _checks(state: dict) -> list[dict]:
    sha = head(state)
    if not sha:
        return []
    res = ci(state, sha)
    verdict = "SUCCESS" if res["ok"] else "FAILURE"
    return [{"name": state["ci"]["name"], "state": verdict, "conclusion": verdict, "bucket": "pass" if res["ok"] else "fail",
             "link": f"https://github.com/{state['pr']['slug']}/actions/runs/1", "workflow": "ci",
             "description": "", "event": "pull_request"}]


def _graphql(state: dict, args: list[str]) -> dict:
    query = next((a.split("=", 1)[1] for a in args if a.startswith("query=")), "")
    if "reviewThreads" in query:
        nodes = [{"isResolved": False, "isOutdated": False, "path": t["path"], "line": t["line"],
                  "comments": {"nodes": [{"author": {"login": t["author"]}, "body": t["body"]}]}}
                 for t in state.get("threads", [])]
        pr = {"reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}
    elif "reviews" in query:
        pr = {"reviews": {"nodes": [{"state": r["state"], "body": r["body"], "author": {"login": r["author"]}}
                                    for r in state.get("reviews", [])]}}
    else:
        return {"errors": [{"message": "replay: unsupported graphql query"}]}
    return {"data": {"repository": {"pullRequest": pr}}}


def main(args: list[str]) -> int:
    if args[:1] == ["--version"] or args[:1] == ["version"]:
        print("gh version replay-fake")
        return 0
    state = _state()
    _log(args)
    cmd = args[:2]
    if args[:1] == ["auth"]:
        print("Logged in to github.com as replay (replay-fake)")
        return 0
    if cmd == ["pr", "view"]:
        return _emit(_pr(state), args)
    if cmd == ["pr", "list"]:
        return _emit([_pr(state)], args)
    if cmd == ["pr", "checks"]:
        checks = _checks(state)
        if "--json" in args:
            return _emit(checks, args)
        for c in checks:
            print(f"{c['name']}\t{c['bucket']}\t1m\t{c['link']}")
        return 1 if any(c["bucket"] == "fail" for c in checks) else 0
    if cmd in (["run", "view"], ["run", "list"]):
        sha = head(state)
        res = ci(state, sha) if sha else {"ok": True, "log": ""}
        run = {"databaseId": 1, "headSha": sha, "status": "completed", "name": "ci", "workflowName": "ci",
               "conclusion": "success" if res["ok"] else "failure", "headBranch": state["branch"],
               "url": f"https://github.com/{state['pr']['slug']}/actions/runs/1"}
        if cmd == ["run", "list"]:
            return _emit([run], args)
        if "--log" in args or "--log-failed" in args:
            print(res["log"])
            return 0
        return _emit(run, args) if "--json" in args else (print(f"ci {run['conclusion']} {sha[:12]}") or 0)
    if cmd == ["api", "graphql"]:
        return _emit(_graphql(state, args), args)
    if args[:1] == ["api"]:
        print("{}")
        return 0
    if cmd in (["pr", "comment"], ["pr", "review"], ["pr", "edit"], ["pr", "ready"]):
        print(f"https://github.com/{state['pr']['slug']}/pull/{state['pr']['number']}")
        return 0
    print(f"replay fake gh: unsupported call: gh {' '.join(args)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
