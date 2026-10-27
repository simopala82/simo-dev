---
name: plan-execute
description: Use when executing a written implementation plan task-by-task — load, execute with per-task tracking, stop on blockers instead of guessing.
---

# Executing Plans

## The loop

1. **Load and review.** Read the plan file completely. Review it critically — raise concerns with the user BEFORE starting, not mid-task. This includes reconciling any literal citation the plan makes (a line number, a described "current" behavior of a file) against what the target files actually show in this worktree — `task-intake` step 7.5's base-freshness gate and this step's own checkpoint below both check file-level overlap, not citation staleness, and neither can catch a plan citation that aged between when the plan was written and when this worktree was actually forked.

   **Base.** Resolved once, before the first task. `<base>` = the branch this worktree was cut from: the one `simo-dev:task-intake` named at hand-off, else the base the plan itself states; it may stay unknown. `<ledger-base>` is what step 2's branch-point check reads: `<base>` when it has an `origin/` ref, else the remote default branch, printed by (`<base>` substituted as a literal, empty when unknown):
   ```bash
   LB="<base>"
   [ -n "$LB" ] && ! git -C "<worktree-abs>" rev-parse --verify --quiet "origin/$LB" >/dev/null 2>&1 && { echo "LEDGER_BASE_FALLBACK: no origin/$LB"; LB=""; }
   [ -n "$LB" ] || LB=$(git -C "<worktree-abs>" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
   if [ -z "$LB" ]; then
     for cand in main master; do
       git -C "<worktree-abs>" rev-parse --verify --quiet "origin/$cand" >/dev/null 2>&1 && { LB="$cand"; break; }
     done
   fi
   echo "LEDGER_BASE=$LB"
   ```
   An empty `LEDGER_BASE=` makes every branch-point check `error`; a `LEDGER_BASE_FALLBACK:` line means the merge-base is the remote default branch's, so work between it and `<base>` reads `branch-added` (e.g. a local-only parent branch, an unfetched or misspelled `<base>`). An unknown `<base>` falls back the same way with no such line, so a branch not cut from the default branch gets the same effect silently. Step 3's Report states either printed line once.

   **Dependency freshness check.** If the header has a `Depends on: <TICKET>` field, run it before creating any todos — a stale worktree that still compiles is not proof the dependency is unchanged (incident 2026-08-05: a blocking sub-task merged with a different API shape than the plan assumed, and the worktree kept building because no touched symbol had been removed):
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/check_dependency_ticket.py" <TICKET> --repo "<worktree-abs>" --base "<base>"
   ```
   `<base>` (**Base** above) unknown → drop `--base` (the script falls back to the remote default branch, and a dependency merged into a different integration branch then reads as `open`). A `<base>` with no `origin/<base>` ref prints `unverifiable`, never a silent `open`.
   - `open` → the ticket is not Done and no commit naming it is on `origin/<base>` → proceed normally; the dependency isn't ready yet, nothing to re-verify.
   - `resolved-with-diff` → whenever a commit naming the key is on the base, the printed diff is that commit's, whatever the status; for a ticket not yet Done this is also what makes it resolved (a `status: <X> (merged into <base>)` line precedes the diff, for that case only). That commit is a signal, not proof of the merge, and its diff can be partial (several MRs, a rebase-merge, a hit on one branch-side commit of a merge whose own message omits the key, a later follow-up, a revert or another commit that only mentions the key being the newest hit), so the confirmation below still applies. A Done ticket with no such commit gets its diff from branch/log/forge lookups, which can pick unmerged work. Show the printed diff next to the plan's `assumed shape`. Ask the user to confirm the diff still matches the assumption before continuing. On a mismatch, stop — route back to `simo-dev:plan-write` (or `simo-dev:grill-me` for a design-level mismatch) instead of executing against a stale assumption.
   - `resolved-no-diff-found` / `unverifiable` (without an `(origin/<base> not found)` line — that case is the next bullet) → the dependency looks resolved (or its status could not be read) but its shape could not be recovered automatically. Warn and ask for manual confirmation before continuing — never block silently, never assume a match. For a ticket not yet Done, `resolved-no-diff-found` is followed by the same `status: <X> (merged into <base>)` line.
   - `unverifiable` + `status: <X> (origin/<base> not found)` → a different case: the ticket is not Done and the merge check could not run, because `origin/<base>` does not resolve. The dependency may still be open. Check the `--base` value and that an `origin` remote exists, re-run, and only then ask.

   No concerns (from either check) → create one todo per task.

   **Plan-fidelity note.** If the plan's header or a task cites a design spec (`docs/specs/*.md` or similar), record that path — step 2 needs it, both for direct execution and for every dispatch prompt. A plan snippet that looks broader than its own task's `**Files:**`/`**Interfaces:**` is not automatically drift: `simo-dev:plan-write` mirrors an approved spec faithfully, so a "replace the whole file" snippet can carry spec-required content that task line never calls out by name. Before trimming any such snippet as out-of-scope, check the referenced spec (if one exists) for that requirement first — "no test covers it" is not evidence of out-of-scope, only that it wasn't singled out for testing (incident 2026-09-11: two dispatched tasks each dropped spec-mandated content on that reasoning alone; a later review caught it only because it happened to read the spec directly).

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

   **On `n` (or the gate was skipped, `Agent` unavailable) — direct execution, unchanged:** Mark in_progress → follow the steps exactly → run every verification as specified (evidence per `simo-dev:verify-done`) → mark completed. Don't skip verifications, don't reorder steps without reason. Apply the plan-fidelity note from step 1 before trimming any snippet as out-of-scope. Capture surprising discoveries inline (CONTEXT.md / ADR / reference file), don't batch.

   **Also-fixed ledger.** An In-scope improvement a task made (`docs/canonical-flow.md` "In-scope improvements") goes to the ledger right after the task's verification when it qualifies:
   - The improvement a task titled `In-scope improvement — …` is named after qualifies with no check, a dead-code deletion included. Known limit: this trusts the plan, so it is ledgered even when an earlier task of the same plan wrote its lines, or when a mid-execution plan update added the task.
   - Any other one (that task's further improvements, and a dead-code deletion on top of its `rg -uu` evidence, unlike `simo-dev:review-loop`) qualifies only on an all-`pre-existing` branch-point check (block below), run **before** the edit: after it, the improvement's own lines read `branch-added`. Keep the verdict until the ledger write. Targets, and the "completes the branch's own change" exclusion: canonical-flow "In-scope improvements". Any other verdict, or none → the change stays, not ledgered; an `error` goes to step 3's Report.

   One single-quoted `file — what — why` argument per improvement, `file` relative to the repo root exactly as `git status --porcelain` prints it (never the plan's `<P>/…` or absolute form; the check's target files take the same form), a `'` inside a line written as `’`. Always appended by the orchestrator, never by a dispatched agent (no concurrent writers). A task that reverts an earlier task's ledgered improvement runs the `add` block with `drop` in place of `add`, only for a line actually ledgered (`drop` exits rc 1 on a line not in the ledger, and the `LEDGER_FAILED` hint would misdiagnose it). Skipped on a protected branch, check included (`simo-dev:commit`'s protected-branch detection): that path never reads or clears the ledger.
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "$(git -C "<worktree-abs>" merge-base HEAD "origin/<ledger-base>")" "<file1>:<line1>" "<file2>:<line2>" ...
   ```
   rc ≠ 0, or a target with no output line → `error` for that improvement.
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/also_fixed_ledger.py" add --repo "<worktree-abs>" '<file> — <what> — <why>' \
     || echo "LEDGER_FAILED rc=$? — the improvement is not recorded; fix the --repo literal and re-run"
   ```

   **Base-freshness checkpoint (direct execution).** Run after each task completes, per task and not once per plan: `simo-dev:task-intake` cuts the worktree from a fresh `origin/<base>`, but that guarantee is point-in-time and the drift lands mid-implementation (observed 2026-08-09: the base was fresh at creation, `origin/master` advanced ~8h35m later on a file the branch was editing). After and not before, because the guard intersects the advanced base commits with the files the branch has **already** changed — a task's own files become visible to it only once that task has written them:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
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

   **On `y` — delegated execution, batch-by-batch:** batches execute in the order `detect_independent_task_groups.py` printed. For each batch:

   1. Dispatch one `Agent` call per task in the batch — a single call for a size-1 batch, one call per task sent together in the same message for a size-≥2 batch, so they run concurrently. Every dispatch is `run_in_background: false` (never the `Agent` tool's own default of background): step 4 below needs every dispatch in the batch to have already returned before the checkpoint runs, which is exactly the "the very next action depends on this agent's result" case that calls for foreground — a batch of foreground calls sent together in one message all return together, before this step continues, with no separate wait-for-notifications step needed. Each dispatch's prompt carries: that task's own steps, read fresh from the plan file on disk; the worktree's absolute path, with the standing `git -C "<worktree-abs>"` discipline; that task's verification steps; the same "don't reorder steps without reason, capture surprising discoveries inline (CONTEXT.md / ADR / reference file), don't batch" discipline as the direct-execution branch above; the plan-fidelity note from step 1 verbatim, plus the recorded spec path if one exists, with an explicit instruction to read that spec before trimming any plan snippet as out-of-scope rather than relying on the task's own `Files:`/`Interfaces:` lines alone; the line "Write throwaway files only under a fresh `mktemp -d /tmp/claude_<slug>.XXXXXX` of your own; never write into or overwrite files in the Scratchpad directory your environment lists — it belongs to the caller's session." verbatim; and an explicit instruction never to commit (task-level commits do not happen in this skill regardless of delegation — `simo-dev:commit` is the only place a commit is made) and to scope every git/file operation it performs to that task's own declared Files: paths only, never touching another task's files in the same batch — an In-scope improvement included, limited to that task's own declared `Files:`; the task's `### Task N:` title; and the absolute path of this plugin's `docs/canonical-flow.md` (never a worktree-relative one: plan-execute runs in any repo), resolved once with:
      ```bash
      R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
      echo "$R/docs/canonical-flow.md"
      ```
      Unless `<ledger-base>` is empty or the branch is protected, the prompt also carries the ledger paragraph's branch-point check block with `<worktree-abs>` and `<ledger-base>` already substituted (the agent never sees this skill), and tells the agent to read that file's "In-scope improvements" (targets, and the "completes the branch's own change" exclusion) before its first check, then run the block before each improvement edit, target files in the ledger `file` path form. The agent names every In-scope improvement it made in its `✓ completed:` line (file — what — why, `file` relative to the repo root as `git status --porcelain` prints it) with at most one tag: its verdict (`pre-existing` / `branch-added` / `error`); `completes-branch` for one that completes the branch's own change, overriding any verdict, `pre-existing` included; or `exempt` for the improvement a task titled `In-scope improvement — …` is named after, which gets no check. With no check block in the prompt only `exempt` and `completes-branch` apply, and any other improvement carries no tag. The orchestrator appends the qualifying ones to the Also-fixed ledger (item 3; any other path form rewritten to the porcelain one before `add`). Known, unmitigated limitation: file-disjointness does not isolate *other* shared worktree/runner state — two dispatches in the same batch both running a whole-suite verification command (e.g. the full `pytest` suite) can race on a shared cache directory or a `.git` lockfile even though their own file sets never overlap, and the branch-point check's `git diff` touches `.git/index` too. Such a collision is indistinguishable from a genuine `⚠ blocked` outcome and is handled the same way (stop, surface, resume in-session) — no separate mechanism exists for it. `detect_independent_task_groups.py` has the same blind spot toward a task's `**Interfaces:**` block: two tasks whose `**Files:**` blocks don't overlap can land in the same batch even when one task's plan text `Consumes:` a name the other's `Produces:` line promises — see step 1's gate above for the cross-check this implies. Model: pass `model: $MODEL` when non-empty (the gate above already computed it), omit `model` entirely when empty (natural inherit-caller's-tier).
   2. Each dispatch's final message must end with exactly one of:
      - `✓ completed: <summary + verification result + In-scope improvements made, each with its item-1 tag, if any>`
      - `⚠ blocked: <what's missing, what was tried>`
   3. Mark every `✓ completed` task's todo completed, and append to the ledger (the `add` block above) exactly the In-scope improvements it tagged `pre-existing` or `exempt`. `branch-added`, `completes-branch`, `error` and untagged ones are not appended; untagged counts as `error`, and with `<ledger-base>` empty so does every non-`exempt` one. On a protected branch nothing is appended and nothing counts as `error`. `exempt` holds only when the text after `### Task N: ` in the title the orchestrator dispatched starts with `In-scope improvement — ` (the titular improvement may span several ledger lines, one per file); any other `exempt` counts as `error`. A batch containing a `⚠ blocked` task does not fail its siblings — their own `✓ completed` outcomes stand; only the blocked task's todo stays open.
   4. Run the base-freshness checkpoint (same block, unchanged, and rc table as above) **once for the whole batch**, after every dispatch in it has returned — not once per task within the batch (nothing in the checkpoint is task-specific, and running it concurrently per task would race the same worktree's git state for no benefit).
   5. Any `⚠ blocked` outcome in the batch → stop here, surface it to the user exactly like the "Hard stops" section below (the dispatch itself already refused to guess past it) — no task-level commits happened, so the worktree holds every completed task's work plus the blocked task's partial state, resumable in-session. Do not proceed to the next batch until resolved.
   6. All tasks in the batch completed → continue to the next batch.
3. **Report.** When all tasks are done: what was built vs the plan, deviations called out, and what the plan added to the Also-fixed ledger, and on its own labelled line each improvement whose branch-point check was `error` (made, not ledgered) — or, when `LEDGER_BASE=` was empty, one line saying so instead of that list; plus one line when step 1 printed `LEDGER_BASE_FALLBACK:`. The proactive `simo-dev:review-loop` trigger fires at this point (implementation complete).

## Hard stops — ask, never guess

Stop immediately and ask the user when: a blocker appears (missing dependency, failing test you can't explain), the plan has a gap that prevents proceeding, an instruction is unclear, or a verification fails repeatedly. Don't force through blockers.

Return to step 1 when the user updates the plan or the approach proves wrong.

## Worktree discipline

Work in the worktree created by `simo-dev:task-intake`: `git -C "<worktree-abs>"` for every git op, absolute worktree paths for scripts/tests — the Bash cwd does NOT follow the worktree.
