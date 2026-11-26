#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read/write the global simo-dev tracker adapter config.

Maps a ticket-key prefix (e.g. "CUS") to the skills/commands that fetch,
transition and create tickets for it. One file, global per user, not
per-repo — prefixes are stable across every repo a developer touches.

Path: ~/.claude/simo-dev/tracker.json (override for tests only via
SIMO_DEV_TRACKER_CONFIG or the --config flag). Whether that path is
versioned by the user's own dotfiles is outside this script's concern.

Usage:
    tracker_config.py get <PREFIX> <FIELD> [--config PATH]
    tracker_config.py set <PREFIX> <FIELD> <VALUE> [--config PATH]
    tracker_config.py run [--config PATH] <PREFIX> <FIELD> [ARGS...]
    tracker_config.py ticket-line <KEY> [--config PATH]

FIELD is one of: fetch_skill, transition_cmd, create_skill,
supports_subtasks, post_merge_state, post_merge_state_no_deploy, scope_fetch_cmd, comment_cmd,
link_cmd, assign_cmd, requirements_review_cmd, next_subtask_cmd,
pr_body_ticket_line, browse_url.

browse_url is a template, not a command: the web page of one ticket, with
`{key}` (the full key), `{number}` (the digits after its last dash) and
`{repo_url}` (the repo's `origin` as an https web URL, for a tracker backed by
the repo's own issues: "{repo_url}/issues/{number}"). This file only stores
it; the readers are lib/flow_dashboard.py (read-only; a {repo_url} template
gives no link there) and a status-line mod outside this plugin. Unconfigured
-> the ticket gets no link.

pr_body_ticket_line is a template, not a command: `ticket-line <KEY>`
substitutes `{key}` (the full ticket key) and `{number}` (the digits after
its last dash) and prints the result wrapped in a blank line, ready to append
to an MR/PR body. Typical value for a tracker backed by the same repo's
GitHub/GitLab issues: "Closes #{number}", so the forge closes the issue on
merge. Unconfigured, an empty KEY, or a KEY not ending in -<digits> -> prints
nothing. lib/mr_create.py body (simo-dev:commit step 7) is the only caller of ticket-line.

scope_fetch_cmd is invoked as `<CMD> <key> <comments>` and must print plain
text ticket-view output to stdout; jira_scope.py is the sole consumer. A
value of "-" means "explicitly declined" (jira_scope.py treats it exactly
like unconfigured: no fetch, no error).

comment_cmd is invoked as `<CMD> <key> <body-file>` and must post the
UTF-8 text in <body-file> as one ticket comment, exiting 0 on success.
lib/post_tracker_comment.py is the sole consumer — every gate-output
comment the dev flow posts (direction, plan, tests performed,
implementation summary, and the verification notes posted at MR open,
skills/commit/lib/verification-notes.md) routes through it. Body always on a file, never a positional
arg or heredoc: a positional/heredoc body hung the `jira` CLI 15+
minutes with no output (observed 2026-08-25). jira_comment.py (not shipped
in the published plugin) implements this contract for JIRA — configure with:

    tracker_config.py set <PREFIX> comment_cmd "python3 <path to jira_comment.py>"

<path to jira_comment.py> must be the plugin's source working tree
path (same rule as every other field here — see
_reject_if_pinned_plugin_cache_path), never a
.claude/plugins/cache/**/<version>/ path; `set` already rejects that
pattern.

link_cmd is invoked as `<CMD> <blocked-key> <blocker-key>` and must create a
"<blocked-key> is blocked by <blocker-key>" link between the two issues,
exiting 0 on success (already linked counts as success — idempotent, not an
error). simo-dev:task-intake step 2.5 is the sole consumer — recording a
dependency between sub-tasks split from the same umbrella (or between a
sub-task and an already-known external blocker), best-effort like
transition_cmd. jira_link.py (not shipped in the published plugin) implements
this contract for JIRA — configure with:

    tracker_config.py set <PREFIX> link_cmd "python3 <path to jira_link.py>"

Same source-working-tree-path rule as comment_cmd above.

assign_cmd is invoked as `<CMD> <key>` (a single ticket key, no other args) and
must assign the ticket to whatever user the configured command itself resolves
as "current", exiting 0 on success (already assigned to that user counts as
success — idempotent, not an error). simo-dev:task-intake step 5.6 is the sole
consumer, best-effort like transition_cmd. jira_assign.py (not shipped in the
published plugin) implements this contract for JIRA — configure with:

    tracker_config.py set <PREFIX> assign_cmd "python3 <path to jira_assign.py>"

Same source-working-tree-path rule as the fields above it.

requirements_review_cmd is invoked as `<CMD> <key> <body-file> [--replace]`; the
configured command must write the UTF-8 requirements/AC draft from <body-file>
into the ticket (mechanism left to that command) and exit 0 on success. When
the ticket already carries a block written by a previous run, the command must
leave it untouched and exit 4, unless --replace is given.
lib/post_requirements_review.py is the sole consumer, reached from
simo-dev:task-intake step 4.5 (ticket mode, key known immediately) and step
5.5 (prompt mode, key resolved later). jira_requirements_review.py (not shipped
in the published plugin) implements this contract for JIRA by appending a block
to the ticket description, never touching the author's text — configure with:

    tracker_config.py set <PREFIX> requirements_review_cmd "python3 <path to jira_requirements_review.py>"

Same source-working-tree-path rule as the fields above it.

next_subtask_cmd is invoked as `<CMD> <leaf-key> [--resolved-at <state>]` and
prints one JSON line, exiting 0 on success; empty stdout means nothing to
propose:

    {"parent": str|null, "parent_status": str|null, "parent_complete": bool,
     "ready": [{"key", "summary", "status", "components"}],
     "pending": [{"key", "summary", "status", "reason"}]}

`parent` is the leaf's umbrella (null when the leaf has no parent, or its
parent has no sub-tasks). A sibling (never the leaf itself) is `ready` when it
has not started (Backlog/Refinement/To Do-class states), is unassigned or
assigned to the current user (matched by a stable user id, never by email),
and every "is blocked by" blocker is Closed or at/past --resolved-at (default
Done). Otherwise it is `pending`, `reason` = the first of `started`,
`assigned-to-other`, `blocked-by:<KEY>[,<KEY>...]`. Done and Closed siblings,
and siblings whose state is outside the binding's known states (or that cannot
be read), appear in neither list. `parent_complete` = at least one non-Closed
sub-task exists and every one has a known state at/past Done: an unknown or
unreadable one keeps it false, fail-safe, since it may still be in work.
simo-dev:mr-followup step 8.6 is the sole consumer.
jira_next_subtask.py (not shipped in the published plugin) implements this
contract for JIRA — configure with:

    tracker_config.py set <PREFIX> next_subtask_cmd "python3 <path to jira_next_subtask.py>"

Same source-working-tree-path rule as the fields above it.

`get` prints the value (empty string if the prefix/field is not
configured) and always exits 0 on a well-formed config — an absent entry
is a normal outcome, not an error, mirroring derive_ticket_key.py's
no-key convention. `set` creates the config file and its parent
directory if missing, and merges into any existing entry for the prefix
without disturbing its other fields. `run` splits the configured value
with shlex and executes it with ARGS appended, propagating its exit
code — an empty/unconfigured value is a silent no-op (exit 0). Unlike
scope_fetch_cmd's shell-side invocation, `run` does the splitting in
Python so callers never depend on which shell renders the calling
skill body (a bash `$CMD` expansion is not word-split under zsh).
`run`'s ARGS positional is greedy (argparse REMAINDER): `--config`
MUST come before <PREFIX>, not after — placed later it is swallowed
into ARGS and forwarded to the invoked command instead of being
parsed as the config override.

Exit codes:
    0   success (get: value printed, possibly empty; set: written;
        run: command exited 0, or was a no-op; ticket-line: line printed,
        possibly empty)
    1   usage error, the existing config file is not valid JSON, or
        (run only) the configured command string is malformed or its
        executable was not found
    2   set: invalid value for a typed field (supports_subtasks wants
        "true"/"false"), or a value that pins a versioned plugin cache
        path (see _reject_if_pinned_plugin_cache_path)
    *   run: any other exit code is the invoked command's own
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

_PINNED_PLUGIN_CACHE_RE = re.compile(
    r"\.claude/plugins/cache/[^/\s]+/[^/\s]+/\d+\.\d+\.\d+/"
)

FIELDS = (
    "fetch_skill",
    "transition_cmd",
    "create_skill",
    "supports_subtasks",
    "post_merge_state",
    "post_merge_state_no_deploy",
    "scope_fetch_cmd",
    "comment_cmd",
    "link_cmd",
    "assign_cmd",
    "requirements_review_cmd",
    "next_subtask_cmd",
    "pr_body_ticket_line",
    "browse_url",
)


def config_path() -> Path:
    override = os.environ.get("SIMO_DEV_TRACKER_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "simo-dev" / "tracker.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"tracker_config: {path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def get_field(prefix: str, field: str, path: Path) -> str:
    data = _load(path)
    value = data.get(prefix.upper(), {}).get(field, "")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value) if value else ""


def _reject_if_pinned_plugin_cache_path(field: str, value: str) -> None:
    match = _PINNED_PLUGIN_CACHE_RE.search(value)
    if not match:
        return
    print(
        f"tracker_config: refusing to store {field}={value!r} because it contains {match.group(0)!r} — "
        "this pins a versioned plugin cache path. The next plugin release's orphan cleanup deletes that "
        "directory, turning this field into a silent no-op. Use the plugin's source working tree path "
        "instead (the same fallback skill bodies use, e.g. $HOME/.claude/local-marketplace/<plugin>/...), "
        "which stays valid across every release.",
        file=sys.stderr,
    )
    sys.exit(2)


def set_field(prefix: str, field: str, value: str, path: Path) -> None:
    data = _load(path)
    entry = data.setdefault(prefix.upper(), {})
    if field == "supports_subtasks":
        low = value.strip().lower()
        if low not in ("true", "false"):
            print(
                f"tracker_config: supports_subtasks wants 'true'/'false', got {value!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        entry[field] = low == "true"
    else:
        _reject_if_pinned_plugin_cache_path(field, value)
        entry[field] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def run_field(prefix: str, field: str, args: list[str], path: Path) -> int:
    cmd = get_field(prefix, field, path)
    if not cmd:
        return 0
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        print(f"tracker_config: {field} value {cmd!r} is not a valid shell command: {e}", file=sys.stderr)
        return 1
    if not argv:
        print(f"tracker_config: {field} value {cmd!r} is not a valid shell command: no command word", file=sys.stderr)
        return 1
    argv += args
    try:
        proc = subprocess.run(argv)
    except FileNotFoundError as e:
        print(f"tracker_config: {field} command {argv[0]!r} not found: {e}", file=sys.stderr)
        return 1
    return proc.returncode


def ticket_line(key: str, path: Path) -> str:
    if not key:
        return ""
    prefix, _, number = key.rpartition("-")
    if not prefix or not number.isdigit():
        return ""
    template = get_field(prefix, "pr_body_ticket_line", path)
    if not template:
        return ""
    return "\n" + template.replace("{key}", key).replace("{number}", number) + "\n"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Read/write the simo-dev tracker config.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get")
    g.add_argument("prefix")
    g.add_argument("field", choices=FIELDS)
    g.add_argument("--config", help="override the config path (tests only)")

    s = sub.add_parser("set")
    s.add_argument("prefix")
    s.add_argument("field", choices=FIELDS)
    s.add_argument("value")
    s.add_argument("--config", help="override the config path (tests only)")

    r = sub.add_parser("run")
    r.add_argument("prefix")
    r.add_argument("field", choices=FIELDS)
    r.add_argument("--config", help="override the config path (tests only)")
    r.add_argument("args", nargs=argparse.REMAINDER)

    t = sub.add_parser("ticket-line")
    t.add_argument("key")
    t.add_argument("--config", help="override the config path (tests only)")

    args = p.parse_args(argv)
    path = Path(args.config) if args.config else config_path()

    if args.cmd == "get":
        sys.stdout.write(get_field(args.prefix, args.field, path))
        return 0

    if args.cmd == "ticket-line":
        sys.stdout.write(ticket_line(args.key, path))
        return 0

    if args.cmd == "run":
        return run_field(args.prefix, args.field, args.args, path)

    set_field(args.prefix, args.field, args.value, path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
