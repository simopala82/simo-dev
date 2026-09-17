---
name: plan-write
description: Use when writing an implementation plan from an approved direction or design — bite-sized checkbox tasks with exact paths, complete content, and verification steps. Invoked by simo-dev:task-intake step 7 and simo-dev:grill-me handoff.
---

# Writing Implementation Plans

Write for a skilled developer with ZERO context for this codebase: which files to touch per task, the actual code for code targets (and an anchor plus a required outcome for prose targets — see below), how to verify, exact commands. DRY, YAGNI, TDD.

**Save to:** `docs/plans/YYYY-MM-DD-<name>.md` (with a ticket key: `YYYY-MM-DD-<jira-key-lowercase>-<name>.md`).

## Header (mandatory)

```markdown
# [Feature Name] Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use simo-dev:plan-execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** [one sentence]
**Architecture:** [2-3 sentences]
**Tech Stack:** [key technologies]
**Depends on:** [optional — `<TICKET-KEY> — assumed shape: <one-liner>`. Omit entirely unless this plan's approach assumes the API/contract of a ticket not yet merged at plan-write time.]
**Deploy required:** [optional — `yes` | `no`. Omit entirely unless every task in this plan applies its change directly to a live external system that is already verified at plan-write time, with nothing left for a deploy pipeline to publish — omitting the field (or writing `yes`) defaults to `yes`, today's unchanged behavior.]

## Global Constraints
[project-wide requirements — version floors, naming rules, repo conventions — one line each, exact values. Every task implicitly includes this section.]
---
```

`Depends on` is optional — set it only when the plan's approach assumes the shape of a ticket that has not merged yet (typically a blocking sub-task opened via `simo-dev:grill-me` and developed in another session). `simo-dev:plan-execute` step 1 re-derives that ticket's real status and merged diff before executing any task; a plan with no such assumption omits the field.

`Deploy required` is optional too — set it to `no` only when every task in this plan applies its change directly to a live external system, already verified at plan-write time, with no artifact for a deploy pipeline to publish afterward; omit it (or write `yes`) for anything that touches application code or otherwise needs a deploy to take effect. `simo-dev:mr-followup` step 8 reads it from the merged plan file to pick the post-merge ticket state (`docs/canonical-flow.md` documents the full routing).

A whole-suite verification gate:
- States the baseline at a named branch-point SHA (`git merge-base HEAD origin/<base>`) and gates on the **delta** above it, never the raw total.
- On a mismatch at execution time, re-measure the baseline at the current merge-base and reconcile — never edit the number to match the output.

Logged in the author's own claim-hygiene notes (keep a similar file if you have one): "A recorded number can age for reasons that have nothing to do with what it measures."

## Task shape

A task is the smallest unit with its own test/verify cycle — it ends with an independently verifiable deliverable. Each step is one action (2-5 min).

Nesting: `Global Constraints` above is `##`; every `Task N` heading nests one level below it, at `###` — never `##`, which makes the task a sibling of `Global Constraints` instead of a child of it. Combined, the two templates read:

```markdown
## Global Constraints
[...]
---

### Task 1: [Component]
...
```

```markdown
### Task N: [Component]

**Files:**
- Create: `exact/path/file.py`
- Modify: `exact/path/existing.py:123-145`   # code: a line range is fine
- Modify: `exact/path/doc.md` at `<stable anchor>`   # prose: never a line range, see below
- Test: `tests/exact/path/test.py`

**Interfaces:**
- Consumes: [from earlier tasks — exact signatures]
- Produces: [what later tasks rely on — exact names/types. The implementer sees only their own task; this block is how neighbors' names reach them.]

- [ ] **Step 1: Write the failing test** [actual test code block]
- [ ] **Step 2: Run it — expect FAIL** [exact command + expected failure]
- [ ] **Step 3: Minimal implementation** [actual code block]
- [ ] **Step 4: Run it — expect PASS** [exact command]
```

- A table with one row per mutation/variant records the **failure count** per row (and which tests) — never a passed or passed/total count.

Logged in the author's own claim-hygiene notes (keep a similar file if you have one): "A recorded number can age for reasons that have nothing to do with what it measures."

## No placeholders

These are plan failures — never write them:
- "TBD" / "TODO" / "implement later" / "fill in details"
- "Add appropriate error handling" / "add validation" / "handle edge cases"
- "Write tests for the above" without the actual test code
- "Similar to Task N" — repeat the content; tasks may be read out of order
- Steps that say what without showing how (code blocks required for code steps)
- References to names/types not defined in any task

## Prose targets: anchor and outcome, never the final wording

When **editing** prose. Which files those are is judged by content, not extension: `*.md`, `*.txt`, skill bodies, `CLAUDE.md` and docs are where prose usually lives, but the extension settles nothing on its own. Classification is per *block*, not per file: `skills/mr-followup/lib/post-merge.md` and its `lib/pipeline-scripts.md` carry shell that runs verbatim, so a plan changing one of those blocks owes the exact code, while a plan changing the prose around them follows this rule. For prose, record three things, and never paste the final wording:

1. the file;
2. a **stable identifying anchor** locating the spot — a heading, an option name, a command, a symbol. Never a whole sentence: those age under exactly the rewording this rule exists for;
3. what the change must achieve, and what must survive it.

Reviewed prose gets reworded. A pasted copy then reads as an instruction to undo the review: a plan whose `new_string` lags the shipped text deletes the fix on replay (measured 2026-08-06 — that loop alone produced about eight findings and their fixes across ten review passes on one MR).

For **code** the section above stands unchanged: real code blocks, no placeholders, nothing vague.

When the plan **creates** a prose file that does not exist yet there is no anchor to cite, and this rule does not apply: state the file's purpose and the sections it must contain, and author the text during execution.

This is not the vagueness that section forbids. "In file X, at the section/heading/identifier `<anchor>`, state Y and keep Z" is precise and executable — it just stops duplicating a string git already stores. Same reasoning as citing file + identifying string instead of `file:line`.

## Self-review (before presenting)

Run yourself, fix inline:
1. **Spec coverage** — every requirement maps to a task; list gaps and add tasks.
2. **Placeholder scan** — search the plan for the patterns above.
3. **Cross-task consistency** — names, signatures, and types used in later tasks match their definitions in earlier tasks.
4. **Task header level** — every `Task N` heading matches `### Task N:` (see "Task shape" above), never `## Task N:`; fix any mismatch before presenting the plan.
5. **Literal-wording scan** — for each prose block (per the `## Prose targets: anchor and outcome, never the final wording` rule's own "Classification is per *block*, not per file" clause), check that it cites an anchor and an outcome instead of pasting the final wording — a before/after or "becomes:" style block.

## Plan critique and review gate

**Read the plan file from disk first** (always — subagent-authored plans never reached this context). Then run the base-freshness check against the plan's declared files — once, synchronously, right here; not repeated later in this same gate loop (an `edit`/`restart` round-trip re-runs it naturally next time this section executes from the top):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/check_base_freshness.py" --repo "<worktree-abs>" --plan-files
```

- rc 1 → fold the printed overlap (the overlapping file paths — `analyze()` returns no commit data) into the Plan summary below, under a `⚠ Base advanced` heading, right after Risks/verification.
- rc 0 or rc 2 → no change to the summary.

Then read the `plan_critique` model config — defaults to `sonnet` when unconfigured; set the sentinel value `none` (`model_strategy.py set plan_critique none`) to disable automatic plan critique entirely:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
PLAN_CRITIQUE_MODEL=$(python3 "$R/lib/model_strategy.py" get plan_critique)
if [ "$PLAN_CRITIQUE_MODEL" = "none" ]; then
  PLAN_CRITIQUE_MODEL=""
elif [ -z "$PLAN_CRITIQUE_MODEL" ]; then
  PLAN_CRITIQUE_MODEL="sonnet"
fi
```

Empty (only reached via the explicit `none` sentinel now) → no dispatch. Non-empty → dispatch an `Agent` with `subagent_type: simo-dev:plan-reviewer`, `model: $PLAN_CRITIQUE_MODEL`, `run_in_background: false` (same reasoning as `code-review-protocol` item 2: the gate must see the findings before the user is asked to proceed) — prompt = the plan file content just read from disk plus the gathered context already available at this section. Parse the response's findings in the same `[severity] location — message` line format `code-reviewer` findings use elsewhere in this plugin.

**Auto-apply objective findings, before the gate is shown.** Classify each parsed finding:

- **Objective** — severity `critical`/`high`/`medium` AND the finding names a mechanically verifiable plan defect: a wrong anchor/reference, a non-executable command, a dead/unused variable, an empty assertion, and similar. Fix it with a targeted `Edit` to the plan file, right here — do not wait for an `edit` at the gate below, and never a full `plan-write` regeneration for these (that scope belongs to judgment findings, where the human sees and approves the resulting diff).
- **Judgment** — anything else: severity `low`, or the finding argues for an alternative direction, scope, or design choice, or otherwise needs the user's own decision rather than a mechanical fix. This escape hatch overrides severity — a `critical`- or `medium`-tagged design objection is still judgment, never auto-applied. Judgment findings wait for the user at the gate, unchanged from today.

Round 1 is the initial dispatch just above. After applying a round's objective fixes, re-read the plan file from disk (never the pre-fix content still sitting in context) and re-dispatch `plan-reviewer` on it — same model, same call shape, prompt built the same way as round 1: from the freshly re-read file, not from memory. Repeat classify → autofix → re-review for up to 3 rounds total. On round 3, stop auto-applying: any remaining objective findings fall through to the gate as judgment findings instead, tagged `(autofix cap reached)` in the `⚠ Plan critique` list — this bounds the loop if the reviewer keeps surfacing new objective findings every round.

Carry judgment findings forward cumulatively across rounds — each re-dispatch is a stateless call with no memory of earlier rounds, so a judgment finding from round 1 that round 2's dispatch doesn't re-surface (new objective findings crowding it out, or plain non-determinism) must still reach the gate. Keep a **gate findings set**: every judgment finding seen across all rounds, deduped by the **(location, message) pair** — never by location alone, since a location is a single string (a task or a file reference) and `plan-reviewer`'s independent check categories can legitimately raise two distinct findings against the same one — plus (round 3 only) any objective findings the cap pushed there. On a genuine (location, message) match across rounds, keep the `(autofix cap reached)` tag if any colliding occurrence carries it — a merge must never silently drop it. Exact-string match only: a differently-worded restatement of the same finding across rounds is not deduped by this rule and can surface as a separate entry — accepted, to keep the check simple. This set — not any single round's raw output — is what both the `⚠ Plan critique` list below and the `Recommended: edit` count reference.

If any autofix round ran above, re-read the plan file from disk again — the summary below must reflect the post-autofix content, never the pre-fix version still sitting in context.

Then show:

- Clickable absolute path; 1-line gist + ticket key + branch.
- **Plan summary** from the file just read (never memory): Goal (header line), Approach (2-3 bullets from Architecture), Tasks (one line per `### Task N:`), Files touched (deduped), Risks/verification (omit if absent), `⚠ Base advanced` (only on rc 1 above — the overlapping file paths), `✓ Plan critique auto-fixed` (only when ≥1 objective finding was auto-applied above — one line per round, `<n> objective finding(s) fixed`), `⚠ Plan critique` (only when the gate findings set above is non-empty — list each as `[severity] location — message`, `(autofix cap reached)` findings included). Cap ~25 lines for the summary as a whole; if the `⚠ Plan critique` list alone would blow that, cap it to 8 lines and add a trailing `+<n> more (see plan file)` line instead of dropping entries silently.

Path + gist + summary MUST be visible markdown in the chat reply BEFORE the gate prompt — AskUserQuestion IS allowed here, but only in the same turn AFTER the text block (widget fields truncate; never their only carrier). Multiple plan files → one summary block each, single gate.

On rc 1 above, print immediately above the gate prompt: `Recommended: restart — base advanced on file(s) this plan also targets.` (mirroring how a `Recommended:` line reads elsewhere in this plugin). On rc 0 or rc 2, no such line — the gate looks exactly as it does today.

When the gate findings set above is non-empty, print a second line immediately above the gate prompt: `Recommended: edit — <n> finding(s) from plan critique.` (`<n>` = the gate findings set's size) — on its own line. When `plan_critique` is explicitly disabled (set to `none`), or the gate findings set is empty, no such line prints — this holds even if earlier rounds auto-fixed objective findings, since those no longer need an `edit`. The two `Recommended:` lines are independent and can both print together; when both fire, `Recommended: restart` prints first, `Recommended: edit` second.

The "Tasks" and "Files touched" bullets above already produce a task count and a deduped file count for the plan just read — reuse those two counts here, do not recompute them. A plan is "large" when its task count is greater than 5, or its file count is greater than 8. When the plan just read is "large" by either measure, print a third line immediately above the gate prompt — `Suggestion: consider spawn_task — plan has <n> tasks across <m> files.` (`<n>`/`<m>` = the two counts) — after any `Recommended:` line(s) above, on its own line. This suggestion never blocks the gate, never mentions a model (spawn_task takes none), and is independent of both `Recommended:` lines — it can appear alone, or alongside either or both of them.

> "Plan written to `<path>`. Review it:
> - **`y`** — proceed to hand-off (the caller's next step).
> - **`edit <feedback>`** — apply changes, re-present this gate.
> - **`restart`** — tell the caller a new direction is needed; editing won't fix it."

On `edit` → apply the requested changes to the plan file directly (targeted `Edit`, or a full internal rewrite of the affected tasks), then loop back to the top of this section (re-run the base-freshness check and re-dispatch `plan-reviewer` on the freshly edited file, same as any other re-entry). On `restart` → do not decide what this means; report back to the caller that the user chose `restart` and stop here — the caller (`simo-dev:task-intake`, `simo-dev:grill-me`, or any other invoker) owns what `restart` means in its own flow. No interactive user to answer the gate (detectable from the caller's own dispatch — e.g. `private/autopilot.py`'s `_plan_prompt` carries the literal token `NON-INTERACTIVE` in its prompt text) → skip the `y`/`edit`/`restart` prompt itself (there is no one to answer it), but still run everything above it (base-freshness check, the autofix rounds) and still produce the plan summary; append any remaining judgment findings from the gate findings set to the plan file itself, under a trailing `## Plan critique (unresolved)` heading, one `[severity] location — message` line each — so they surface wherever that caller's own human review happens (for `autopilot`, its existing Plan MR review), instead of being silently dropped. If the gathered context carries no ticket key at all (as `autopilot`'s headless invocation does not — its prompt names a `.ai-pipeline/{key}/ticket.txt` path, not a ticket key handed to `plan-write` as gathered context), the post-plan-comment call below no-ops on the empty key exactly as it does today — this preserves `autopilot`'s existing behavior of never posting a tracker comment from the plan-write step; it does not need a separate guard. A `Recommended:` line above is a recommendation, not a block — `y` still proceeds to hand-off regardless of what was recommended. Gate exits on `y`, `restart`, or the no-interactive-user fallback above — all three are terminal outcomes handled by returning control to the caller; only `edit` loops back within this section.

Once the gate above exits on `y` (interactively, or via the no-interactive-user fallback above, which also counts as `y` for this purpose): post a plan comment, same mechanism as `post_tracker_comment.py`'s other call sites in this plugin. Body = the "Plan summary" block already built for the gate prompt (goal, approach, tasks, files touched, risks/verification) — never the raw plan file, which can run long; the summary is already the right size for a ticket comment. Write it to a temp file, then the same `R`-bound call shape used elsewhere for tracker comments:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/post_tracker_comment.py" "<KEY-or-empty>" "<tmpfile>"
```

Announce `Plan: <script's stdout line>`.

## Done

The plan-critique and review gate above already ran before this point is reached — the caller does not need to run its own review. On the gate's `y` (interactive, or the no-interactive-user fallback), this section has already posted the plan comment (using whatever ticket key was in the gathered context — an empty key is the script's own existing no-op, unchanged) and now returns control to the caller for hand-off. On `restart`, this section returns control to the caller immediately with that outcome, and the caller decides what happens next.
