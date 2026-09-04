# Testing Anti-Patterns

Load when writing or changing tests, adding mocks, or tempted to add test-only methods to production code. Core rule: **test what the code does, not what the mocks do.**

## 1. Testing mock behavior

```typescript
// WRONG — verifies the mock exists, says nothing about the component
expect(screen.getByTestId('sidebar-mock')).toBeInTheDocument();
// RIGHT — unmock it, or test the host component's real behavior
expect(screen.getByRole('navigation')).toBeInTheDocument();
```

Before asserting on any mock element ask: am I testing real behavior or mock existence? Mock existence → delete the assertion or unmock.

## 2. Test-only methods on production classes

```typescript
// WRONG — Session.destroy() exists only for afterEach()
class Session { async destroy() { ... } }
// RIGHT — cleanup lives in test utilities
export async function cleanupSession(session: Session) { ... }
```

Before adding a method: only tests use it → put it in test utils. Also check the class actually owns that resource's lifecycle.

## 3. Mocking without understanding

```typescript
// WRONG — mocked method had a side effect (config write) the test depends on
vi.mock('ToolCatalog', ...); // duplicate detection now never triggers
// RIGHT — mock at the lower level (the slow/external op), preserve behavior
vi.mock('MCPServerManager');
```

Before mocking: what side effects does the real method have, and does the test depend on any? Unsure → run the test against the real implementation first, then mock minimally at the right level. "I'll mock this to be safe" is the red flag.

## 4. Incomplete mocks

Mock the COMPLETE data structure as it exists in reality, not just the fields your test uses — downstream code depending on an omitted field fails silently while the test stays green. Check the real response/schema; when uncertain include all documented fields.

## Mock complexity check

Mock setup longer than the test logic, mocks missing methods the real component has, test breaking when the mock changes → consider an integration test with real components instead; it is often simpler.

TDD prevents all of these: watching the test fail against real code first is what exposes a mock-shaped lie.
