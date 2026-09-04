#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Derive the ticket key from a git branch name for the canonical dev flow.

Single source of truth for ticket-key derivation used by simo-dev:commit
(build_mr_ctx.py + commit-message scope) and simo-dev:mr-followup (step 1
+ step 8 transitions). Prints the key (uppercased) to stdout, or an empty
string when no valid key is present.

Usage:
    derive_ticket_key.py <branch> [--cwd DIR]

Three guards, matching the three cases:

1. no-tracker opt-out. Prints "" when DEV_FLOW_NO_TRACKER=1 OR a
   `.claude/no-tracker` marker file exists at/above --cwd (default:
   process cwd), bounded by the enclosing git repo. Personal repos with
   no linked ticket tracker (myrepo, myrepo-tools) carry the marker,
   so a slug that *looks* like a key never triggers a bogus transition.

   The walk-up stops at the first repo root (a dir containing `.git`) so a
   marker never escapes its own repo. This matters because `~/.claude` is a
   symlink into the myrepo repo, i.e. `$HOME/.claude/no-tracker` IS
   `myrepo/.claude/no-tracker`. Without the boundary, an unbounded walk-up
   from any repo under `$HOME` reaches `cur=$HOME`, finds that marker, and
   wrongly opts every tracker-linked repo (prompter, content-hub, …) out of key
   derivation.

2. ticket-key override. A `.claude/ticket-key-override` file (first non-blank
   line = a ticket key, e.g. "PROJ-1994") at/above --cwd, same walk-up/boundary
   as the no-tracker marker above, wins over the branch-derived key below.
   Worktree-local and untracked — never commit it. Written by
   simo-dev:task-intake's "late umbrella promotion" (step 2.5): when a ticket
   already in flight turns out to need a split, this points every later flow
   transition (commit, mr-followup) at the new sub-task instead of the
   already-pushed branch's own key, without renaming that branch.

3. Anchored match. The regex `^[A-Za-z][A-Za-z0-9]{1,9}-[0-9]+` is anchored to
   the start of the LAST path segment of the branch (the convention is
   `<type>/<ticket-key-lowercase>-<short-desc>`, so the key — if any — is always
   the first token after the final `/`). This kills the old `re.search`
   false-match on year/number slug suffixes:
       feat/rebalancer-beta-native-2022  ->  "" (was NATIVE-2022)
       feat/ACME-1234-foo                ->  ACME-1234

   Optional allowlist: set DEV_FLOW_JIRA_PROJECTS to a comma-separated list of
   project prefixes (e.g. "ACME,FOO,BAR") to additionally require the
   matched prefix to be known. Off by default (anchoring alone is sufficient).

Exit code is always 0 — an absent/invalid key is a normal outcome, not an error.
"""
import os
import re
import sys

_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,9})-([0-9]+)")


def _no_tracker_marker(start: str) -> bool:
    """True if a `.claude/no-tracker` marker exists at/above `start`, bounded by
    the enclosing git repo (walk-up stops at the first dir containing `.git`).

    The marker is checked BEFORE the boundary at each level, so a repo root
    carrying both `.git` and `.claude/no-tracker` (e.g. myrepo) still opts
    out; the boundary only prevents a marker ABOVE the repo — notably
    `$HOME/.claude/no-tracker`, which resolves through the `~/.claude` symlink to
    the main checkout — from leaking into tracker-linked repos.
    """
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".claude", "no-tracker")):
            return True
        if os.path.exists(os.path.join(cur, ".git")):  # repo boundary
            return False
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _ticket_key_override(start: str) -> str:
    """First non-blank line of `.claude/ticket-key-override`, walked up like
    `.claude/no-tracker`, bounded by the enclosing git repo. See the module
    docstring, guard 2. A present-but-blank/whitespace-only file is treated as
    absent (falls through to the branch-derived key, or a marker/file further up)."""
    cur = os.path.abspath(start)
    while True:
        p = os.path.join(cur, ".claude", "ticket-key-override")
        if os.path.exists(p):
            try:
                for line in open(p):
                    line = line.strip()
                    if line:
                        return line
            except OSError:
                pass
        if os.path.exists(os.path.join(cur, ".git")):  # repo boundary
            return ""
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent


def derive(branch: str, cwd: str | None = None) -> str:
    """Return the uppercased ticket key for `branch`, or "" if none/opted-out."""
    if os.environ.get("DEV_FLOW_NO_TRACKER") == "1":
        return ""
    start = cwd or os.getcwd()
    if _no_tracker_marker(start):
        return ""
    if override := _ticket_key_override(start):
        return override.upper()

    segment = (branch or "").rstrip("/").split("/")[-1]
    m = _KEY_RE.match(segment)
    if not m:
        return ""

    key = f"{m.group(1).upper()}-{m.group(2)}"
    allow = os.environ.get("DEV_FLOW_JIRA_PROJECTS", "").strip()
    if allow:
        allowed = {p.strip().upper() for p in allow.split(",") if p.strip()}
        if m.group(1).upper() not in allowed:
            return ""
    return key


def _selftest() -> int:
    # marker/env guards must be neutralized for the pure-regex cases
    os.environ.pop("DEV_FLOW_NO_TRACKER", None)
    os.environ.pop("DEV_FLOW_JIRA_PROJECTS", None)
    nomarker = "/"  # root has no .claude/no-tracker

    cases = [
        # (branch, cwd, env, expected)
        ("feat/rebalancer-beta-native-2022", nomarker, {}, ""),
        ("feat/ACME-1234-foo", nomarker, {}, "ACME-1234"),
        ("fix/foo-456-certs", nomarker, {}, "FOO-456"),
        ("feat/acme-1234-topn-by-maxrank", nomarker, {}, "ACME-1234"),
        ("ACME-1234-no-type-prefix", nomarker, {}, "ACME-1234"),
        ("rebalancer-beta-native-2022", nomarker, {}, ""),
        ("chore/bump-deps-2024", nomarker, {}, ""),
        ("feat/bar-789-thing", nomarker, {}, "BAR-789"),
        ("master", nomarker, {}, ""),
        ("", nomarker, {}, ""),
        # nested path: key still after the last slash
        ("hotfix/team/ACME-99-x", nomarker, {}, "ACME-99"),
        # no-tracker via env
        ("feat/ACME-1234-foo", nomarker, {"DEV_FLOW_NO_TRACKER": "1"}, ""),
        # allowlist accepts known prefix, rejects unknown
        ("feat/ACME-1234-foo", nomarker, {"DEV_FLOW_JIRA_PROJECTS": "ACME,FOO"}, "ACME-1234"),
        ("feat/BAZ-1234-foo", nomarker, {"DEV_FLOW_JIRA_PROJECTS": "ACME,FOO"}, ""),
    ]
    failures = 0
    for branch, cwd, env, expected in cases:
        saved = {k: os.environ.get(k) for k in ("DEV_FLOW_NO_TRACKER", "DEV_FLOW_JIRA_PROJECTS")}
        os.environ.pop("DEV_FLOW_NO_TRACKER", None)
        os.environ.pop("DEV_FLOW_JIRA_PROJECTS", None)
        os.environ.update(env)
        got = derive(branch, cwd)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        status = "ok " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"  [{status}] {branch!r} env={env} -> {got!r} (want {expected!r})")
    failures += _selftest_boundary()
    failures += _selftest_override()
    total = len(cases) + _BOUNDARY_CASE_COUNT + _OVERRIDE_CASE_COUNT
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


_BOUNDARY_CASE_COUNT = 5
_OVERRIDE_CASE_COUNT = 4


def _selftest_override() -> int:
    """`.claude/ticket-key-override` cases: wins over the branch-derived key, respects
    the same repo boundary as the no-tracker marker, and DEV_FLOW_NO_TRACKER still wins
    over it (opt-out beats override)."""
    import tempfile

    os.environ.pop("DEV_FLOW_NO_TRACKER", None)
    os.environ.pop("DEV_FLOW_JIRA_PROJECTS", None)
    failures = 0
    with tempfile.TemporaryDirectory() as root:
        repo = os.path.join(root, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        os.makedirs(os.path.join(repo, ".claude"))
        with open(os.path.join(repo, ".claude", "ticket-key-override"), "w") as fh:
            fh.write("proj-1994\n")
        deep = os.path.join(repo, "src", "deep")
        os.makedirs(deep)

        outside = os.path.join(root, "elsewhere")
        os.makedirs(outside)

        cases: list[tuple[str, str, dict, str]] = [
            # override beats the branch-derived key, from the repo root...
            ("feat/proj-1987-schedule-tune", repo, {}, "PROJ-1994"),
            # ...and from a nested cwd, same walk-up as no-tracker
            ("feat/proj-1987-schedule-tune", deep, {}, "PROJ-1994"),
            # no override file above cwd -> branch-derived key, unaffected
            ("feat/ACME-1-x", outside, {}, "ACME-1"),
            # DEV_FLOW_NO_TRACKER still wins over an override present in the repo
            ("feat/proj-1987-schedule-tune", repo, {"DEV_FLOW_NO_TRACKER": "1"}, ""),
        ]
        for branch, cwd, env, expected in cases:
            saved = os.environ.get("DEV_FLOW_NO_TRACKER")
            os.environ.pop("DEV_FLOW_NO_TRACKER", None)
            os.environ.update(env)
            got = derive(branch, cwd)
            if saved is None:
                os.environ.pop("DEV_FLOW_NO_TRACKER", None)
            else:
                os.environ["DEV_FLOW_NO_TRACKER"] = saved
            status = "ok " if got == expected else "FAIL"
            if got != expected:
                failures += 1
            label = os.path.relpath(cwd, root)
            print(f"  [{status}] override cwd={label!r} {branch!r} env={env} -> {got!r} (want {expected!r})")
    return failures


def _selftest_boundary() -> int:
    """Repo-boundary cases: a marker ABOVE the repo must not leak in, while a
    marker AT the repo root still opts out. Mirrors the `$HOME/.claude/no-tracker`
    symlink leak that motivated the boundary."""
    import tempfile

    os.environ.pop("DEV_FLOW_NO_TRACKER", None)
    os.environ.pop("DEV_FLOW_JIRA_PROJECTS", None)
    failures = 0
    with tempfile.TemporaryDirectory() as root:
        # parent-level marker (simulates $HOME/.claude/no-tracker)
        os.makedirs(os.path.join(root, ".claude"))
        open(os.path.join(root, ".claude", "no-tracker"), "w").close()

        # repo WITHOUT its own marker, sitting under the parent marker
        plain = os.path.join(root, "repo_plain")
        os.makedirs(os.path.join(plain, ".git"))
        deep = os.path.join(plain, "src", "deep")
        os.makedirs(deep)

        # repo WITH its own marker at the root
        optout = os.path.join(root, "repo_optout")
        os.makedirs(os.path.join(optout, ".git"))
        os.makedirs(os.path.join(optout, ".claude"))
        open(os.path.join(optout, ".claude", "no-tracker"), "w").close()

        # worktree: `.git` is a FILE (gitdir pointer), not a dir — the boundary
        # must still stop here so the parent marker can't leak in.
        wt = os.path.join(root, "repo_worktree")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as fh:
            fh.write("gitdir: /somewhere/.git/worktrees/wt\n")

        cases = [
            # (branch, cwd, expected) — parent marker must NOT leak past repo .git
            ("feat/ACME-1-x", plain, "ACME-1"),
            ("feat/ACME-1-x", deep, "ACME-1"),
            # repo carrying its own marker still opts out
            ("feat/ACME-1-x", optout, ""),
            # worktree root (.git is a file) still bounds the walk-up
            ("feat/ACME-1-x", wt, "ACME-1"),
            # no repo boundary above cwd -> parent marker applies (degenerate)
            ("feat/ACME-1-x", os.path.join(root, "loose"), ""),
        ]
        os.makedirs(os.path.join(root, "loose"))
        for branch, cwd, expected in cases:
            got = derive(branch, cwd)
            status = "ok " if got == expected else "FAIL"
            if got != expected:
                failures += 1
            label = os.path.relpath(cwd, root)
            print(f"  [{status}] boundary cwd={label!r} {branch!r} -> {got!r} (want {expected!r})")
    return failures


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:]]
    if "--selftest" in args:
        return _selftest()

    cwd = None
    if "--cwd" in args:
        i = args.index("--cwd")
        cwd = args[i + 1]
        del args[i : i + 2]

    branch = args[0] if args else ""
    sys.stdout.write(derive(branch, cwd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
