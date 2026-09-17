---
name: grill-me
description: Use when the user wants to stress-test a plan or design, get grilled on it, mentions "grill me" — or when simo-dev:task-intake escalates a deep-dive (decisions mode for 2-4 discrete open decisions; design mode for open scope / architecture-level work).
---

# Dev Grill Me

Resolve every open decision in a plan or design before implementation.

## Rules (both modes)

- Walk the design tree branch by branch. Resolve dependencies between decisions one at a time.
- Ask **one question per turn**. Multiple choice when applicable.
- For each question, provide a **recommended answer** and the trade-off in one line.
- If a question can be answered by exploring the codebase, **explore instead of asking**.
- YAGNI ruthlessly — cut unnecessary features from every design.
- Stop when no open branches remain.

## Domain capture (inline)

While grilling, capture domain knowledge as it crystallizes — do not batch:

- **Sharpen fuzzy terms.** When the user uses a vague/overloaded term, propose a precise canonical term, then write it into `CONTEXT.md` (repo root, or the context dir if a `CONTEXT-MAP.md` exists). `CONTEXT.md` is a glossary only — zero implementation detail. `CONTEXT.md` missing in this repo → ask once "Create `CONTEXT.md`?" before the first write; approved → create, every later term in this or a future session writes straight in, no gate.
- **Offer an ADR sparingly.** Only when all three hold: hard to reverse + surprising without context + the result of a real trade-off. Write it under `docs/adr/NNNN-<slug>.md`. If any of the three is missing, skip it. `docs/adr/` missing in this repo → same shape: ask once before the first ADR, approved → create the directory + `0001-<slug>.md`, every later ADR writes straight in.

Both create-gates are the same policy `skills/commit/lib/knowledge-harvest.md`'s home-selection gate uses for the harvest step — cross-reference, don't re-describe, so the two never drift apart again.

## Design mode

For open scope / architecture-level work (task-intake 4b-bis escalation, or on request).

**Hard gate: no implementation skill, code, or scaffolding until a written design is presented AND the user approves it. "Too simple to need a design" is not an exemption — the design may be a few sentences, but it must be presented and approved.**

1. **Explore context** — files, docs, recent commits, and (on a standalone/on-request invocation not escalated from `task-intake`) `~/.claude/reference/` filenames matching any external service/product named in the task — before asking anything. Describe facts, never paste literal secrets (passwords, tokens, keys) that some reference files inline (e.g. `local-services.md`) — step 6 below commits this into a spec file.
2. **Scope check** — if the request spans multiple independent subsystems, flag it immediately and decompose: each sub-project gets its own design → plan cycle; grill the first one.
3. **Grill** per the rules above (purpose, constraints, success criteria).
4. **Propose 2-3 approaches** with trade-offs; lead with your recommendation and why.
5. **Present the design** in sections scaled to complexity (a few sentences to ~250 words each), asking after each section whether it looks right. Cover architecture, components, data flow, error handling, testing.
6. **Write the spec** to `docs/specs/YYYY-MM-DD-<topic>-design.md`, then self-review inline: placeholders/TBDs, internal contradictions, single-plan scope, ambiguous requirements (pick one reading, make it explicit). Fix and move on.
7. **User review gate** — "Spec written to `<path>`. Review it before we plan." Wait; apply changes and re-check until approved.

## Output

**Decisions mode:** summarize the resolved decisions in a numbered list. Hand off to `simo-dev:plan-write` (or back to `simo-dev:task-intake`). `simo-dev:plan-write` runs its own plan-critique and review gate before returning; do not expect a silent hand-off — wait for its `y`/`edit`/`restart` outcome the same as `task-intake` would. On `restart`, since `grill-me` (standalone, not escalated from `task-intake`) has no "step 4" of its own to fall back to, treat it the same as any open branch this skill would grill on its own — resume grilling from wherever the design/decisions left off. A `grill-me` invocation with no worktree yet passes its own current repo checkout as the "worktree-abs" context and an empty ticket key if none exists — both are already valid inputs to `plan-write`'s gate per the underlying scripts' own existing CLI contracts (`check_base_freshness.py --repo` defaults to `.` and still does real work — a fetch and a base-freshness comparison, not a no-op, and can legitimately recommend `restart`; `post_tracker_comment.py` is the one that truly no-ops on an empty key), so no new plumbing is needed here.

**Design mode:** the approved spec file. The ONLY next step is `simo-dev:plan-write` — never an implementation skill. `simo-dev:plan-write` runs its own plan-critique and review gate before returning; wait for its `y`/`edit`/`restart` outcome. On `restart`, since `grill-me` (standalone, not escalated from `task-intake`) has no "step 4" of its own to fall back to, treat it the same as any open branch this skill would grill on its own — resume grilling from wherever the design/decisions left off. A `grill-me` invocation with no worktree yet passes its own current repo checkout as the "worktree-abs" context and an empty ticket key if none exists — both are already valid inputs to `plan-write`'s gate per the underlying scripts' own existing CLI contracts (`check_base_freshness.py --repo` defaults to `.` and still does real work — a fetch and a base-freshness comparison, not a no-op, and can legitimately recommend `restart`; `post_tracker_comment.py` is the one that truly no-ops on an empty key), so no new plumbing is needed here.
