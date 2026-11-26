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
- YAGNI ruthlessly — cut unnecessary features from every design. It applies to features in the design, not to In-scope improvements (`docs/canonical-flow.md` "In-scope improvements").
- Stop when no open branches remain.

## Domain capture (inline)

While grilling, capture domain knowledge as it crystallizes — do not batch:

- **Sharpen fuzzy terms.** When the user uses a vague/overloaded term, propose a precise canonical term, then write it into `CONTEXT.md` (repo root, or the context dir if a `CONTEXT-MAP.md` exists). `CONTEXT.md` is a glossary only — zero implementation detail. `CONTEXT.md` missing in this repo → ask once "Create `CONTEXT.md`?" before the first write; approved → create, every later term in this or a future session writes straight in, no gate.
- **Offer an ADR sparingly.** Only when all three hold: hard to reverse + surprising without context + the result of a real trade-off. Write it under `docs/adr/NNNN-<slug>.md`. If any of the three is missing, skip it. `docs/adr/` missing in this repo → same shape: ask once before the first ADR, approved → create the directory + `0001-<slug>.md`, every later ADR writes straight in.

Both create-gates are the same policy `skills/commit/lib/knowledge-harvest.md`'s home-selection gate uses for the harvest step — cross-reference, don't re-describe, so the two never drift apart again.

## Design mode

For open scope / architecture-level work (task-intake 4b-bis escalation, or on request).

**Hard gate: no implementation skill, code, or scaffolding until a written design is presented AND the user approves it — and, when step 5 below builds a mockup, until that mockup is approved too. "Too simple to need a design" is not an exemption — the design may be a few sentences, but it must be presented and approved.**

1. **Explore context** — files, docs, recent commits, and (on a standalone/on-request invocation not escalated from `task-intake`) `~/.claude/reference/` filenames matching any external service/product named in the task — before asking anything. Describe facts, never paste literal secrets (passwords, tokens, keys) that some reference files inline (e.g. `local-services.md`) — step 7 below commits this into a spec file.
2. **Scope check** — if the request spans multiple independent subsystems, flag it immediately and decompose: each sub-project gets its own design → plan cycle; grill the first one.
3. **Grill** per the rules above (purpose, constraints, success criteria).
4. **Propose 2-3 approaches** with trade-offs; lead with your recommendation and why.
5. **Build a mockup, when UX/UI-facing** — if the task or spec describes a user-facing screen, form, layout, component, or interaction worth seeing or testing (or the user explicitly asks for one), build exactly one self-contained mockup for the recommended approach only — never one per candidate approach — from the fixed skeleton in `lib/mockup-template.md`, never the visualize `read_me` tool (forbidden plugin-wide, see `lib/tests/test_no_read_me_precondition.py`). Write it to `docs/specs/YYYY-MM-DD-<topic>-mockup.html`, same date/topic as the design spec written in step 7 below. When `mcp__visualize__show_widget` is not yet callable (listed only as deferred, or not listed at all), run `ToolSearch` `select:mcp__visualize__show_widget` once first, never skipped because the name is unlisted; no schema, or no `ToolSearch`, counts as unavailable. If `mcp__visualize__show_widget` is available, preview it inline as you go; if it's unavailable or errors, skip the preview silently — the file on disk is unaffected. Otherwise (the task isn't UX/UI-facing), skip this step entirely.
6. **Present the design** in sections scaled to complexity (a few sentences to ~250 words each), asking after each section whether it looks right. Cover architecture, components, data flow, error handling, testing. When step 5 built a mockup, present it as one of these sections too.
7. **Write the spec** to `docs/specs/YYYY-MM-DD-<topic>-design.md`, then self-review inline: placeholders/TBDs, internal contradictions, single-plan scope, ambiguous requirements (pick one reading, make it explicit). Fix and move on. When step 5 built a mockup, reference its path here.
8. **User review gate** — "Spec written to `<path>`. Review it before we plan." Approval is not final until both the written design and, when one was built, the mockup are approved. Wait; apply changes and re-check until approved.

## Output


**Escalated from `simo-dev:task-intake`** (its `deep-dive`): hand the decisions or the approved spec back to task-intake and never invoke `simo-dev:plan-write` here — task-intake's step 7 puts them in the plan brief and dispatches the writer subagent, so the plan gets one critique and one gate.

**Standalone run** (not escalated): no plan here — `simo-dev:plan-write` holds only the writer subagent's rules and has no gate of its own. End with the outcome and the next step:
- **Decisions mode:** the resolved decisions as a numbered list, each with the rejected alternative and why in one line.
- **Design mode:** the approved spec's path (and the mockup's). Never an implementation skill next.

Then tell the user that `/simo-dev:task-intake` plans it, with that list or the spec path as its prompt: its direction gate re-checks the outcome against the code, and its step 7 carries it into the plan brief.
