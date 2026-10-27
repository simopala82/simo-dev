#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read/write the global simo-dev per-repo config.

Maps a repo-id (lib/repo_identity.py: normalized origin URL, fallback the
main checkout's absolute path -- the same value from every worktree) to
per-repo settings. One file, global per user. Unlike tracker_config.py,
keyed by repo, not by ticket prefix: one prefix can span repos that need
different settings.

Path: ~/.claude/simo-dev/repo.json (override for tests only via
SIMO_DEV_REPO_CONFIG or the --config flag).

Usage:
    repo_config.py get --repo PATH <FIELD> [--config PATH]
    repo_config.py set <REPO> <FIELD> <VALUE> [--config PATH]

FIELD is one of: release_skill.

release_skill is a skill name; simo-dev:mr-followup step 8.7 (post-merge)
invokes it with the Skill tool and args exactly `post-merge`. Sole consumer.

`get` resolves PATH (any checkout or worktree of the repo) to its repo-id
and prints the field's value, or nothing when unset; exit 0 either way.
`set` takes REPO as an existing directory (resolved the same way) or a
literal repo-id / origin URL (normalized), so a repo can be configured
without a local clone. A path-like value (starting with /, ~ or .)
that is not a directory is refused (exit 2): it is a typo'd checkout,
not an origin URL. So is a literal that normalizes to no `/` (a bare
name like `app`): no repo-id ever matches it.

Exit codes:
    0   success (get: value printed, possibly empty; set: written)
    1   the config file is not valid JSON or not an object of objects,
        or the directory given (get --repo, or set REPO) is not a git repo
    2   argparse usage error, FIELD not in FIELDS, or set: REPO a
        path-like value (/, ~, .) that is not a directory, or a literal
        with no `/` after normalization
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from repo_identity import normalize_url, repo_id  # noqa: E402

FIELDS = ("release_skill",)


def config_path() -> Path:
    override = os.environ.get("SIMO_DEV_REPO_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "simo-dev" / "repo.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"repo_config: {path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        print(f"repo_config: {path} must be a JSON object of objects", file=sys.stderr)
        sys.exit(1)
    return data


def _check_field(field: str) -> None:
    if field not in FIELDS:
        print(f"repo_config: unknown field {field!r}, must be one of {FIELDS}", file=sys.stderr)
        sys.exit(2)


def _resolve(repo: str) -> str:
    path = os.path.expanduser(repo)
    if os.path.isdir(path):
        return repo_id(path)
    if repo.startswith(("/", "~", ".")):
        print(f"repo_config: {repo} is not a directory", file=sys.stderr)
        sys.exit(2)
    rid = normalize_url(repo)
    if "/" not in rid:
        print(f"repo_config: {repo} is neither a directory nor a repo-id (host/path)", file=sys.stderr)
        sys.exit(2)
    return rid


def get_value(repo_path: str, field: str, path: Path) -> str:
    _check_field(field)
    data = _load(path)
    value = data.get(repo_id(repo_path), {}).get(field, "")
    return str(value) if value else ""


def set_value(repo: str, field: str, value: str, path: Path) -> None:
    _check_field(field)
    data = _load(path)
    data.setdefault(_resolve(repo), {})[field] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Read/write the simo-dev per-repo config.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get")
    g.add_argument("--repo", required=True)
    g.add_argument("field")
    g.add_argument("--config", help="override the config path (tests only)")

    s = sub.add_parser("set")
    s.add_argument("repo")
    s.add_argument("field")
    s.add_argument("value")
    s.add_argument("--config", help="override the config path (tests only)")

    args = p.parse_args(argv)
    path = Path(args.config) if args.config else config_path()

    if args.cmd == "get":
        sys.stdout.write(get_value(args.repo, args.field, path))
        return 0

    set_value(args.repo, args.field, args.value, path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
