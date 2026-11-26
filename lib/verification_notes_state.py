#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Per-worktree record of the "Verification notes (auto)" comments posted, per MR.

The tracker's comment_cmd can only post, never edit, and every call site rebuilds the notes as
fresh prose, so idempotency keys on the HEAD the notes were last checked at and on whether the
QA-gate line was posted, never on body text. Stored in the worktree's own git dir, one file per
branch: survives compaction and session changes, never committed.

  has    --repo W --mr URL                                   "yes" when a record exists, else "no"
  show   --repo W --mr URL                                   the recorded body, or nothing
  stale  --repo W --mr URL --head SHA [--qa-gate]            "yes" when no record, another head, or a QA gate not yet posted
  record --repo W --mr URL --head SHA [--body F] [--qa-gate] store; no --body keeps the previous body; qa_gate is sticky

URL is the forge's canonical web URL (GitLab web_url, GitHub url); a trailing "/" is ignored.
A detached HEAD (mid-rebase, a detached checkout) reads the "detached" file, so `has` answers "no" there.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PREFIX = "simo-dev-vnotes"


def git_dir(repo: str) -> Path:
    out = subprocess.run(["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-dir"], capture_output=True, check=True,
                         encoding="utf-8", errors="surrogateescape")
    return Path(out.stdout.strip())


def branch_key(repo: str) -> str:
    out = subprocess.run(["git", "-C", repo, "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True,
                         encoding="utf-8", errors="surrogateescape")
    return out.stdout.strip().replace("/", "-") if out.returncode == 0 else "detached"


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["has", "show", "stale", "record"])
    p.add_argument("--repo", required=True)
    p.add_argument("--mr", required=True)
    p.add_argument("--head")
    p.add_argument("--body")
    p.add_argument("--qa-gate", action="store_true")
    args = p.parse_args()
    if not Path(args.repo).is_absolute():
        return fail(f"--repo must be an absolute path, got {args.repo!r}")
    mr = args.mr.rstrip("/")
    if not mr:
        return fail("--mr must be a non-empty MR/PR URL")
    if args.cmd in ("stale", "record") and not args.head:
        return fail(f"{args.cmd} needs --head")
    try:
        path = git_dir(args.repo) / f"{PREFIX}.{branch_key(args.repo)}.json"
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError) as e:
        return fail(f"not a git repo: {args.repo} ({e})")
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        rec = state.get(mr)
        if rec is not None and not (isinstance(rec.get("body"), str) and isinstance(rec.get("head"), str) and "qa_gate" in rec):
            raise ValueError(f"malformed record for {mr}")
    except (ValueError, AttributeError, OSError) as e:
        return fail(f"unreadable state file {path}: {e}")
    if args.cmd == "has":
        print("yes" if rec else "no")
    elif args.cmd == "show":
        sys.stdout.write(rec["body"] if rec else "")
    elif args.cmd == "stale":
        print("yes" if not rec or rec["head"] != args.head or (args.qa_gate and not rec["qa_gate"]) else "no")
    else:
        if args.body:
            text = Path(args.body).read_text(encoding="utf-8")
        elif rec:
            text = rec["body"]
        else:
            return fail("record needs --body when nothing is recorded for this MR")
        state[mr] = {"body": text, "head": args.head, "qa_gate": args.qa_gate or bool(rec and rec["qa_gate"])}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
