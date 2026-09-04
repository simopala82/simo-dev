# Debugging Techniques

## Root-cause backward tracing

Bugs often manifest deep in the call stack (file created in the wrong directory, DB opened with the wrong path). Fixing where the error appears treats a symptom. Trace the bad value backward — what called this with the bad value, and what produced it — until you reach the original trigger, then fix at the source.

```
symptom: gitInit ran in $HOME        → who passed that directory?
  ← WorkspaceManager.create(dir)     → who computed dir?
    ← config.projectDir = ''          ← ROOT: empty config default
```

Add an assertion/guard at the deepest point too (it turns silent corruption into a loud failure), but the FIX goes at the origin.

## Defense-in-depth validation

A single check for invalid data can be bypassed by other code paths, refactors, or mocks. After fixing the root cause, validate at EVERY layer the data passes through — entry point (reject invalid input at the API boundary), business logic (edge cases), environment guards (context-specific dangers), debug logging (for when other layers fail). Goal: make the bug structurally impossible, not just fixed once.

## Condition-based waiting

Arbitrary delays (`sleep`, `setTimeout`) in tests guess at timing and fail under load/CI. Wait for the actual condition instead:

```typescript
async function waitFor<T>(cond: () => T | undefined, timeoutMs = 5000): Promise<T> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const v = cond();
    if (v !== undefined) return v;
    await new Promise(r => setTimeout(r, 10));
  }
  throw new Error('condition not met in time');
}
```

Poll the condition you actually care about (file exists, message consumed, state flag set) with a hard timeout. A fixed sleep is acceptable only when timing itself is the behavior under test — document why.
