#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Guard against pushing a follow-up commit onto a branch whose MR/PR is no
longer open.

Closes the gap where GitLab's force_remove_source_branch deletes the source
branch on merge: a later push under the same branch name silently recreates
it as a fresh orphan branch with no MR attached (git prints "[new branch]"
instead of the usual "oldsha..newsha" -- easy to miss without an explicit
check).

Usage:
    check_mr_state.py <ref> --cwd DIR [--forge glab|gh] [--slug SLUG]

<ref> is a branch name (typical) or an MR IID / PR number -- both
`glab mr view` and `gh pr view` accept either form.

--forge is optional: omit it to try glab first, then gh (mirrors the
existing "glab mr view ... || gh pr view ..." fallback already used in
commit/SKILL.md step 6).

--cwd is required: it is the absolute path of the repo/worktree to ask about,
and it becomes the CLI subprocess's working directory. Both glab and gh
resolve their project from the cwd, and `-R owner/repo` is not a substitute --
a bare slug pins the path but not the host, so glab resolves it against
`glab config get host` (measured 2026-08-15, glab 1.110.0: `glab mr list -R
<user>/<repo>` 404s from /tmp against gitlab.com while origin is
<your-gitlab-host>). Omitting it is an argparse usage error, hence rc 2 and
a STOP -- deliberately, per the note below.

Exit codes:
    0  MR/PR found, state is open (opened / OPEN)        -> safe to push
    1  MR/PR found, but NOT open (merged/closed/...)      -> STOP, do not push
    2  could not verify (glab/gh missing from PATH, a     -> STOP, do not push
       --cwd that is relative or unusable, an                (fail-closed)
       auth/network error, or an unrecognized CLI
       response)
    3  no MR/PR found for <ref> (forge confirmed there    -> nothing to guard
       is none)                                              yet, safe to push

rc=2 is deliberately the same code argparse itself uses for a usage error,
and the same one the interpreter exits with when it cannot even open this
file (e.g. the invoked path resolves into a deleted plugin cache dir, or
the caller's plugin-root expansion produced a malformed path):
those accidents now fail closed exactly like a missing glab/gh binary would,
instead of being silently indistinguishable from "no MR found".

Prints exactly one line to stdout:
    <state>       raw MR/PR state (rc 0/1)
    unverifiable  could not determine MR/PR state (rc 2)
    none          no MR/PR found (rc 3)
"""
from __future__ import annotations

import argparse
import json
import os.path
import subprocess
import sys

OPEN_STATES = {"opened", "open"}

# Sentinels distinguishing "confirmed absent" from "could not tell" through
# every layer (_run -> glab_state/gh_state -> detect_state -> main). Plain
# objects, not strings, so they can never collide with a real MR/PR state.
NOT_FOUND = object()
UNVERIFIABLE = object()

# Forge CLI error text isn't a stable contract across versions. A missed
# match here only downgrades to UNVERIFIABLE (fail-closed STOP), never to a
# silent push -- so an outdated marker is a (safe) missed optimization, not
# a correctness bug.
NOT_FOUND_MARKERS = {
    "glab": ("no open merge request available for",),
    "gh": ("no pull requests found",),
}


def _run(tool: str, args: list[str], cwd: str) -> object:
    """Run a forge CLI command with `cwd` as its working directory.

    Returns stdout on success, NOT_FOUND when stderr matches `tool`'s known
    "no MR/PR" message, or UNVERIFIABLE for anything else that prevented a
    definitive answer (missing binary, unusable cwd, auth/network error,
    unrecognized stderr).
    """
    try:
        out = subprocess.run(args, capture_output=True, text=True, check=True, cwd=cwd)
        return out.stdout
    except OSError:
        # Widened from FileNotFoundError when `cwd=` was added: a cwd that is
        # missing, empty, a file, or unreadable raises NotADirectoryError /
        # PermissionError, siblings of FileNotFoundError under OSError. All of
        # them are "could not ask", not "no MR" -- fail closed, never a traceback.
        return UNVERIFIABLE
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").lower()
        if any(marker in stderr for marker in NOT_FOUND_MARKERS[tool]):
            return NOT_FOUND
        return UNVERIFIABLE


def _extract_state(out: str) -> object:
    """Parse a forge CLI's JSON stdout into a state string.

    Guarantees the return is either UNVERIFIABLE or a non-empty str -- never
    lets a surprising JSON shape (not an object, "state" not a string) reach
    main()'s `state.lower()` as an uncaught AttributeError/TypeError.
    """
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return UNVERIFIABLE
    state = parsed.get("state") if isinstance(parsed, dict) else None
    return state if isinstance(state, str) and state else UNVERIFIABLE


def glab_state(ref: str, cwd: str) -> object:
    # No -R here: a bare `owner/repo` pins the path but not the host, so glab
    # resolves it against `glab config get host` and 404s on a self-hosted
    # origin (measured 2026-08-15, glab 1.110.0). The cwd is what both CLIs
    # actually resolve their project from.
    out = _run("glab", ["glab", "mr", "view", ref, "-F", "json"], cwd)
    if out is NOT_FOUND or out is UNVERIFIABLE:
        return out
    return _extract_state(out)


def gh_state(ref: str, slug: str | None, cwd: str) -> object:
    args = ["gh", "pr", "view", ref, "--json", "state"]
    if slug:
        args += ["--repo", slug]
    out = _run("gh", args, cwd)
    if out is NOT_FOUND or out is UNVERIFIABLE:
        return out
    return _extract_state(out)


def detect_state(ref: str, forge: str | None, slug: str | None, cwd: str) -> object:
    """Try glab then gh (or only the one named by `forge`).

    A definitive NOT_FOUND from one forge wins over an UNVERIFIABLE from the
    other -- e.g. glab confirms no MR exists while gh is simply missing from
    PATH must still resolve to "no MR found", not "could not verify".
    """
    results = []
    if forge in (None, "glab"):
        state = glab_state(ref, cwd)
        if state is not NOT_FOUND and state is not UNVERIFIABLE:
            return state
        results.append(state)
    if forge in (None, "gh"):
        state = gh_state(ref, slug, cwd)
        if state is not NOT_FOUND and state is not UNVERIFIABLE:
            return state
        results.append(state)
    return NOT_FOUND if NOT_FOUND in results else UNVERIFIABLE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ref")
    # Required, not defaulted: both CLIs resolve their project from the process
    # cwd, so an omitted value would silently answer about whatever directory
    # the caller happened to sit in -- the exact defect this flag closes. A
    # missing required argument is an argparse usage error, which exits 2 =
    # "could not verify" = STOP, matching the fail-closed contract above.
    parser.add_argument("--cwd", required=True, help="absolute path of the repo/worktree to ask about")
    # Accept both CLI-tool names and detect_remote.py's FORGE values (gitlab|github).
    # Any argparse usage error (bad --forge, missing ref) exits 2 -- same as our
    # own "could not verify" code, deliberately: a usage accident should fail
    # closed (STOP) exactly like a missing glab/gh binary would.
    parser.add_argument("--forge", choices=["glab", "gh", "gitlab", "github"])
    parser.add_argument("--slug")
    args = parser.parse_args(argv)
    # A relative --cwd resolves against the ambient process cwd, which is the
    # very defect the flag closes -- and it would do so silently. parser.error
    # exits 2, the same fail-closed STOP as an unverifiable state.
    if not os.path.isabs(args.cwd):
        parser.error(f"--cwd must be an absolute path, got {args.cwd!r}")

    forge = {"gitlab": "glab", "github": "gh"}.get(args.forge, args.forge)
    state = detect_state(args.ref, forge, args.slug, args.cwd)
    if state is UNVERIFIABLE:
        print("unverifiable")
        return 2
    if state is NOT_FOUND:
        print("none")
        return 3
    print(state)
    return 0 if state.lower() in OPEN_STATES else 1


if __name__ == "__main__":
    sys.exit(main())
