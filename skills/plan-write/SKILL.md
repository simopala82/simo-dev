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

## Global Constraints
[project-wide requirements — version floors, naming rules, repo conventions — one line each, exact values. Every task implicitly includes this section.]
---
```

`Depends on` is optional — set it only when the plan's approach assumes the shape of a ticket that has not merged yet (typically a blocking sub-task opened via `simo-dev:grill-me` and developed in another session). `simo-dev:plan-execute` step 1 re-derives that ticket's real status and merged diff before executing any task; a plan with no such assumption omits the field.

A whole-suite verification gate:
- States the baseline at a named branch-point SHA (`git merge-base HEAD origin/<base>`) and gates on the **delta** above it, never the raw total.
- On a mismatch at execution time, re-measure the baseline at the current merge-base and reconcile — never edit the number to match the output.

Logged in the author's own claim-hygiene notes (keep a similar file if you have one): "A recorded number can age for reasons that have nothing to do with what it measures."

## Task shape

A task is the smallest unit with its own test/verify cycle — it ends with an independently verifiable deliverable. Each step is one action (2-5 min).

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

## Done

Return to the caller — `simo-dev:task-intake` presents its plan review gate (step 7.5) and owns the execution hand-off.
