### ⚠ Ticket drift — <jira_key>
<drift_note from review_pres.json>

<details from $MRTMP/review_jira_full.txt — intended scope vs what the diff delivers>

### N. <semaphore> <title> — `<file>:<line_range>`
<description>

```mermaid
<per_finding_mermaid>
```

...

---
**Commit reviewed:** <head_sha>
**Merge verdict:** <overall semaphore> <one-line rationale>

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `DEV_REVIEW_MR_OUT_DIR` | `~/Documents/cr` | Output directory. Auto-created if missing. |
| `DEV_REVIEW_MR_AGENT_TYPE` | `simo-dev:code-reviewer` | Subagent type for step 5. |
| `DEV_REVIEW_JIRA_TIMEOUT` | `15` | Per-call scope_fetch_cmd timeout (seconds), set in jira_scope.py. |

**Ticket scope.** When the source branch carries a ticket key, the review fetches the ticket summary + description (light) and feeds it to the presentation. If the diff diverges from that scope, the full ticket (description + recent comments) is fetched and shown in a "⚠ Ticket drift" section, and a guarded ticket-scope rule is added to the findings step. The fetch is delegated to whatever command `tracker_config.py` has configured as `scope_fetch_cmd` for the key's prefix — step 4/4b ask and remember it on first use. All ticket access degrades silently — a `.claude/no-tracker` marker in the reviewed repo, a missing key, an unconfigured or explicitly-declined (`-`) `scope_fetch_cmd`, or any adapter failure leaves the review identical to its no-ticket output.
