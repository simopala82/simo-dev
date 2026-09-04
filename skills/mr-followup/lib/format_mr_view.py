#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Format `glab mr view <branch> -F json`'s stdout into one BASE/IID/STATE/DRAFT/SOURCE
line.

Replaces the shell one-liner previously used at skills/mr-followup/SKILL.md step 1 (no
external JSON-parsing tool is a plugin dependency any more, see README.md
"Prerequisites"). Reads the MR JSON
object from stdin, prints exactly one line:

    BASE=<target_branch>  IID=<iid>  STATE=<state>  DRAFT=<draft>  SOURCE=<source_branch>

Exit 0 on success. Exit 1 with nothing on stdout when stdin is empty or not valid JSON
-- the same failure shape as an expired token, a network error, or a wrong cwd
(SKILL.md step 1 treats any non-zero exit here as "no JSON on stdout", regardless of
cause, and does not depend on a specific exit-code value).
"""
import json
import sys


def _fmt(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


def main(argv: list[str]) -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 1
    if not isinstance(data, dict):
        return 1
    fields = ("target_branch", "iid", "state", "draft", "source_branch")
    labels = ("BASE", "IID", "STATE", "DRAFT", "SOURCE")
    parts = [f"{label}={_fmt(data.get(field))}" for label, field in zip(labels, fields)]
    sys.stdout.write("  ".join(parts) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
