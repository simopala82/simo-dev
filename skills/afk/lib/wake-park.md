# AFK wake + park mechanics

Loaded on demand from `afk` §4-§6.

Every `afk_state.py` / `afk_log.py` call in this file also passes `--repo "<worktree-abs>"`, same convention as `SKILL.md` §0 — on a wakeup re-fire `<worktree-abs>` comes from context first, then is confirmed by the decision-log's `RESUME:` line.

## Blocker handling detail (§4)

- **SSO / auth failure** → run the re-auth command, retry the action (bounded, ~3× with backoff). Log `⚠`.
- **Transient command failure / rate-limit hint** → exponential backoff in-turn; long wait → `ScheduleWakeup`.
- **Approaching context limit** → rely on harness auto-compaction; all state is on disk (decision-log + plan + `RESUME:` marker), a fresh window continues cleanly.

## Token-limit dead-man's-switch

No reliable mid-turn quota signal exists, so keep one `ScheduleWakeup` always armed, refreshed every turn while AFK is active:

- `prompt`: `simo-dev:afk` (re-invokes the skill on wake).
- `delaySeconds`: long fallback (1800–3600) — safety net, not active polling.

If a usage limit kills the turn, the queued wakeup fires later. On wake, §0's stop-check runs first — a user interrupt or park leaves a `stopped` entry (`afk_state.py`), so the wakeup exits without resuming. Otherwise: read `RESUME:` (no plan → the `afk_log.py show` block in `SKILL.md` §1), try a cheap action to check quota — back → continue; still limited → re-arm (1h clamp → polls roughly hourly). Re-arm after every meaningful step (`afk_state.py arm` + fresh `ScheduleWakeup`) so the switch never goes stale.

## Decision-log format (§5)

No plan → entries go through `afk_log.py append` and the top line through `afk_log.py resume` (`SKILL.md` §5).

```
RESUME: <current step + enough context to continue cold>
## AFK run
- [gate] direction → y (recommended A: reuse handler Y)
- [fork] no rec at X → conservative: in-place, reversible
- [⚠ blocker] SSO expired on glab call → re-auth ok, retried
- [review-loop] 2 iters, 3 medium fixed, exited clean
- [🔖 deferred spawn] follow-up in another repo: bump the shared client — accept manually
- [deferred (needs decision)] In-scope improvement: `src/a.py` rename helper — two reasonable names
- [push] MR !123 created, pipeline running
```

## Park procedure (§6)

When `simo-dev:mr-followup` settles (pipeline green or findings handled per its cap):

1. Disarm — in a Bash block opening with `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"`, since shell state does not survive between Bash calls: `python3 "$R/lib/afk_state.py" stop --repo "<worktree-abs>"` (writes a `stopped` entry for THIS repo+branch; a stale wakeup now exits at §0) and stop re-arming `ScheduleWakeup`. This file is Read-loaded, so `R` lands on the source tree here while the reader in `skills/afk/SKILL.md` §0 resolves the released cache — see that section's note on why the entry format has to stay frozen across a release boundary, and on which way the mismatch fails.
2. Print the recap in four buckets from the decision-log (no plan → read it with the `afk_log.py show` block in `SKILL.md` §1) — **decisions made** (numbered) / **⚠ blockers retried** / **🔖 deferred spawns awaiting your click** / **`deferred (needs decision)` In-scope improvements** — plus MR link + pipeline status:

> "Implementation complete and on the MR, unattended. Decisions: 1) … 2) … . Blockers retried: … . Deferred spawns: … . In-scope improvements deferred (needs decision): … . MR: <link> (pipeline <status>). Reply `merge` to do the final merge, or a number to revisit that choice."

Then stop and wait. `merge` → the ask is satisfied (user typed it) → run the merge. A number → revisit that decision, re-present.

**Protected-branch variant** (§0 guard hit): terminal state is `simo-dev:commit`'s commit+push gate instead — same 2 steps, with the gate's own ask ("Commit + push to `<branch>`?") replacing the `merge` ask. `y` from the returning user satisfies the gate.
