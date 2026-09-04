# Findings-agent prompt skeleton (step 5)

Loaded on demand from `mr-review` step 5 when dispatching the findings agent.

```
Critical-only review. Report ONLY:
- Security vulnerabilities (injection, auth bypass, secret leakage, missing validation of external input, SSRF, unsafe deserialization)
- Data loss / corruption risks (destructive migrations without backfill, wrong transaction boundaries, lost writes)
- Breaking bugs (logic errors, wrong control flow, race conditions, resource leaks)
- Severe architectural or invariant violations

Out of scope — DO NOT MENTION: style, nits, low severity, perf hints, doc gaps, test-coverage commentary, praise, refactor ideas, generic summaries.

Pass schema = <contents of ~/.claude/local-marketplace/simo-dev/lib/schemas/findings.json>.

For each mitigated/blocker finding, attach a per_finding_mermaid diagram of the SPECIFIC code path the finding is about, and set per_finding_diagram_type.

Choose the diagram type:
- flowchart (`flowchart LR`): the data/control path that reaches the bug.
- sequence (`sequenceDiagram`): a cross-service/actor call ordering where the issue lives.
- before_after (`flowchart` with `subgraph "Before"` / `subgraph "After"`): the finding is a behavior delta.
- state (`stateDiagram-v2`): a wrong or missing status/lifecycle transition.

Highlight the PROBLEM node (the failure point, not a change):
- flowchart / before_after / state: add `classDef problem fill:#ffb3b3,stroke:#c00` and tag the failing node `:::problem`; leave context nodes plain.
- sequence: wrap the bad message in a `rect` or add `Note over X: bug`.

Rules:
- ALL node labels double-quoted: `A["..."]`, decision `C{"..."}`. NEVER backslash-escape brackets (`\[`, `\]`) — backslash is not an escape character in mermaid. Use `<br/>` for line breaks. Inside a label, write a double quote as `#quot;` — `'` only if a single quote is what you meant — and a literal angle bracket as `#lt;` / `#gt;` — those are mermaid's own escapes, and a bare `<type>` is read as an HTML tag and disappears from the rendering. NEVER HTML entities (`&quot;`, `&lt;`, `&gt;`): this diagram is written straight to the output file, with no normalization step to repair them.
- Concrete sample data ONLY on the 1-2 edges central to the failure (e.g. `B["rates=[111,222,333]"]`). Do NOT label every edge.
- Keep it focused: ~4-8 nodes. Drop nodes that don't explain the bug.
- For resolved findings, skip the diagram (one line is enough).

Diff:
<paste from $MRTMP/review_diff.txt; if > 50k chars, truncate with marker>

Changed files:
<from git diff --stat>
```

## Drift block (append only when `[ -s "$MRTMP/review_jira_full.txt" ]`, step 4b)

```
Intended scope (from the linked ticket — the diff was flagged as diverging from it):
<paste contents of $MRTMP/review_jira_full.txt>

Ticket-scope rule: raise a finding from this ticket ONLY if the diff OMITS or CONTRADICTS a requirement that independently meets the critical bar (security / data-loss / breaking). Do NOT raise findings for deferred nice-to-haves, future phases, or scope the author may legitimately have split into another MR. Drift that is not itself critical is reported in the md drift section, not here.
```
