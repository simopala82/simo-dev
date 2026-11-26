---
name: plan-execute
description: Use when executing a written implementation plan task-by-task — load, execute with per-task tracking, stop on blockers instead of guessing.
---

# Executing Plans

## The loop

1. **Load and review.** Read the plan file completely. Review it critically — raise concerns with the user BEFORE starting, not mid-task. This includes reconciling any literal citation the plan makes (a line number, a described "current" behavior of a file) against what the target files actually show in this worktree — `task-intake` step 7.5's base-freshness gate and this step's own checkpoint below both check file-level overlap, not citation staleness, and neither can catch a plan citation that aged between when the plan was written and when this worktree was actually forked.

   **Preflight.** One call, before the first task. `<base>` = the branch this worktree was cut from: the one `simo-dev:task-intake` named at hand-off, else the base the plan itself states; it may stay unknown (pass it empty):
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/plan_execute_preflight.py" "<plan-path>" --repo "<worktree-abs>" --base "<base>"
   ```
   Keep its lines as literals. A non-zero rc, or a missing line, means the call failed: stop and surface its output to the user.
   - `LEDGER_BASE=` → `<ledger-base>`, what step 2's branch-point check reads: `<base>` when it has an `origin/` ref, else the remote default branch. Empty makes every branch-point check `error`; a `LEDGER_BASE_FALLBACK:` line means the merge-base is the remote default branch's, so work between it and `<base>` reads `branch-added` (e.g. a local-only parent branch, an unfetched or misspelled `<base>`). An unknown `<base>` falls back the same way with no such line, so a branch not cut from the default branch gets the same effect silently. Step 3's Report states either printed line once.
   - `depends-on:` other than `none` → read `lib/dependency-check.md` and run it before creating any todos.
   - `model:` → `<model-label>`.
   - `batches:` and `execution:` → the delegation gate below.

   No concerns → create one todo per task.

   **Plan-fidelity note.** If the plan's header or a task cites a design spec (`docs/specs/*.md` or similar), record that path — step 2 needs it, both for direct execution and for every dispatch prompt. A plan snippet that looks broader than its own task's `**Files:**`/`**Interfaces:**` is not automatically drift: `simo-dev:plan-write` mirrors an approved spec faithfully, so a "replace the whole file" snippet can carry spec-required content that task line never calls out by name. Before trimming any such snippet as out-of-scope, check the referenced spec (if one exists) for that requirement first — "no test covers it" is not evidence of out-of-scope, only that it wasn't singled out for testing (incident 2026-09-11: two dispatched tasks each dropped spec-mandated content on that reasoning alone; a later review caught it only because it happened to read the spec directly).

   **Execution delegation gate.** Answered by the agent running this skill, never asked. The preflight's `execution:` line applies the first match:
   1. **`direct`** — the plan has **12 or less** steps (checkbox lines; a bare plan counts 0, and checkbox lines inside fenced blocks count too). A clean-context executor first re-reads the code it needs, which costs more than it saves on a small plan: break-even is about 20 execution calls, and execution took a median of about 1.4 calls per plan step (15 plans, range 0.4-2.3, measured 2026-11-16).
   2. **`agent-dispatch?`** — some batch has ≥2 file-disjoint tasks: read `lib/agent-dispatch.md`, whose first section decides `agent-dispatch` or `subagent`.
   3. **`subagent`** — everything else: one clean-context executor runs the whole plan, so the execution's reading and edits never enter this session's context, which review-loop, commit and mr-followup re-read on every call.

   `Agent` tool unavailable → `direct` whatever the line says (why: `Agent` unavailable). Print one line and continue without asking:
   ```
   Execution: <direct|subagent|agent-dispatch> (model: <model-label>) — <why>
   ```
   `direct` → step 2. `subagent` → read `lib/executor-dispatch.md` and follow it in place of step 2. `agent-dispatch` → `lib/agent-dispatch.md`'s Execution section in place of step 2. The model applies to `subagent` and `agent-dispatch` only.
2. **Execute task-by-task (`direct`).** Mark in_progress → follow the steps exactly → run every verification as specified (evidence per `simo-dev:verify-done`) → mark completed. Don't skip verifications, don't reorder steps without reason. Apply the plan-fidelity note from step 1 before trimming any snippet as out-of-scope. Capture surprising discoveries inline (CONTEXT.md / ADR / reference file), don't batch.

   **Also-fixed ledger.** An In-scope improvement a task made (`docs/canonical-flow.md` "In-scope improvements") goes to the ledger right after the task's verification when it qualifies:
   - The improvement a task titled `In-scope improvement — …` is named after qualifies with no check, a dead-code deletion included. Known limit: this trusts the plan, so it is ledgered even when an earlier task of the same plan wrote its lines, or when a mid-execution plan update added the task.
   - Any other one (that task's further improvements, and a dead-code deletion on top of its `rg -uu` evidence, unlike `simo-dev:review-loop`) qualifies only on an all-`pre-existing` branch-point check (block below), run **before** the edit: after it, the improvement's own lines read `branch-added`. Keep the verdict until the ledger write. Targets, and the "completes the branch's own change" exclusion: canonical-flow "In-scope improvements". Any other verdict, or none → the change stays, not ledgered; an `error` goes to step 3's Report.

   One single-quoted `file — what — why` argument per improvement, `file` relative to the repo root exactly as `git status --porcelain` prints it (never the plan's `<P>/…` or absolute form; the check's target files take the same form), a `'` inside a line written as `’`. Always appended by the orchestrator, never by a dispatched agent (no concurrent writers). A task that reverts an earlier task's ledgered improvement runs the `add` block with `drop` in place of `add`, only for a line actually ledgered (`drop` exits rc 1 on a line not in the ledger, and the `LEDGER_FAILED` hint would misdiagnose it). Skipped on a protected branch, check included (`simo-dev:commit`'s protected-branch detection): that path never reads or clears the ledger.
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "$(git -C "<worktree-abs>" merge-base HEAD "origin/<ledger-base>")" "<file1>:<line1>" "<file2>:<line2>" ...
   ```
   rc ≠ 0, or a target with no output line → `error` for that improvement.
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/also_fixed_ledger.py" add --repo "<worktree-abs>" '<file> — <what> — <why>' \
     || echo "LEDGER_FAILED rc=$? — the improvement is not recorded; fix the --repo literal and re-run"
   ```

   **Base-freshness checkpoint (direct execution).** Run after each task completes, per task and not once per plan: `simo-dev:task-intake` cuts the worktree from a fresh `origin/<base>`, but that guarantee is point-in-time and the drift lands mid-implementation (observed 2026-08-09: the base was fresh at creation, `origin/master` advanced ~8h35m later on a file the branch was editing). After and not before, because the guard intersects the advanced base commits with the files the branch has **already** changed — a task's own files become visible to it only once that task has written them:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   G="$R/lib/check_base_freshness.py"
   [ -f "$G" ] || { echo "STOP: guard unreachable at $G"; exit 4; }
   git -C "<worktree-abs>" fetch origin --prune --quiet
   BASE=$(python3 "$R/lib/build_mr_ctx.py" --repo "<worktree-abs>" --print-target) || BASE=""
   echo "CHECK BASE: ${BASE:-<guard default>}"
   if [ -n "$BASE" ]; then python3 "$G" --repo "<worktree-abs>" --base "$BASE"; else python3 "$G" --repo "<worktree-abs>"; fi
   ```
   The base is the target `simo-dev:commit` will use (stacked parent, else `origin/HEAD`), so a stacked branch is not stopped for the default branch's own advance; a failed or empty resolution falls back to the guard's default. Not detected (silently the default): a branch with no commits of its own yet whose parent advanced on the remote after the cut — the usual state mid-plan, since nothing is committed per task. The explicit `if` is deliberate: zsh does not word-split `${BASE:+--base "$BASE"}` (the trap `simo-dev:commit` step 1 documents).
   - rc `1` → **stop**, surface the printed overlap. When the resolution printed a `note:` or a `STOP:` (stderr, kept visible), the check ran against the default branch: ask the user which branch this one is stacked on BEFORE any rebase or park — rebasing onto the default would pull the parent's commits in (same rule as `simo-dev:commit` step 0.5). Otherwise recovery is `git rebase origin/<CHECK BASE>` in the worktree, which needs a clean tree — at a task boundary it normally carries the earlier tasks' uncommitted work, so that has to be committed first, or parked per `simo-dev:commit` step 0.5's rc `1` recovery — never `git stash`: ask, per the hard-stop rule below.
   - rc `0` (safe, or base advanced on files disjoint from the branch) / `2` (git error — never a false stop) → continue. rc `4` → **stop**, the guard never ran. Why the test is there and how to recover: `simo-dev:commit` step 0.5.
3. **Report.** When all tasks are done: what was built vs the plan, deviations called out (on `subagent`: from the executor's report, not from re-reading the diff), and what the plan added to the Also-fixed ledger, and on its own labelled line each improvement whose branch-point check was `error` (made, not ledgered) — or, when `LEDGER_BASE=` was empty, one line saying so instead of that list; plus one line when step 1 printed `LEDGER_BASE_FALLBACK:`. The proactive `simo-dev:review-loop` trigger fires at this point (implementation complete).

## Hard stops — ask, never guess

Stop immediately and ask the user when: a blocker appears (missing dependency, failing test you can't explain), the plan has a gap that prevents proceeding, an instruction is unclear, or a verification fails repeatedly. Don't force through blockers.

Return to step 1 when the user updates the plan or the approach proves wrong.

## Worktree discipline

Work in the worktree created by `simo-dev:task-intake`: `git -C "<worktree-abs>"` for every git op, absolute worktree paths for scripts/tests — the Bash cwd does NOT follow the worktree.
