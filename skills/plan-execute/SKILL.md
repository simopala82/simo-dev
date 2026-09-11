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

   **Independent task groups & execution delegation gate.** Compute the plan's batches:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/detect_independent_task_groups.py" "<plan-path>"
   ```
   One line per batch, space-separated task numbers, in plan order (a single number means a size-1, purely sequential batch). Then read the delegation model:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   MODEL=$(python3 "$R/lib/model_strategy.py" get plan_execute)
   ```
   `<model-label>` = `$MODEL` when non-empty, else the literal text `inherit caller's tier` — never print a blank.

   Fires always, once per `plan-execute` run — not only when a batch has size ≥2, so that model configurability has value on a purely sequential plan too:

   - Any batch has size ≥2:
     ```
     Recommended: agent-dispatch — <n> tasks in <m> independent groups.
     Delegate task execution to Agent dispatch (model: <model-label>)?
     ```
     (`<n>` = total task count across all batches, `<m>` = batch count)
   - Every batch has size 1:
     ```
     Delegate task execution to Agent dispatch (model: <model-label>)?
     ```

   For any batch of size ≥2, cross-check that batch's tasks' `**Interfaces:**` sections before answering — the batching above reflects only `**Files:**` overlap (mechanism: step 2's "Known, unmitigated limitation" paragraph below), so a `Consumes:`/`Produces:` relationship found between two tasks in the same batch means that batch is wrong for parallel delegation: answer `n`, not a plan edit.

   Default `y` in both cases. `n` → step 2's direct-execution branch, unchanged from today. `y` → step 2's delegated-execution branch drives the batches instead.

   `Agent` tool unavailable → skip this gate silently, fall back to direct execution — same convention as other unavailable-tool fallbacks in this plugin.
2. **Execute task-by-task.** Behavior depends on the execution delegation gate above.

   **On `n` (or the gate was skipped, `Agent` unavailable) — direct execution, unchanged:** Mark in_progress → follow the steps exactly → run every verification as specified (evidence per `simo-dev:verify-done`) → mark completed. Don't skip verifications, don't reorder steps without reason. Capture surprising discoveries inline (CONTEXT.md / ADR / reference file), don't batch.

   **Base-freshness checkpoint (direct execution).** Run after each task completes, per task and not once per plan: `simo-dev:task-intake` cuts the worktree from a fresh `origin/<base>`, but that guarantee is point-in-time and the drift lands mid-implementation (observed 2026-08-09: the base was fresh at creation, `origin/master` advanced ~8h35m later on a file the branch was editing). After and not before, because the guard intersects the advanced base commits with the files the branch has **already** changed — a task's own files become visible to it only once that task has written them:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   G="$R/lib/check_base_freshness.py"
   [ -f "$G" ] || { echo "STOP: guard unreachable at $G"; exit 4; }
   python3 "$G" --repo "<worktree-abs>"
   ```
   - rc `1` → **stop**, surface the printed overlap. Recovery is `git rebase origin/<base>` in the worktree, which needs a clean tree — at a task boundary it normally carries the earlier tasks' uncommitted work, so that has to be committed first, or parked per `simo-dev:commit` step 0.5's rc `1` recovery — never `git stash`: ask, per the hard-stop rule below.
   - rc `0` (safe, or base advanced on files disjoint from the branch) / `2` (git error — never a false stop) → continue. rc `4` → **stop**, the guard never ran. Why the test is there and how to recover: `simo-dev:commit` step 0.5.

   **On `y` — delegated execution, batch-by-batch:** batches execute in the order `detect_independent_task_groups.py` printed. For each batch:

   1. Dispatch one `Agent` call per task in the batch — a single call for a size-1 batch, one call per task sent together in the same message for a size-≥2 batch, so they run concurrently. Every dispatch is `run_in_background: false` (never the `Agent` tool's own default of background): step 4 below needs every dispatch in the batch to have already returned before the checkpoint runs, which is exactly the "the very next action depends on this agent's result" case that calls for foreground — a batch of foreground calls sent together in one message all return together, before this step continues, with no separate wait-for-notifications step needed. Each dispatch's prompt carries: that task's own steps, read fresh from the plan file on disk; the worktree's absolute path, with the standing `git -C "<worktree-abs>"` discipline; that task's verification steps; the same "don't reorder steps without reason, capture surprising discoveries inline (CONTEXT.md / ADR / reference file), don't batch" discipline as the direct-execution branch above; and an explicit instruction never to commit (task-level commits do not happen in this skill regardless of delegation — `simo-dev:commit` is the only place a commit is made) and to scope every git/file operation it performs to that task's own declared Files: paths only, never touching another task's files in the same batch. Known, unmitigated limitation: file-disjointness does not isolate *other* shared worktree/runner state — two dispatches in the same batch both running a whole-suite verification command (e.g. the full `pytest` suite) can race on a shared cache directory or a `.git` lockfile even though their own file sets never overlap. Such a collision is indistinguishable from a genuine `⚠ blocked` outcome and is handled the same way (stop, surface, resume in-session) — no separate mechanism exists for it. `detect_independent_task_groups.py` has the same blind spot toward a task's `**Interfaces:**` block: two tasks whose `**Files:**` blocks don't overlap can land in the same batch even when one task's plan text `Consumes:` a name the other's `Produces:` line promises — see step 1's gate above for the cross-check this implies. Model: pass `model: $MODEL` when non-empty (the gate above already computed it), omit `model` entirely when empty (natural inherit-caller's-tier).
   2. Each dispatch's final message must end with exactly one of:
      - `✓ completed: <summary + verification result>`
      - `⚠ blocked: <what's missing, what was tried>`
   3. Mark every `✓ completed` task's todo completed. A batch containing a `⚠ blocked` task does not fail its siblings — their own `✓ completed` outcomes stand; only the blocked task's todo stays open.
   4. Run the base-freshness checkpoint (same script and rc table as above) **once for the whole batch**, after every dispatch in it has returned — not once per task within the batch (nothing in the checkpoint is task-specific, and running it concurrently per task would race the same worktree's git state for no benefit).
   5. Any `⚠ blocked` outcome in the batch → stop here, surface it to the user exactly like the "Hard stops" section below (the dispatch itself already refused to guess past it) — no task-level commits happened, so the worktree holds every completed task's work plus the blocked task's partial state, resumable in-session. Do not proceed to the next batch until resolved.
   6. All tasks in the batch completed → continue to the next batch.
3. **Report.** When all tasks are done: what was built vs the plan, deviations called out. The proactive `simo-dev:review-loop` trigger fires at this point (implementation complete).

## Hard stops — ask, never guess

Stop immediately and ask the user when: a blocker appears (missing dependency, failing test you can't explain), the plan has a gap that prevents proceeding, an instruction is unclear, or a verification fails repeatedly. Don't force through blockers.

Return to step 1 when the user updates the plan or the approach proves wrong.

## Worktree discipline

Work in the worktree created by `simo-dev:task-intake`: `git -C "<worktree-abs>"` for every git op, absolute worktree paths for scripts/tests — the Bash cwd does NOT follow the worktree.
