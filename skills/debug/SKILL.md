---
name: debug
description: Use on any bug, unexpected test failure, or unexplained behavior, before proposing fixes — root cause first, fix second.
---

# Systematic Debugging

**The rule:** no fixes without root-cause investigation first. Symptom patches are failure. This applies MORE under time pressure, when the quick fix "seems obvious", and when previous fixes didn't work — guessing thrashes longer than the process takes.

Complete each phase before the next.

## Phase 1 — Root cause

- Read the full error: complete stack trace, line numbers, codes. Errors often contain the answer.
- Build the repro loop: reproduce the failure reliably. Not reproducible → gather more data, don't guess.
- Check recent changes: git diff, new dependencies, config, environment.
- Multi-component systems: instrument each component boundary (what enters, what exits, config propagation) and run ONCE to see WHERE it breaks — before touching anything.
- Bad value deep in the stack → trace it backward to its origin (see lib).

**Done when:** the failure has a deterministic, fast, agent-runnable repro AND the failing component is identified.

## Phase 2 — Pattern comparison

Find similar working code in the codebase; read reference implementations COMPLETELY (no skimming); list every difference between working and broken, however small; note the dependencies and config each assumes.

**Done when:** the delta set between working and broken is explicit.

## Phase 3 — Hypothesis

One stated hypothesis: "I think X is the root cause because Y." Test it with the SMALLEST possible change, one variable at a time. Wrong → new hypothesis, never stack fixes. Don't know → say "I don't understand X" and investigate; never pretend.

**Done when:** the hypothesis is confirmed by a minimal test (or replaced).

## Phase 4 — Fix

1. Failing test first that reproduces the bug (`simo-dev:tdd`) — automated, or a one-off script if no framework.
2. ONE fix addressing the root cause. No "while I'm here" changes.
3. Verify with `simo-dev:verify-done`: test passes, nothing else broke, original symptom gone.
4. Fix didn't work → back to Phase 1 with the new information. **After 3 failed fixes STOP**: each fix revealing a new problem elsewhere means the architecture is wrong, not the hypothesis — discuss with the user before attempt 4.

**Done when:** the reproducing test passes and the full suite is green. A found root cause is almost always durable knowledge — capture it inline (CONTEXT.md / ADR / reference file), don't batch.

## No root cause found

Only after completing all phases: document what was investigated, implement appropriate handling (retry, timeout, clear error), add monitoring for the next occurrence. Most "no root cause" verdicts are incomplete Phase 1.

> Deep techniques (backward tracing, defense-in-depth, condition-based waiting): [lib/techniques.md](lib/techniques.md) — load when Phase 1 stalls or the fix needs layered defenses.
