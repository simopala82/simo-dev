#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read one field out of a JSON file (or stdin) by dotted path.

Replaces the small command-line JSON field-extraction snippets previously used across
simo-dev's commit/mr-review/mr-followup skills -- no external JSON-parsing tool is a
plugin dependency any more (see README.md "Prerequisites"). Every single-field
extraction, reused or not, routes through this helper; only the one extraction that
isn't single-field (a composite multi-field formatted string) has its own script
instead: skills/mr-followup/lib/format_mr_view.py.

Usage:
    json_field.py <file|-> <path> [path2 ...] [default]

<file> is a path to a JSON file, or "-" to read JSON from stdin (for
`glab api user | python3 json_field.py - username`).

<path> is a dot-separated key path into nested objects (e.g. "branch_meta.target").
Multiple <path> arguments are tried in order -- the first one that resolves to a
present, non-null, non-false value wins (mirrors a chained `//` fallback, e.g.
".source_branch // .headRefName // \"\"").

With exactly one remaining argument after <file>, it is the sole <path> and there is
no default: a missing/null/false result prints an empty string. With two or more
remaining arguments, the LAST one is the default (mirrors a trailing `// default`
fallback) and every argument before it is a <path> tried in order.

A resolved JSON boolean prints as the lowercase "true"/"false" JSON itself uses, so
existing bash string-compares (`[ "$DRIFT" = "true" ]`) keep working unchanged.

Exit 0 whether the lookup found a value, fell through to the default, or (with no
default) printed an empty string -- a missing/null path is not itself an error
condition here. Exit 1 with a message on stderr when the input is not readable, not
valid JSON, or when a <path> resolves to a JSON object/array -- this script only
extracts scalar fields, so a nested object/array is a caller error, not something to
silently stringify. Exit 2 on a usage error (fewer than two arguments).
"""
import json
import sys

_MISSING = object()


def _resolve_one(data, path: str):
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def resolve(data, paths: list[str]):
    """First path in `paths` resolving to a present, non-null, non-false value; else _MISSING."""
    for path in paths:
        value = _resolve_one(data, path)
        if value is not _MISSING and value is not None and value is not False:
            return value
    return _MISSING


def render(value, default: str | None) -> str:
    if value is _MISSING or value is None:
        return default if default is not None else ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: json_field.py <file|-> <path> [path2 ...] [default]\n")
        return 2
    src, rest = argv[0], argv[1:]
    if len(rest) == 1:
        paths, default = rest, None
    else:
        paths, default = rest[:-1], rest[-1]

    try:
        raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        sys.stderr.write(f"json_field.py: {e}\n")
        return 1

    resolved = resolve(data, paths)
    if isinstance(resolved, (dict, list)):
        sys.stderr.write(f"json_field.py: {paths[0]!r} resolved to a JSON object/array, not a scalar -- this script only extracts scalar fields\n")
        return 1

    sys.stdout.write(render(resolved, default))
    return 0


if __name__ == "__main__":
    # A Windows pipe/redirect defaults to the ANSI code page (cp1252), which cannot encode
    # most non-ASCII text. stderr keeps backslashreplace: reconfigure() without errors= resets it to strict.
    # stdin moves with stdout: a cp1252-decoded stdin re-encoded as UTF-8 would be mojibake.
    if sys.stdin:
        sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main(sys.argv[1:]))
