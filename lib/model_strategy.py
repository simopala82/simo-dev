#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read/write the global simo-dev model-strategy config.

Maps a dispatch category to the model that category's Agent dispatch
should use. One file, global per user, not per-repo. Unlike
tracker_config.py this has no per-project-prefix dimension: every
category applies across every repo a developer touches.

Path: ~/.claude/simo-dev/model-strategy.json (override for tests only
via SIMO_DEV_MODEL_STRATEGY_CONFIG or the --config flag).

Usage:
    model_strategy.py get <CATEGORY> [--config PATH]
    model_strategy.py set <CATEGORY> <VALUE> [--config PATH]

CATEGORY is one of: code_review, plan_critique, explore_research.

`get` prints the configured model name (empty string if the category
is not configured) and always exits 0 on a well-formed config -- an
absent entry is a normal outcome, not an error. Each category's caller
decides what an empty value means (pass no `model:` override and
inherit the caller's tier, or fall back to a hardcoded default already
documented at that call site) -- this script only stores and returns
strings, it does not know or enforce any default.

`set` rejects a category outside the fixed whitelist above (exit 2).
Unlike tracker_config.py's per-prefix fields, there is no per-user
extension point here: the three categories are the three places this
plugin actually dispatches an Agent with a configurable model.

Exit codes:
    0   success (get: value printed, possibly empty; set: written)
    1   the existing config file is not valid JSON
    2   argparse usage error (missing/invalid subcommand or argument),
        or set: category not in CATEGORIES
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CATEGORIES = ("code_review", "plan_critique", "explore_research")


def config_path() -> Path:
    override = os.environ.get("SIMO_DEV_MODEL_STRATEGY_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "simo-dev" / "model-strategy.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"model_strategy: {path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def get_value(category: str, path: Path) -> str:
    data = _load(path)
    value = data.get(category, "")
    return str(value) if value else ""


def set_value(category: str, value: str, path: Path) -> None:
    if category not in CATEGORIES:
        print(
            f"model_strategy: unknown category {category!r}, must be one of {CATEGORIES}",
            file=sys.stderr,
        )
        sys.exit(2)
    data = _load(path)
    data[category] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Read/write the simo-dev model strategy config.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get")
    g.add_argument("category")
    g.add_argument("--config", help="override the config path (tests only)")

    s = sub.add_parser("set")
    s.add_argument("category")
    s.add_argument("value")
    s.add_argument("--config", help="override the config path (tests only)")

    args = p.parse_args(argv)
    path = Path(args.config) if args.config else config_path()

    if args.cmd == "get":
        sys.stdout.write(get_value(args.category, path))
        return 0

    set_value(args.category, args.value, path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
