# Plan review gate: rare paths

Loaded on demand from `task-intake` `SKILL.md` step 7.5, each section by its own trigger there: a loop-back answer (`apply and re-present`, `edit`, `restart`), a `⚠ Plan critique` list too long for the summary, a bare plan that missed a design decision, a `bypass:` other than `none`, or a question about what the base-freshness check can see. Step numbers refer to `SKILL.md`.

## Loop-back answers

`apply and re-present` → the same items as `apply and proceed` (one per gate finding that has a recommended option: its `fix:` text or that option; `(no recommendation)` findings are left for the next pass), sent through the writer as `SKILL.md` step 7.5's "Every plan change at this gate" paragraph says, then loop back like `edit`.

On `edit` → send the feedback as the item (never a fresh writer dispatch except **Resume**'s own fallback, which keeps the plan) and loop back — the top of step 7.5 re-runs (an explicit `edit` always gets one critique round, even past the `Critique rounds` cap), so the bypass check is recomputed fresh next time through.

On `restart` → discard the plan, ask: restart from direction (step 4, reuse analysis) or analysis (step 3, re-dispatch Explore)? Step 7 then writes a new brief and dispatches a new writer.

None of these exits the gate, so none posts the plan comment or records the `plan` gate event.

## Summary overflow

The summary stays at ~25 lines: cap the `⚠ Plan critique` list to 8 lines and add a trailing `+<n> more (see <file>)` line instead of dropping entries silently, `<file>` being `<brief-dir>/gate-findings.md` (a fast-path plan has no `<brief-dir>`: create one the same way), rewritten with the full gate findings set (tails included) on every gate pass, never appended. Never write it into the plan file: `plan-reviewer` reads that file.

## Bare plan: escape hatch

There is no runtime reviewer here to auto-escalate on evidence, unlike `review-loop`'s high/critical-finding restore (rationale: `docs/specs/2026-09-11-task-intake-bare-plan-design.md`, "Escape hatch"). If `simo-dev:plan-execute` or the user (at this gate's `edit`) surfaces a real design decision the bare-plan file didn't anticipate, the correction is manual. At this gate: `restart` back to step 4 (which discards the bare plan) and pick `deep-dive` there; step 7 then dispatches the writer. From `plan-execute`, where this gate is long closed: stop, mark the bare-plan file superseded rather than silently overwriting it, and re-run `simo-dev:task-intake` on the same ticket or prompt, picking `deep-dive` at step 4 (`plan-write` is the writer subagent's rules only, never a standalone planner).

## Bypass conditions

`lib/plan_gate_precheck.py` prints one `bypass:` line; each condition forces step 8's In-session row, whatever the gate would otherwise offer:

1. **Follow-up on an existing branch** (`follow-up`). This run's target branch already exists remotely (`git ls-remote --exit-code origin "refs/heads/<branch>"`: exit 2 → new branch, condition does not fire; any other exit counts as existing). `start_session` has no working path for a follow-up (row 3's adopt-rename step only renames onto a name that doesn't already exist elsewhere — a pre-existing gap in step 6, not fixed here).
2. **This session is itself already sitting in a linked worktree that step 6's own table would resolve to row 3 or row 4** (`linked-worktree (row <r>)`) — step 3's `row:` line, already computed this run, is `3s`, `3a`, `3b` or `4`. Row 3s normally stopped at step 3 already; it reaches this gate only after the user picked "adopt anyway" (then it is row 3a), or when a linked session started in the tree since — step 6's re-check stops it then. On row 3a/3b — never a shared tree, since 3s is matched first — this session's own uncommitted artifacts live inside a tree step 6 is about to rename (3a) or move and rename (3b) — there is no separate base-branch state to hand a spawned child, and no clean way to build a carry commit from a typically-detached HEAD (`worktree-carry.md`'s own first command, `symbolic-ref --short HEAD`, would refuse). Rows 3s and 4 stop regardless of hand-off path.
3. **This session is a submodule working tree** (`submodule`) — step 3's `row: 1` with `submodule: yes`. A child spawned from here lands under the submodule, its `<REAL>` is a `.git/modules/**` path that fails step 6's `<REAL>` assert, and it stops after this session already built the carry.

## Freshness check scope

The `--plan-files` check still compares the local `<base>` against `origin/<base>`, not against `HEAD` — a stale local base still surfaces a real overlap here exactly as before; only the worktree-local divergence this same check would also catch after step 6 runs is no longer available at this point (that guard now runs per-task/per-commit instead, in `plan-execute` and `commit` step 0.5).

rc 0/rc 2 here answers a file-overlap question, not a citation-staleness one: the base can advance on exactly the files the plan cites between this gate and step 8's actual worktree creation, with no worktree yet for the guard to see, and once that worktree does fork from the already-advanced base there is no overlap left to detect — the plan's own line numbers or "current behavior" claims can still be stale even though every later run of this same check reports rc 0. This gate cannot close that: the fix is at the point the worktree's own files become readable — see `plan-execute` step 1's "Load and review".
