---
name: tdd
description: Use when implementing any feature or bugfix — write the failing test before the production code, one vertical slice at a time.
---

# Test-Driven Development

**The rule:** no production code without a failing test first. Code written before its test is deleted and rewritten — never kept as reference, never "adapted while writing tests". Exceptions (throwaway prototypes, generated code, config files) require the user's explicit OK.

## Philosophy

Tests verify behavior through public interfaces, not implementation details. A good test reads like a specification and survives refactors; if renaming an internal function breaks a test, that test was testing implementation, not behavior. Prefer real code over mocks — mock only what is genuinely slow or external.

## Planning (before the first test)

- Confirm the public interface and which behaviors matter most — you can't test everything; prioritize critical paths and complex logic.
- Read `CONTEXT.md` if present so test names match the project's domain vocabulary; respect ADRs in the area.

## Anti-pattern: horizontal slices

Never write all tests first, then all code. Bulk-written tests test imagined behavior and the *shape* of things, not what the system does.

```
WRONG (horizontal):  RED: test1..test5   GREEN: impl1..impl5
RIGHT (vertical):    test1→impl1, test2→impl2, ...  (tracer bullet)
```

The first cycle is the tracer bullet: one test proving the path end-to-end. Each next test responds to what the previous cycle taught you.

## The loop

Per behavior, in order:

1. **RED** — write ONE minimal test: one behavior, clear name, real code.
2. **Verify RED** (mandatory, never skip) — run it; it must FAIL, for the right reason (feature missing — not a typo, not an error). Passes immediately → you're testing existing behavior, fix the test.
3. **GREEN** — write the simplest code that passes. No extra features, no speculative options, nothing beyond the test.
4. **Verify GREEN** (mandatory) — run it; it passes, all other tests still pass, output pristine (no warnings). Fails → fix the code, not the test.
5. **REFACTOR** — only while green: remove duplication, improve names, extract helpers. No new behavior. Never refactor while RED.
6. Next behavior → back to 1.

## Bug fixes

A bug is a missing test. Write the failing test that reproduces it, then fix through the loop. Never fix a bug without a test.

> Mock/test-utility pitfalls: [lib/testing-anti-patterns.md](lib/testing-anti-patterns.md) — load when writing test doubles or test utilities.

## Done when

- [ ] Every new behavior has a test that was watched failing for the expected reason
- [ ] Minimal code per test; all tests pass; output pristine
- [ ] Tests exercise public interfaces with real code (mocks only where unavoidable)
- [ ] Edge cases and error paths covered

Can't check a box? That slice skipped TDD — redo it.
