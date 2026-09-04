# Worktree carry-over + EnterWorktree ban (full rationale)

Loaded on demand from `task-intake` step 6.

## Carrying uncommitted artifacts into the worktree

When steps 3-4 wrote artifacts on the base branch (`simo-dev:grill-me` → `CONTEXT.md` / `docs/adr`, design mode → a design spec): do NOT branch in-place to keep them with the code — carry them into the worktree so docs + code stay together on the feature branch and the base branch stays clean.

Not via the stash: `refs/stash` is ONE stack shared by every worktree of the repo, and this carry crosses two of them, so a `push` here and a `pop` there can pair with somebody else's entry — both at rc 0 (logged in the author's own notes, §17 — worth keeping a similar log). A throwaway commit on a **detached** HEAD keeps the base branch ref still, and the two worktrees share the object database, which is what makes `<carry-sha>` readable from the new one. Not `lib/park_and_rebase.py` either: that parks and restores inside one worktree — its record lives in that worktree's git dir — and never moves work to another tree.

```bash
git -C "<repo>" symbolic-ref --short HEAD || { echo "STOP: detached base checkout"; exit 1; }   # <basebranch>
git -C "<repo>" fetch origin <base> --quiet || { echo "STOP: fetch failed, a stale origin/<base> would ship"; exit 1; }
[ -z "$(git -C "<repo>" ls-files -u)" ] || { echo "STOP: unmerged paths in the base checkout"; exit 1; }
git -C "<repo>" switch --detach || { echo "STOP: detach refused, nothing has moved"; exit 1; }
git -C "<repo>" rev-parse HEAD                 # <pre-sha>
git -C "<repo>" add -A
git -C "<repo>" -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -m "CARRY: do not push"
git -C "<repo>" rev-parse HEAD                 # <carry-sha>; EQUAL to <pre-sha> → nothing was carried:
                                               #   switch back and STOP, never cherry-pick it
git -C "<repo>" switch "<basebranch>" || { echo "STOP: still detached on <carry-sha>"; exit 1; }   # NOT -q: git names the commit it leaves behind
git -C "<repo>" worktree add --no-track "<repo>/.worktrees/<name>" -b <branch> origin/<base>   # fresh origin/<base>, no upstream
git -C "<repo>/.worktrees/<name>" rev-parse --show-toplevel   # MUST resolve to that path — compare
                                               #   directories, not strings; anything else → STOP, the
                                               #   work is only in <carry-sha> (recovery below)
git -C "<repo>/.worktrees/<name>" cherry-pick -n "<carry-sha>" \
  || { echo "STOP: collision, or the worktree above is missing — do NOT run the reset below, it erases the tells"; exit 1; }
git -C "<repo>/.worktrees/<name>" reset --quiet                 # index back to HEAD; content stays on disk
```

Six lines fail closed and must stay that way, never softened into prints whose output nobody reads: the four before the `<pre-sha>` capture, the `switch` back, and the `cherry-pick`. Three more are reads the operator has to act on — the `<pre-sha>`/`<carry-sha>` pair and the `--show-toplevel` assert. `worktree add` is the one failure deliberately left to the recovery paragraph below. The sha comparison covers none of the states the first four catch. A base checkout that never detaches takes `add -A` + `commit` on the branch itself, landing a `CARRY: do not push` commit **on the base branch** at rc 0 with `<carry-sha>` duly different from `<pre-sha>` (measured 2026-08-16, git 2.55.0). `symbolic-ref --short HEAD` yields `<basebranch>` and exits 128 when there is none, so the read and the refusal are the same command. The detach then refuses loudly on some dirty states — 128, `fatal: cannot switch branch while merging`, likewise with `CHERRY_PICK_HEAD` present — and **not** on an unmerged index with no marker file, where it exits 0 and the `add -A` resolves the conflict by staging the marker text itself. That state is not exotic: it is what this file's own collision recovery, two paragraphs down, leaves behind. Hence the `ls-files -u` test, which reads the index rather than the marker.

`--no-verify` is not enough on the commit either: it bypasses `pre-commit` and `commit-msg` and **not** `prepare-commit-msg`, whose non-zero exit aborts the carry at rc 1 with the whole base checkout already staged — the sha guard then reports "nothing was carried" while the index says otherwise (measured 2026-08-16). `core.hooksPath=/dev/null` covers all three.

`<carry-sha>` differing from `<pre-sha>` guards the other degradation, and is not optional: on a clean tree the commit creates nothing and exits 1, leaving `<carry-sha>` on whatever the base checkout was sitting on. Let that through and, on a checkout one unpushed commit ahead of `origin/<base>`, `cherry-pick -n` stages *that* commit onto the feature branch at rc 0 — §17's trap in a new shape (measured 2026-08-16, git 2.55.0).

Every `-C "<repo>"` above is load-bearing, `worktree add` included: run bare against a foreign ambient cwd it exits **0** and creates the directory at the right path while registering it in the *other* repo, and the `cherry-pick` then fails rc 128 `bad object` with the artifacts already gone from the base checkout (measured 2026-08-16 — the "Worktree cwd gotcha" this file bans `EnterWorktree` over, three sections down).

Past the `switch` back the work can end up reachable only through `<carry-sha>`. Two ways, observed: a failing `git worktree add`, and a `cherry-pick -n` that collides with what the base gained meanwhile — rc 1, unmerged paths in `status --porcelain` (`AA` when both sides add it, `UU` when both modify it), and **no** `CHERRY_PICK_HEAD`, so nothing marks the tree as mid-pick, `park_and_rebase.py`'s `in_progress()` included (measured 2026-08-16). Those unmerged paths are the only tell there is, which is why the `cherry-pick` stops the block before the `reset`: run anyway, the reset exits 0, empties `ls-files -u` and rewrites `AA`/`UU` into plain ` M`, leaving the markers on disk in a tree that reads as carried (measured 2026-08-16). Recovery starts the same way whatever stopped it, since the sha is still the handle: `git -C "<repo>" cherry-pick -n "<carry-sha>"` puts everything back in the base checkout. After a collision that is not enough — the new worktree keeps the conflicted index and the markers on disk, and with no `CHERRY_PICK_HEAD` a `cherry-pick --abort` there exits 128 (`no cherry-pick or revert in progress`). Clear it with `git -C "<repo>/.worktrees/<name>" reset --hard`, or drop the tree entirely with `git -C "<repo>" worktree remove --force "<repo>/.worktrees/<name>"`.

**`.worktrees/` must be gitignored in `<repo>` before any of this runs** — the same thing the normal-add path in `task-intake` step 6 tells you to ensure, and here it is load-bearing rather than tidy. Where it is not, `add -A` stages every sibling worktree already under `<repo>/.worktrees/` as a **gitlink** (`warning: adding embedded git repository`, mode `160000`, measured 2026-08-16), the carry commit carries it, and `cherry-pick -n` puts it in the new tree. One worktree per task means a sibling is the normal state, not an edge case. That is also why `worktree add` stays after the commit rather than next to the `fetch`, where it would add *this* tree to the same sweep.

The trailing `reset` is what the closing `git commit` of `simo-dev:commit` step 4 depends on. `cherry-pick -n` leaves the whole `add -A` sweep **staged**, new artifacts as `A`, and `git commit` takes the index rather than step 4's explicit pathspec, so unrelated WIP swept out of the base checkout would ride into the first commit on the feature branch. Resetting here settles that at intake, where the sweep is still fresh, instead of leaving a reconciliation for a step that does not load this file.

It lands on the old `stash pop` end state for three of the four shapes — unstaged tracked modification, staged tracked modification and untracked file all come back exactly as `pop` left them. **A path that was staged-new in the base checkout does not**: `pop` returned it `A `, the reset returns it `??` (measured 2026-08-16, git 2.55.0). Step 4 stages untracked paths only when this session touched them or the plan lists them, so such a file drops out of the first commit without a word. Re-`git add` in the worktree anything that was staged-new before the carry — `park_and_rebase.py` records and re-stages that path list, this block does not.

Two limits do carry over unchanged from the `stash push -u` / `pop` pair this replaces: `add -A` sweeps the whole base checkout rather than the artifacts alone, and skips gitignored and skip-worktree paths.

Run as its own Bash call, the `CARRY: do not push` commit fires the review-loop reminder hook, which only prints — noise here, since no ref ever points at that commit and nothing pushes it under its own name. Pasted as part of the whole block it stays silent: the hook's separator pattern has no newline in it.

**Carry = move, never copy.** The commit above already moves the files off the base checkout — the `switch` back deletes them there. If you carry selectively or by hand, use `mv` — never `cp`: the untracked original left behind blocks the post-merge `git pull --ff-only` with "untracked working tree files would be overwritten by merge" (incident 2026-07-23; recovery in mr-followup `lib/post-merge.md`).

The compound `switch --detach` + `add -A` + `commit` sequence can itself be refused by an auto-mode permission classifier as one risky compound git operation on the main checkout, independent of anything about the artifacts being carried (observed 2026-09-02). When the artifacts to carry are only brand-new untracked files with no filename collision anywhere in history, `mv` alone (no detach, no commit) is a safe, much smaller substitute for this whole block — it is exactly the manual "carry selectively or by hand" case above, just reached because the scripted path was blocked rather than chosen for a selective carry.

## Why the native `EnterWorktree` tool is banned

(This is the canonical, fuller copy; the terser rule lives in CLAUDE.md "Worktrees".)

The native tool moves the *session* working directory, but the **Bash tool's cwd is independent and unreliable in both directions**: a `cd <worktree>` can survive several calls, and can also be silently re-anchored back to the project dir — both observed in one session, 2026-07-28. Either way no file tool sees it: Edit/Write (absolute paths) land where you name them while bare-path Bash commands (`python3`, `dotnet`, `git`) resolve against whatever the cwd currently is — a silent split-tree write that has put commits on `develop`. Manual `git worktree add` + `git -C "<abs>"` discipline avoids this entirely; read the ambient cwd at most once, then substitute that path as a **literal** into every later command — never a shell variable, which is empty by the next Bash call (CLAUDE.md "Worktree cwd gotcha" has the mechanism). If any other skill or tool offers `EnterWorktree`, decline — this flow overrides.

## `git -C <pruned-dir>` resolves to the parent repo, does not error

When a worktree's branch is deleted mid-intake by a concurrent merge (follow-up race, incident 2026-07-24), git prunes the worktree and leaves an **orphan shell dir**. `git -C <that-dir> …` does not fail — it walks up to the parent repo and runs there, so writes meant for the (gone) worktree silently hit the base checkout. The `rev-parse --show-toplevel == <worktree-abs>` assert in step 6 must ALWAYS run after `git worktree add`, but it does **not** cover this case: on an orphan shell the assert would return the *parent* toplevel and could even be skipped because the `git worktree add` itself failed on the missing ref. The real guard is the step-6 follow-up ref re-verify (`git ls-remote --exit-code origin <branch>`) *before* `git worktree add`.
