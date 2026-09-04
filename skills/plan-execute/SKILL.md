---
name: plan-execute
description: Use when executing a written implementation plan task-by-task — load, execute with per-task tracking, stop on blockers instead of guessing.
---

# Executing Plans

## The loop

1. **Load and review.** Read the plan file completely. Review it critically — raise concerns with the user BEFORE starting, not mid-task.

   **Dependency freshness check.** If the header has a `Depends on: <TICKET>` field, run it before creating any todos — a stale worktree that still compiles is not proof the dependency is unchanged (incident 2026-08-05: a blocking sub-task merged with a different API shape than the plan assumed, and the worktree kept building because no touched symbol had been removed):
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/check_dependency_ticket.py" <TICKET> --repo "<worktree-abs>"
   ```
   - `open` → proceed normally; the dependency isn't ready yet, nothing to re-verify.
   - `resolved-with-diff` → show the printed diff next to the plan's `assumed shape`. Ask the user to confirm the diff still matches the assumption before continuing. On a mismatch, stop — route back to `simo-dev:plan-write` (or `simo-dev:grill-me` for a design-level mismatch) instead of executing against a stale assumption.
   - `resolved-no-diff-found` / `unverifiable` → the dependency looks resolved but its shape could not be recovered automatically. Warn and ask for manual confirmation before continuing — never block silently, never assume a match.

   No concerns (from either check) → create one todo per task.
2. **Execute task-by-task.** Mark in_progress → follow the steps exactly → run every verification as specified (evidence per `simo-dev:verify-done`) → mark completed. Don't skip verifications, don't reorder steps without reason. Capture surprising discoveries inline (CONTEXT.md / ADR / reference file), don't batch.

   **Base-freshness checkpoint.** Run after each task completes, per task and not once per plan: `simo-dev:task-intake` cuts the worktree from a fresh `origin/<base>`, but that guarantee is point-in-time and the drift lands mid-implementation (observed 2026-08-09: the base was fresh at creation, `origin/master` advanced ~8h35m later on a file the branch was editing). After and not before, because the guard intersects the advanced base commits with the files the branch has **already** changed — a task's own files become visible to it only once that task has written them:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   G="$R/lib/check_base_freshness.py"
   [ -f "$G" ] || { echo "STOP: guard unreachable at $G"; exit 4; }
   python3 "$G" --repo "<worktree-abs>"
   ```
   - rc `1` → **stop**, surface the printed overlap. Recovery is `git rebase origin/<base>` in the worktree, which needs a clean tree — at a task boundary it normally carries the earlier tasks' uncommitted work, so that has to be committed first, or parked per `simo-dev:commit` step 0.5's rc `1` recovery — never `git stash`: ask, per the hard-stop rule below.
   - rc `0` (safe, or base advanced on files disjoint from the branch) / `2` (git error — never a false stop) → continue. rc `4` → **stop**, the guard never ran. Why the test is there and how to recover: `simo-dev:commit` step 0.5.
3. **Report.** When all tasks are done: what was built vs the plan, deviations called out. The proactive `simo-dev:review-loop` trigger fires at this point (implementation complete).

## Hard stops — ask, never guess

Stop immediately and ask the user when: a blocker appears (missing dependency, failing test you can't explain), the plan has a gap that prevents proceeding, an instruction is unclear, or a verification fails repeatedly. Don't force through blockers.

Return to step 1 when the user updates the plan or the approach proves wrong.

## Worktree discipline

Work in the worktree created by `simo-dev:task-intake`: `git -C "<worktree-abs>"` for every git op, absolute worktree paths for scripts/tests — the Bash cwd does NOT follow the worktree.
