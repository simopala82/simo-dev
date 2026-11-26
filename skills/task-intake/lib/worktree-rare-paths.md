# Step 6 rare paths: row 3b move failures, merged-branch path, existing-branch follow-up

Loaded on demand from `task-intake` `SKILL.md` step 6 when one of these fires. The row table, its definitions (`<TOP>`, `<REAL>`, `<GITDIR>`, `<HEADREF>`, `<WT>`) and the adopt checks are in `lib/worktree_resolve.py` (plugin root) and `SKILL.md` step 6.

## Row 3b: the move fails, or ran in an earlier run

`worktree_resolve.py resolve` prints the move (`mkdir -p`, `git -C "<REAL>" worktree move "<TOP>" "<REAL>/.worktrees/<name>"`) only after checking the destination is free: onto an existing directory `worktree move` exits 0 and registers the tree one level deeper (`.worktrees/<name>/<random>`), silent and wrong.

**A non-zero move is a stop, not a soft error.** Refusals observed on git 2.55.0: a main working tree (`fatal: … is a main working tree`), a **locked** worktree (`fatal: cannot move a locked working tree`) — both 128; `git-worktree(1)` also states a worktree containing submodules cannot be moved, and the move is a rename, so a cross-device destination fails too. Not a closed set — treat any non-zero as a stop. Continuing is what makes it dangerous: `<WT>` would name a directory that does not exist, and `git -C <missing-dir> ls-remote` exits 128 with **empty stdout**, which reads as "not on the remote" (verified 2026-08-05); `adopt` refuses a missing `<WT>` for that reason.

**Re-entry after a move** (an earlier run moved the tree, then stopped, e.g. at an `ASK:`): step 3's `<TOP>` is gone, so `resolve --dir "<TOP>"` stops on a missing directory. Check that `<REAL>/.worktrees/<name>` is this tree — `git -C "<REAL>/.worktrees/<name>" rev-parse --path-format=absolute --git-dir` prints `<GITDIR>` (a move keeps the git dir) — then run `adopt --wt "<REAL>/.worktrees/<name>"` directly. Any other git dir → a different worktree holds the name: stop and ask.

The move carries the branch, the commits, tracked modifications **and** untracked files, and deregisters the old path (verified 2026-08-04, git 2.55.0) — nothing to stash for the move itself. It succeeds even when the session's cwd is inside `<TOP>`. Afterwards any later `git -C` on the stale `<TOP>` literal fails loudly (`fatal: cannot change to …`). The silent half is the opposite: a **bare** command still works, because the shell's cwd follows the rename, while `pwd` keeps printing the old path and only `pwd -P` shows the new one (verified 2026-08-05, macOS). So a bare `git status` after a move reports the moved tree under a name that no longer exists.

## Untracked `.claude/worktrees/` in the main checkout

Read when a main checkout shows an adopted (row 3a) tree as untracked. `.claude/worktrees/` is excluded via `.git/info/exclude`, local and uncommitted, so a fresh clone lacks it: add the exclusion there.

## Merged-branch path

Named `<HEADREF>`, neither `claude/*` nor `<branch>`. `origin/<base>` is already fresh from `adopt`'s fetch (`SKILL.md` step 6); all guards in one call:
```bash
printf '%s\n' "<HEADREF>" | grep -Eq '^(feat|fix|chore|refactor|docs|test|perf)/'; echo "task-name rc=$?"  # MUST be 0
[ "<HEADREF>" != "<base>" ]; echo "not-base rc=$?"                                      # MUST be 0
# merged=ancestor: HEAD is on base. merged=content: some first-parent commit C of base since the fork
# already held all of HEAD's content (merging HEAD into C is clean and changes nothing): a squash or
# rebase merge, whatever base did after C. merged=no: no such C (or an empty walk) -> STOP.
# A merge driver could fake a clean merge, so no path may carry a `merge` attribute: in-tree attributes
# are ignored (--attr-source empty tree), so are the global (core.attributesFile) and system
# (GIT_ATTR_NOSYSTEM) files, and a non-empty info/attributes stops. merge.default=text closes the
# one selector left.
if git -C "<WT>" merge-base --is-ancestor HEAD "origin/<base>"; then echo "merged=ancestor"
elif git -C "<WT>" rev-list --first-parent "origin/<base>" "^$(git -C "<WT>" merge-base HEAD "origin/<base>")" \
  2>/dev/null | ( E=$(git -C "<WT>" hash-object -t tree /dev/null); export GIT_ATTR_NOSYSTEM=1
      [ -s "$(git -C "<WT>" rev-parse --path-format=absolute --git-path info/attributes)" ] \
        && { echo "merged=no cause: info/attributes non-empty" >&2; exit 1; }
      while read -r C; do
        MT=$(git -C "<WT>" -c core.attributesFile=/dev/null -c merge.default=text --attr-source="$E" \
              merge-tree --write-tree "$C" HEAD) \
          && [ "$MT" = "$(git -C "<WT>" rev-parse "${C}^{tree}")" ] && exit 0
      done; exit 1 ); then echo "merged=content"
else echo "merged=no"; fi                                                               # MUST be ancestor or content
git -C "<WT>" ls-remote --exit-code origin "<branch>" >/dev/null; echo "remote rc=$?"  # MUST be 2
git -C "<WT>" branch --list "<branch>"                                                  # MUST print nothing
git -C "<WT>" status --porcelain --untracked-files=all                                  # MUST list only this run's step-7 plan/spec files
```
The first two keep a long-lived branch (`<base>`, `develop`, a release branch) out: only a task-shaped name (this step's own `<type>/` rule — keep the regex in sync with the `<type>` list in `SKILL.md` step 6 "Branch name:") is renamed and later deleted. The `merged=` verdict has three values. `ancestor`: the old branch has no commits of its own beyond base; it proves ancestry, not that an MR merged. `content`: some first-parent commit C of `origin/<base>` since the fork point gives a clean merge-tree with HEAD whose tree equals C's tree, so all of HEAD's content was on base at C. It covers squash and rebase merges (the normal case where MRs squash on merge; observed 2026-11-23 on a squash-merged branch: ancestry rc 1) and any later base change, including edits to the same lines or removal of a file the branch added; it proves the content landed on base, not that an MR merged nor that it is still there. `no` → stop. The walk, not base's tip: when every MR carries a shared change such as a release bump, the tip conflicts with HEAD on those lines (measured 2026-11-24: 3 of 3 recent squash-merged branches of one repo), while the walk finds each branch at its own merge commit. The safe direction: an equal tree at C means merging HEAD there adds nothing, so no false pass; the block comment says how a merge driver is kept from faking one. An empty walk range (old squash-merged branch that later merged `origin/<base>`, base not advanced since; unrelated histories) also gives `no`: safe, but `no` does not always mean missing content. Git older than 2.40 (`--attr-source`) rejects the call and also gives `no`. Remote rc 2 keeps a follow-up out: an existing `origin/<branch>` is the same open gap step 7.5's `follow-up` bypass names. The status listing catches what ancestry cannot: a previous task's leftover uncommitted or untracked files (post-merge keeps a desktop tree that holds uncommitted changes), which `SKILL.md` step 6's park block would otherwise carry into this task's branch; step 7's plan/spec files are expected there. Any guard failing (`merged=no` included) → stop and report; the user decides, and a new session started from the repo root is the clean path. All pass:
```bash
git -C "<WT>" switch --no-track -c "<branch>" "origin/<base>"   # never from HEAD; untracked plan/spec files ride along
```
`--no-track` because under `branch.autoSetupMerge=inherit` or `always` the new branch would otherwise track the old branch or its upstream, so a bare push would target the wrong name (reproduced on git 2.56.0, 2026-10-20). The new branch starts from `origin/<base>` in both verdicts, so the old branch's commits never reach it and step 6's park-block rebase is a no-op on that fresh base. A non-zero switch (e.g. an untracked plan/spec path that `origin/<base>` tracks) is a stop; nothing was renamed yet. Then skip the rename (the `branch --list` guard already ran that check) and run `SKILL.md` step 6's park block as usual (park commits the untracked plan). After step 6's park block, delete the old branch: `git -C "<WT>" branch -d "<HEADREF>"` on `merged=ancestor`; `git -C "<WT>" branch -D "<HEADREF>"` on `merged=content` only, because `-d` refuses a squash-merged branch ("not fully merged") and `content` already proved its content landed on base. Post-merge's desktop-tree branch leaves it in place ("leaving it and branch <name> in place"), so without this it accumulates. A stop after the switch (park rc 2 or 5, or a retry that meets the `adopt` STOP already on `<branch>`) must say in its report that the old branch, `adopt`'s `headref:` literal (`<HEADREF>`) **and its verdict**, is still to delete. If that literal is lost: `git -C "<WT>" branch --merged "origin/<base>"` finds it only on `merged=ancestor`; on `merged=content` it never lists it, so look among task-shaped local branches and delete with `-D` only after the guard's `merged=content` walk, run with `"<name>"` in place of `HEAD` (both in the `merge-base` and in the `merge-tree` call), succeeds. Step 3's Explore and the plan ran against the old, older tree, so their citations may be stale; `simo-dev:plan-execute` step 1 "Load and review" is where that is caught.

## Follow-up on an EXISTING branch/MR

**Follow-up on an EXISTING branch/MR — re-verify right before creating the worktree** (idempotent; the step-3 pre-flight ran too early — the race is mid-intake). A concurrent merge can delete the target branch between the first observation and now. Check the **remote** branch — it is authoritative for a live MR, and a merged MR's local `refs/heads/<branch>` can survive a remote delete (`fetch --prune` never removes local branches):

```bash
git ls-remote --exit-code origin "<branch>"   # exit 2 → remote branch gone
```

- Gone → MR merged/closed mid-intake: **pivot to a fresh branch off base** (new MR, `SKILL.md` step 6's `--no-track ... -b <branch> origin/<base>` form), do NOT fold onto the dead branch. `git -C <pruned-dir>` silently resolves to the parent repo, so a `git worktree add` failing on the missing ref — not step 6's toplevel assert — is the only signal without this check. Target code already read from a pre-pivot snapshot of the dead branch → re-read it from the real base after the pivot.
- Live → fetch first, then create the worktree on the existing branch: `git fetch origin <branch> --quiet && git worktree add <repo>/.worktrees/<name> <branch>` (no `-b`, not `SKILL.md` step 6's new-branch form). `ls-remote` checked existence, not freshness — a stale local `<branch>` lags the MR head after concurrent pushes, so fast-forward it to `origin/<branch>` before working.

If the worktree's plan (`docs/plans/...`) has a `Depends on: <TICKET>` field, resuming this branch/worktree is not enough on its own — a stale worktree can still compile after its dependency merged with a different shape (incident 2026-08-05). That verification runs in `simo-dev:plan-execute` step 1, not here; a green build is not proof the dependency is current.
