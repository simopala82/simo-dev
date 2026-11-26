#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""task-intake's repo pre-flight (step 3) and branch-and-worktree table (step 6), decided from git state.

  worktree_resolve.py resolve --dir D [--branch B --base BASE] [shared-check flags]
  worktree_resolve.py adopt --wt W --branch B --base BASE [--foreign-ok] [--old-remote-ok]

Prints `key: value` lines; `run:` (in order) and `foreign:` repeat. The caller runs every `run:`
line as printed and stops on the first non-zero. rc 0 decided, 1 `STOP: <reason>` (nothing to run),
2 bad args, 3 `NEED: shared-check`, 4 `ASK: <what>` (adopt only: nothing renamed; ask, then re-run with
the matching flag).

resolve
  One `git -C D rev-parse --show-toplevel --show-prefix --abbrev-ref HEAD --path-format=absolute
  --git-dir --git-common-dir` call (one call: separate reads can answer from different repos). Five lines
  and rc 0 or STOP: an unborn HEAD prints three and rc 128, and a positional read would make <REAL>
  `dirname ""` = `.`, a relative path that passes later tests (verified 2026-08-05, git 2.55.0).
  `--path-format=absolute` affects only the options after it. Lines: top, prefix (empty at the toplevel),
  headref (`HEAD` when detached; never `branch --show-current`, empty there), gitdir, common.
  <REAL> = dirname(common), the main checkout that owns the tree.

  Pre-flight STOPs (a different project, before anything is written):
  - <REAL> contains `.claude/plugins/cache/` or `.claude/plugins/marketplaces/`: a cache or marketplace
    copy with its own .git, or a worktree of one.
  - prefix under one of those: a git-less rsync copy; rev-parse walked up to an unrelated outer repo,
    so the toplevel carries no plugins/ segment.

  Facts printed: harness = top contains `/.claude/worktrees/` at any depth (all 7 live harness trees,
  2026-08-05: `.claude/worktrees/<r>`, doubled `.claude/.claude/...`, under `.claude/plugins/cache/**`,
  nested in another worktree; observed set). Never inferred from the branch: a release tree on a tag is
  detached too, and two branch-based designs hijacked such trees. canonical = dirname(top) ==
  <REAL>/.worktrees exactly (a prefix test would accept `<REAL>/.worktrees/<t>/.claude/worktrees/<r>`,
  inside the dir mr-followup removes at merge). desktop = `<gitdir>/claude-desktop-worktree` exists (the
  empty file the desktop app writes for a tree it created: 3/3 desktop trees, 0/3 canonical, 2026-10-19).
  submodule = common contains `/.git/modules/`.

  Rows, first match wins:
    1  gitdir == common: not a linked worktree (main checkout, also detached; submodule tree). Add a
       worktree, repo = top. Before the <REAL> assert, which a submodule's <REAL> fails.
    2  canonical: add a sibling, repo = <REAL>.
    3s harness + desktop + shared: STOP. Another session works on the same HEAD: a rename or rebase
       would move its branch (observed 2026-11-09).
    3a harness + desktop, not shared: adopt in place, wt = top. Never move it: the app's isolation hook
       keys on the original path and refuses every Edit after a move (2026-10-19), and refused one on a
       canonical tree the session made itself (2026-10-20).
    3b harness, no marker: move to <REAL>/.worktrees/<name>, then adopt.
    4  anything else: STOP. A linked worktree placed deliberately (release, bisect, hand-made).
  Only 3a and 3b rename, both on a harness path.

  <REAL> assert, rows 2-4: `git -C <REAL> rev-parse --show-toplevel` must print <REAL>. A worktree of a
  bare repo has <REAL> = the bare repo's parent, which need not be a checkout. Not
  `--is-bare-repository`: it answers from the nearest ancestor repo (`false` for a plain dir inside a
  working tree, verified 2026-08-05).

  Shared check (harness + desktop only; the caller reads the session tools): `--self-worktree P` is
  get_session("self").worktreePath ("" when absent), each `--linked-cwd` a list_sessions(linked) cwd,
  `--no-session-tools` when either tool is missing (fail safe: shared). Shared when P fails -ef against
  top, or any linked cwd passes it. `--adopt-anyway`: the user's "adopt anyway" for this tree: not shared.
  Without these flags: NEED, rc 3, no row.

  Without --branch (classify mode: steps 1, 3, 7.5, 8) only the pre-flight STOPs and 3s stop; row 4 and
  a failed <REAL> assert are printed for step 6. With --branch/--base (step 6) every stop applies and the
  row's commands print. Rows 1/2: `next: follow-up` when origin/<branch> exists (no commands, task-intake
  lib/worktree-rare-paths.md); else fetch, a local exclude when `.worktrees/` is not ignored (nothing to
  commit), `worktree add --no-track <repo>/.worktrees/<name> -b <branch> origin/<base>`: from the fresh
  remote ref (a stale local base reverts merged work, 2026-07-24) and with no upstream (one on origin/<base>
  makes a bare push target the base under push.default=upstream). Destination taken or local <branch>
  present: STOP (`worktree add` would fail 128). Row 3b: the same exclude, then the move, after a guard on the
  destination (onto an existing dir `worktree move` exits 0 and nests the tree one level deeper).

adopt (rows 3a and 3b, wt = resolve's `wt:`, after any move)
  Refuses a tree that is neither harness nor canonical, a canonical one not named <name> (only row 3b's
  move destination is adopted; another task's or a hand-placed tree is not), or not its own toplevel (`git -C` on a missing
  dir exits 128 with empty stdout, which ls-remote would read as "not pushed"). Then: current branch ==
  <branch> -> STOP (a retry after this task's own rename); fetch origin <base> and verify the ref, else
  STOP (an unfetched ref gives an empty range: "no foreign commits"); `foreign:` = origin/<base>..HEAD.
  Shape by the current branch:
    detached  -> `switch -c <branch>` (branch -m exits 128 on a detached HEAD)
    claude/*  -> ls-remote origin <it> (rc != 0 -> STOP; non-empty -> ASK old-remote: renaming opens a
                 second MR, and an MR's source branch cannot be retargeted); `branch -m <branch>` then
                 `branch --unset-upstream` guarded on branch.<new>.merge being set (a dangling upstream,
                 never pushed, fails `rev-parse @{u}` yet still points at the old name; branch -m carries
                 origin/claude/<r> over, verified 2026-07-28; the bare unset exits 128 with none)
    named     -> `next: merged-branch-path` (task-intake lib/worktree-rare-paths.md); foreign commits are
                 expected after a squash merge: listed, never asked
  Exactly one rename form: after `switch -c`, a `branch -m` is a silent no-op that leaves the claude/*
  branch behind. Local <branch> already present -> STOP (both forms exit 128). Foreign commits on a
  detached or claude/* tree -> ASK foreign-commits (they would ride into this task's MR). Then `next: park`.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

PLUGIN_COPY = (".claude/plugins/cache/", ".claude/plugins/marketplaces/")
HARNESS = "/.claude/worktrees/"
MARKER = "claude-desktop-worktree"


class Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def put(self, key: str, value: object) -> None:
        self.lines.append(f"{key}: {value}")

    def emit(self, rc: int) -> int:
        print("\n".join(self.lines))
        return rc

    def stop(self, reason: str) -> int:
        self.lines = [ln for ln in self.lines if not ln.startswith("run: ")]
        self.put("STOP", reason)
        return self.emit(1)


def git(path: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", path, *args], capture_output=True, encoding="utf-8", errors="surrogateescape")


def q(s: str) -> str:
    return shlex.quote(s)


def same(a: str, b: str) -> bool:
    try:
        return bool(a) and os.path.samefile(a, b)
    except OSError:
        return False


def preflight(path: str) -> tuple[dict | None, str]:
    if not os.path.isdir(path):
        return None, f"{path} is not a directory (a row-3b tree an earlier run moved? adopt its new path)"
    proc = git(path, "rev-parse", "--show-toplevel", "--show-prefix", "--abbrev-ref", "HEAD",
               "--path-format=absolute", "--git-dir", "--git-common-dir")
    lines = proc.stdout.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if proc.returncode != 0 or len(lines) != 5:
        return None, (f"rev-parse rc {proc.returncode}, {len(lines)} of 5 lines (unborn HEAD or not a repo): "
                      f"{proc.stderr.strip()[:200]}")
    top, prefix, headref, gitdir, common = lines
    real = os.path.dirname(common)
    return {
        "top": top, "prefix": prefix, "headref": headref, "gitdir": gitdir, "common": common, "real": real,
        "submodule": "/.git/modules/" in common,
        "linked": gitdir != common,
        "harness": HARNESS in top,
        "canonical": os.path.dirname(top) == os.path.join(real, ".worktrees"),
        "desktop": os.path.exists(os.path.join(gitdir, MARKER)),
    }, ""


def yn(flag: bool) -> str:
    return "yes" if flag else "no"


def put_facts(out: Out, f: dict) -> None:
    for key in ("top", "prefix", "headref", "gitdir", "real"):
        out.put(key, f[key])
    for key in ("submodule", "harness", "canonical", "desktop"):
        out.put(key, yn(f[key]))


def shared(args, top: str) -> bool | None:
    if args.adopt_anyway:
        return False
    if args.no_session_tools:
        return True
    if args.self_worktree is None:
        return None
    return not same(args.self_worktree, top) or any(same(c, top) for c in args.linked_cwd)


def row_of(f: dict, is_shared: bool | None) -> str | None:
    if not f["linked"]:
        return "1"
    if f["canonical"]:
        return "2"
    if f["harness"] and f["desktop"]:
        return None if is_shared is None else ("3s" if is_shared else "3a")
    return "3b" if f["harness"] else "4"


def name_of(branch: str) -> str:
    return branch.split("/", 1)[1] if "/" in branch else branch


def exclude_worktrees(out: Out, repo: str) -> None:
    if git(repo, "check-ignore", "-q", ".worktrees/probe").returncode != 0:
        exclude = git(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude").stdout.strip()
        e = q(exclude)
        out.put("run", f"mkdir -p {q(os.path.dirname(exclude))} && {{ [ ! -s {e} ] || [ -z \"$(tail -c1 {e})\" ] || echo >> {e}; }}"
                       f" && printf '%s\\n' '.worktrees/' >> {e}")


def add_commands(out: Out, repo: str, branch: str, base: str) -> int:
    dest = os.path.join(repo, ".worktrees", name_of(branch))
    out.put("repo", repo)
    out.put("wt", dest)
    remote = git(repo, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
    if remote.returncode == 0:
        out.put("next", "follow-up")
        return out.emit(0)
    if remote.returncode != 2:
        return out.stop(f"ls-remote origin {branch} rc {remote.returncode}: {remote.stderr.strip()[:200]}")
    if os.path.lexists(dest):
        return out.stop(f"{dest} already exists (this task's tree resumed, or another one): ask")
    if git(repo, "branch", "--list", branch).stdout.strip():
        return out.stop(f"local branch {branch} already exists: ask")
    out.put("run", f"git -C {q(repo)} fetch origin {q(base)} --quiet")
    exclude_worktrees(out, repo)
    out.put("run", f"git -C {q(repo)} worktree add --no-track {q(dest)} -b {q(branch)} {q('origin/' + base)}")
    out.put("next", "close")
    return out.emit(0)


def move_commands(out: Out, f: dict, branch: str) -> int:
    real = f["real"]
    dest = os.path.join(real, ".worktrees", name_of(branch))
    out.put("wt", dest)
    if os.path.lexists(dest):
        return out.stop(f"{dest} already exists: a `worktree move` onto it would nest the tree; ask")
    exclude_worktrees(out, real)
    out.put("run", f"mkdir -p {q(os.path.join(real, '.worktrees'))}")
    out.put("run", f"git -C {q(real)} worktree move {q(f['top'])} {q(dest)}")
    out.put("next", "adopt")
    return out.emit(0)


def cmd_resolve(args) -> int:
    out = Out()
    if (args.branch is None) != (args.base is None):
        print("--branch and --base go together", file=sys.stderr)
        return 2
    f, err = preflight(args.dir)
    if f is None:
        return out.stop(err)
    put_facts(out, f)
    if any(p in f["real"] for p in PLUGIN_COPY):
        return out.stop("<REAL> is a plugin cache/marketplace copy: a different project (skills/task-intake/lib/repo-preflight.md)")
    if any(p in f["prefix"] for p in PLUGIN_COPY):
        return out.stop("the cwd is a git-less rsync copy of a plugin cache/marketplace inside an unrelated repo "
                        "(skills/task-intake/lib/repo-preflight.md)")
    is_shared = shared(args, f["top"]) if (f["linked"] and not f["canonical"] and f["harness"] and f["desktop"]) else False
    row = row_of(f, is_shared)
    if row is None:
        out.put("NEED", "shared-check")
        return out.emit(3)
    real_ok = "n/a" if row == "1" else yn(git(f["real"], "rev-parse", "--show-toplevel").stdout.strip() == f["real"]
                                          if os.path.isdir(f["real"]) else False)
    out.put("real_ok", real_ok)
    out.put("row", row)
    if row == "3s":
        return out.stop("shared tree: another live session works in it (lib/shared-tree-stop.md)")
    if args.branch is None:
        return out.emit(0)
    if row == "4":
        return out.stop("a linked worktree neither canonical nor on a harness path: placed deliberately, ask")
    if real_ok == "no":
        return out.stop(f"<REAL> {f['real']} is not a checkout (worktree of a bare repo?): ask")
    if row == "1":
        return add_commands(out, f["top"], args.branch, args.base)
    if row == "2":
        return add_commands(out, f["real"], args.branch, args.base)
    if row == "3a":
        out.put("wt", f["top"])
        out.put("next", "adopt")
        return out.emit(0)
    return move_commands(out, f, args.branch)


def cmd_adopt(args) -> int:
    out = Out()
    wt = args.wt
    f, err = preflight(wt)
    if f is None:
        return out.stop(err)
    if f["top"] != wt:
        return out.stop(f"{wt} is not a worktree toplevel (toplevel {f['top']})")
    if not f["linked"] or not (f["harness"] or f["canonical"]):
        return out.stop("adopt runs only on a row-3a tree or a row-3b tree after its move")
    if not f["harness"] and os.path.basename(f["top"]) != name_of(args.branch):
        return out.stop(f"{wt} is a canonical tree named {os.path.basename(f['top'])}, not the row-3b destination "
                        f"for {args.branch}: not adopted")
    out.put("wt", wt)
    current = f["headref"]
    out.put("headref", current)
    if current == args.branch:
        return out.stop(f"already on {args.branch}: a retry after this task's own rename; report, rerun nothing")
    fetch = git(wt, "fetch", "origin", args.base, "--quiet")
    if fetch.returncode != 0:
        return out.stop(f"fetch origin {args.base} failed: a stale origin/{args.base} would hide foreign commits")
    if git(wt, "rev-parse", "--verify", "--quiet", f"origin/{args.base}").returncode != 0:
        return out.stop(f"origin/{args.base} unresolved")
    log = git(wt, "log", "--oneline", f"origin/{args.base}..HEAD")
    if log.returncode != 0:
        return out.stop(f"log origin/{args.base}..HEAD rc {log.returncode}")
    foreign = [ln for ln in log.stdout.splitlines() if ln]
    for ln in foreign:
        out.put("foreign", ln)
    if current == "HEAD":
        out.put("shape", "detached")
        rename = [f"git -C {q(wt)} switch -c {q(args.branch)}"]
    elif current.startswith("claude/"):
        out.put("shape", "claude")
        remote = git(wt, "ls-remote", "origin", current)
        if remote.returncode != 0:
            return out.stop(f"ls-remote origin {current} rc {remote.returncode}: not an answer")
        old_remote = bool(remote.stdout.strip())
        out.put("old_remote", yn(old_remote))
        rename = [f"git -C {q(wt)} branch -m {q(args.branch)}",
                  f"! git -C {q(wt)} config --get {q('branch.' + args.branch + '.merge')} >/dev/null"
                  f" || git -C {q(wt)} branch --unset-upstream"]
    else:
        out.put("shape", "named")
        out.put("next", "merged-branch-path")
        return out.emit(0)
    if git(wt, "branch", "--list", args.branch).stdout.strip():
        return out.stop(f"local branch {args.branch} already exists: both rename forms would exit 128; ask")
    asks = []
    if foreign and not args.foreign_ok:
        asks.append("foreign-commits")
    if current.startswith("claude/") and old_remote and not args.old_remote_ok:
        asks.append("old-remote")
    if asks:
        out.put("ASK", ",".join(asks))
        return out.emit(4)
    for line in rename:
        out.put("run", line)
    out.put("next", "park")
    return out.emit(0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--dir", default=".")
    r.add_argument("--branch")
    r.add_argument("--base")
    r.add_argument("--self-worktree")
    r.add_argument("--linked-cwd", action="append", default=[])
    r.add_argument("--no-session-tools", action="store_true")
    r.add_argument("--adopt-anyway", action="store_true")
    a = sub.add_parser("adopt")
    a.add_argument("--wt", required=True)
    a.add_argument("--branch", required=True)
    a.add_argument("--base", required=True)
    a.add_argument("--foreign-ok", action="store_true")
    a.add_argument("--old-remote-ok", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "resolve":
        args.dir = os.path.abspath(args.dir)
        return cmd_resolve(args)
    return cmd_adopt(args)


if __name__ == "__main__":
    sys.exit(main())
