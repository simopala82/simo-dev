#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
r"""simo-dev:mr-followup step 1 (detect), step 6's guards (review-gate), step 7 item 6 (push).

  mr_followup.py detect      --repo W [--view-only] [--number N]
  mr_followup.py review-gate --repo W --base B [--round1 --session S]
  mr_followup.py push        --repo W --forge gitlab|github --iteration N

Stdout is `key: value` lines only (`note:`, `warning:`, `ahead:`, `drops:`, `candidate:` repeat);
sibling scripts' and forge CLIs' stderr passes through. --repo: as lib/mr_create.py (an absolute
worktree toplevel on a branch, else STOP before git or the forge is asked). Shared with that script:
repo/branch resolution, forge detection, the MR/PR view, the CodeReview transition and the
local-base-ahead check. Every glab/gh call runs with cwd=W and no -R (mr_create.py docstring).

detect  rc 0 done; 1 STOP; 2 bad args (also python's rc when the plugin root was unbound and this
        file cannot be opened: re-issue the block); 3 ASK; 4 STOP: no MR/PR (nothing to follow up).
  1. `forge:`, `slug:`, `api_host:`, `branch:` (detect_remote.detect; unknown forge -> STOP).
  2. View: `glab mr view <branch> -F json` / `gh pr view <branch> --json ...`. A failed view proves
     nothing (expired token, network, wrong cwd all fail alike), so check_mr_state.py decides:
     no MR/PR -> rc 4 with the create hint; anything but an answer (missing guard, empty stdout,
     rc 2) -> STOP "NOT verified"; an answer -> the first call was transient: re-view once, a second
     failure -> STOP. Source/head branch other than <branch> -> ASK (the CLI resolved another MR).
     GitHub: more than one same-repo open PR for the head (`gh pr list --head`; cross-repository
     PRs from a fork with a same-named branch are ignored) -> `candidate:` lines + ASK; a failed list
     only warns. --number N (the IID / PR number the user picked after an ASK) views that MR/PR
     instead of <branch> and skips the second-PR check, so the re-run prints the fields an ASK
     withheld; its source branch name must still be <branch> (else STOP: this worktree pushes
     <branch>, which never reaches that MR/PR; a fork PR with the same branch name is not told
     apart here) and a number that does not exist STOPs. Later steps still look the MR/PR up by
     <branch> (the push guard, the head-SHA read): with several open PRs on one head they may answer
     about another of them, which only matters for the state check - all share the head branch.
     A branch named with digits only STOPs in detect and push: glab/gh read it as an MR/PR number,
     so every branch-keyed lookup would answer about an unrelated MR/PR.
  3. `base:`, `iid:` (GitLab) or `pr:` (GitHub), `url:`, `draft: yes|no`, `state:`, `open:
     yes|no|unknown`. GitLab: check_mr_state.py is the authority (the view's state is
     informational): empty stdout, a missing guard or an unknown rc -> `warning:` NOT verified and
     `open: unknown`, never a fabricated state; rc 1 -> `open: no`. GitHub: the view's state.
     Warn-only: the push guard (push below) is what stops.
  4. CodeReview transition of derive_ticket_key.py's key (mr_create.codereview: `transition:` or
     `warning:`, never a stop) - catches MRs opened by hand after the push.
  5. Unless `open: no`: `git fetch origin --prune`, stack_record.py check --base <base>. rc 0 ->
     record --target <base> (refreshes the fork point; clears the record when <base> is the default
     branch), `stack: ok`; rc 1 -> its `rebase:`/`drops:` lines, `stack: parent-merged` (the caller
     asks before rebasing, lib/parent-merged.md); other rc -> `warning:`, `stack: unverified`.
  --view-only (the Merge entry, lib/merge-entry.md) stops after 3: no transition, no stack check.

review-gate  rc 0 done; 1 STOP; 2 bad args.
  `git fetch origin <B>` (failure warns), then the local-base-ahead check of mr_create.py prepare:
  local refs/heads/<B> an ancestor of HEAD with commits not on origin/<B> -> `ahead:` lines + STOP,
  no bypass. No local <B> -> safe (nothing local to be ahead). Then `local_review:`: `dispatch`
  without --round1; with it, the review-record check of commit step 1 on the branch diff:
  `git diff -z --name-only --diff-filter=d origin/<B>...HEAD` (deleted paths have no mtime, and
  once committed review_marker.py no longer sees them as deletions) -> review_marker.py check
  --session S --files ... -> `skip (covered by review-loop)` on rc 0, else `dispatch (<why>)`:
  listing failed, no file left (an all-deletions diff), or the check's rc; its stdout becomes `note:`.

push  rc 0 pushed; 1 STOP MR/PR not open; 2 STOP the guard never ran or never answered (also bad
      args, or this file unreachable); 3 STOP the guard answered "could not verify"; 4 STOP push rejected.
  check_mr_state.py <branch> --forge F --cwd W; the file missing or empty stdout -> rc 2 before any
  rc is read (a truncated script exits 0 with empty stdout, which would read as open); rc 0 (open)
  or 3 (not found, warned: unexpected mid-loop) -> `git push -u origin <branch>` (never a force push),
  `pushed:`, then flow_telemetry.py event --type retry iteration=N (`retry:`, failure warns); rc 1
  -> rc 1, nothing pushed (pushing after a mid-session merge recreates the deleted branch as an
  orphan, incident 2026-07-17); any other rc -> rc 3. A rejected push -> rc 4: fetch, rebase, retry.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import mr_create as mc
from mr_create import Stop, put

LIB = mc.LIB


class Ask(Stop):
    key = "ASK"

    def __init__(self, reason: str) -> None:
        super().__init__(reason, 3)


def guard_state(repo: str, branch: str, tool: str) -> tuple[str, int, str]:
    """check_mr_state.py's verdict: ("missing"|"silent"|"answer", rc, state)."""
    guard = LIB / "check_mr_state.py"
    if not guard.is_file():
        return "missing", 0, str(guard)
    res = mc.sib("check_mr_state.py", branch, "--forge", tool, "--cwd", repo)
    state = (res.stdout or "").strip()
    return ("answer" if state else "silent"), res.returncode, state


def numeric_branch(branch: str) -> None:
    if branch.isdigit():
        raise Stop(f"branch '{branch}' is digits only: glab/gh read it as an MR/PR number, so every lookup by branch "
                   "would answer about an unrelated MR/PR — rename the branch")


def first_view(repo: str, kind: str, branch: str, ref: str, slug: str, noun: str, tool: str):
    view = mc.view_fields(repo, kind, ref)
    if view is not None:
        return view, None
    verdict = guard_state(repo, ref, tool)
    how, rc, state = verdict
    if how == "answer" and rc == 3 and ref != branch:
        raise Stop(f"{noun} {ref} not found on `{slug}` — the picked number is wrong; ask again")
    if how == "answer" and rc == 3:
        create = "glab mr create" if kind == "gitlab" else "gh pr create"
        raise Stop(f"No {noun} for branch `{branch}` on `{slug}`. To open one: `{create}`, then re-invoke this skill.", 4)
    if how != "answer" or rc not in (0, 1):
        why = {"missing": f"guard unreachable at {state}", "silent": f"rc {rc}, no output: the helper never answered"}
        raise Stop(f"{noun} state NOT verified for '{branch}' — {why.get(how, f'rc {rc}, reported {state!r}')}; "
                   "the view failed too (stderr above), so nothing was established")
    view = mc.view_fields(repo, kind, ref)
    if view is None:
        raise Stop(f"{tool} {'mr' if kind == 'gitlab' else 'pr'} view failed twice for '{branch}' (stderr above) "
                   f"while the {noun} exists — re-invoke once the forge answers")
    put("note", f"the first {tool} view call failed and was retried once (the state check reached the {noun})")
    return view, verdict


def gitlab_open(repo: str, branch: str, ref: str, iid: str, verdict) -> str:
    how, rc, state = verdict or guard_state(repo, ref, "glab")
    if how == "missing":
        put("warning", f"MR state NOT verified — guard unreachable at {state}. State unknown: restart the session.")
    elif how == "silent":
        put("warning", f"MR state NOT verified for '{branch}' — rc {rc}, no output: the helper never answered.")
    elif rc == 0:
        return "yes"
    elif rc == 1:
        put("warning", f"MR !{iid} for branch '{branch}' is in state '{state}' (expected 'opened').")
        return "no"
    elif rc == 3:
        put("warning", f"no MR found for branch '{branch}' by the state check — nothing to follow up on yet.")
    else:
        put("warning", f"MR state NOT verified for '{branch}' — rc {rc}, reported '{state}'.")
    return "unknown"


def one_open_pr(repo: str, branch: str) -> None:
    res = mc.forge(repo, "gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number,url,isCrossRepository")
    try:
        prs = json.loads(res.stdout) if res is not None and res.returncode == 0 else None
    except json.JSONDecodeError:
        prs = None
    if not isinstance(prs, list):
        mc.relay(res)
        put("warning", "gh pr list failed — could not check for a second open PR on this head (continuing)")
        return
    prs = [pr for pr in prs if isinstance(pr, dict) and pr.get("isCrossRepository") is not True]
    if len(prs) > 1:
        for pr in prs:
            put("candidate", f"#{pr.get('number')} {pr.get('url')}")
        raise Ask(f"{len(prs)} open PRs for '{branch}' — ask which one to follow, then re-run detect with --number <its number>")


def stack(repo: str, base: str) -> None:
    if mc.git(repo, "fetch", "origin", "--prune", "--quiet").returncode != 0:
        put("warning", "git fetch origin failed — the stack check reads possibly stale refs (continuing)")
    res = mc.sib("stack_record.py", "check", "--repo", repo, "--base", base)
    if res.returncode == 1:
        for line in (res.stdout or "").splitlines():
            key, sep, value = line.partition(": ")
            put(key, value) if sep and key in ("rebase", "drops") else put("note", line)
        put("stack", "parent-merged")
        return
    if res.returncode != 0:
        put("warning", f"stack_record.py check failed (rc {res.returncode}) — a parent merged rewritten would go "
                       "unnoticed (continuing)")
        put("stack", "unverified")
        return
    rec = mc.sib("stack_record.py", "record", "--repo", repo, "--target", base)
    if rec.returncode != 0:
        put("warning", f"stack_record.py record failed (rc {rec.returncode}) — the stacked-parent record was not "
                       "refreshed (continuing)")
    put("stack", "ok")


def detect(repo: str, branch: str, view_only: bool, number: str) -> int:
    found = mc.detect_remote(repo)
    if not found:
        raise Stop("could not detect the forge from origin (detect_remote stderr above)")
    kind, slug, api_host = found
    put("forge", kind)
    put("slug", slug)
    put("api_host", api_host)
    put("branch", branch)
    noun, tool = ("MR", "glab") if kind == "gitlab" else ("PR", "gh")
    numeric_branch(branch)
    ref = number or branch
    view, verdict = first_view(repo, kind, branch, ref, slug, noun, tool)
    mark = "!" if kind == "gitlab" else "#"
    if number and view["source"] != branch:
        raise Stop(f"{noun} {mark}{number}'s source branch is '{view['source']}', not the local branch '{branch}' — "
                   f"this worktree cannot push to it: follow it from a checkout of '{view['source']}'")
    if number:
        put("note", f"following {noun} {mark}{number} as picked; no second-PR check")
    elif view["source"] != branch:
        raise Ask(f"{tool} resolved {noun} {mark}{view['number']} with source branch '{view['source']}', not "
                  f"'{branch}' — ask which {noun} to follow; only one whose source branch is '{branch}' can be "
                  "followed from here: re-run detect with --number <it>")
    if kind == "github" and not number:
        one_open_pr(repo, branch)
    if not view["base"]:
        raise Stop(f"the {noun} view of '{branch}' carries no target branch — cannot follow up")
    put("base", view["base"])
    put("iid" if kind == "gitlab" else "pr", view["number"])
    put("url", view["url"])
    put("draft", "yes" if view["draft"] else "no")
    put("state", view["state"])
    if kind == "gitlab":
        is_open = gitlab_open(repo, branch, ref, view["number"], verdict)
    elif view["state"].upper() == "OPEN":
        is_open = "yes"
    else:
        put("warning", f"PR #{view['number']} for branch '{branch}' is in state '{view['state']}' (expected 'OPEN').")
        is_open = "no"
    put("open", is_open)
    if view_only:
        return 0
    mc.codereview(mc.sib("derive_ticket_key.py", branch, "--cwd", repo).stdout.strip())
    if is_open == "no":
        put("stack", "skipped (MR not open)")
    else:
        stack(repo, view["base"])
    return 0


def review_gate(repo: str, base: str, round1: bool, session: str) -> int:
    if mc.git(repo, "fetch", "origin", base).returncode != 0:
        put("warning", f"git fetch origin {base} failed — origin/{base} may be stale (continuing)")
    ahead = mc.local_base_ahead(repo, base)
    if ahead:
        for line in ahead:
            put("ahead", line)
        raise Stop(f"local base ahead: local '{base}' is ahead of origin/{base}, and those commits are in this "
                   "branch's ancestry — they would show up in the review diff as unrelated changes. No bypass: ask "
                   f"before pushing the local '{base}', never unasked; once pushed, re-run")
    if not round1:
        put("local_review", "dispatch")
        return 0
    listing = subprocess.run(["git", "-C", repo, "diff", "-z", "--name-only", "--diff-filter=d", f"origin/{base}...HEAD"],
                             capture_output=True)
    if listing.returncode != 0:
        sys.stderr.write(listing.stderr.decode(errors="replace"))
        put("local_review", f"dispatch (listing failed rc={listing.returncode})")
        return 0
    files = [os.fsdecode(f) for f in listing.stdout.split(b"\0") if f]
    if not files:
        put("local_review", "dispatch (no file left to check)")
        return 0
    res = mc.sib("review_marker.py", "check", "--repo", repo, "--session", session, "--files", *files)
    for line in (res.stdout or "").splitlines():
        put("note", line)
    put("local_review", "skip (covered by review-loop)" if res.returncode == 0
        else f"dispatch (review_marker rc={res.returncode})")
    return 0


def push(repo: str, branch: str, forge: str, iteration: int) -> int:
    numeric_branch(branch)
    lost = "Pushing blind risks recreating a merged branch as an orphan. The fix commit is local."
    how, rc, state = guard_state(repo, branch, forge)
    if how == "missing":
        raise Stop(f"could not verify MR state for '{branch}' — the guard is not at {state}, so it never ran: the plugin "
                   f"dir is incomplete, restart the session to rebind the plugin root. {lost}", 2)
    if how == "silent":
        raise Stop(f"could not verify MR state for '{branch}' — the guard exited rc {rc} but printed nothing, so it "
                   "never answered: a crashing or truncated check_mr_state.py (a crash leaves a line on stderr above, "
                   f"a truncation nothing), not a bad token. {lost}", 2)
    if rc == 1:
        raise Stop(f"MR/PR for '{branch}' is now '{state}' (not open) — likely merged/closed mid-session; pushing would recreate "
                   f"'{branch}' as an orphan. The fix commit is local, ready to cherry-pick onto a fresh branch.", 1)
    if rc not in (0, 3):
        raise Stop(f"could not verify MR state for '{branch}' (rc {rc}, '{state}': glab/gh missing, bad token, network "
                   f"down or a transient forge error). {lost} Re-run once the guard can verify again.", 3)
    if rc == 3:
        put("warning", f"no MR found for '{branch}' mid-loop — an MR should exist by now.")
    res = subprocess.run(["git", "-C", repo, "push", "-u", "origin", branch], capture_output=True, text=True)
    sys.stderr.write(res.stdout + res.stderr)
    if res.returncode != 0:
        raise Stop(f"git push rejected (rc {res.returncode}, git's error above) — fetch, rebase onto origin/{branch} "
                   "and retry the round; never force-push", 4)
    put("pushed", branch)
    event = mc.sib("flow_telemetry.py", "event", "--type", "retry", "--cwd", repo, "--field",
                   "skill=simo-dev:mr-followup", "--field", f"iteration={iteration}")
    if event.returncode != 0:
        put("warning", f"flow_telemetry.py event failed (rc {event.returncode}, continuing)")
    else:
        put("retry", iteration)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("detect", "review-gate", "push"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        if name == "detect":
            p.add_argument("--view-only", action="store_true")
            p.add_argument("--number", type=int)
        if name == "review-gate":
            p.add_argument("--base", required=True)
            p.add_argument("--round1", action="store_true")
            p.add_argument("--session", default="")
        if name == "push":
            p.add_argument("--forge", required=True, choices=["gitlab", "github"])
            p.add_argument("--iteration", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        repo, branch = mc.resolve_repo(args.repo)
        if args.cmd == "detect":
            return detect(repo, branch, args.view_only, "" if args.number is None else str(args.number))
        if args.cmd == "review-gate":
            return review_gate(repo, args.base, args.round1, args.session)
        return push(repo, branch, args.forge, args.iteration)
    except Stop as stop:
        put(getattr(stop, "key", "STOP"), stop.reason)
        return stop.rc


if __name__ == "__main__":
    sys.exit(main())
