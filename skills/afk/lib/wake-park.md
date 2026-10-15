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
- [ledger-stop] src/b.py → review-loop
- [⚠ blocker] src/c.py still uncommitted, not in the MR
- [review-loop] 2 iters, 3 medium fixed, exited clean
- [🔖 deferred spawn] follow-up in another repo: bump the shared client — accept manually
- [deferred (needs decision)] In-scope improvement: `src/a.py` rename helper — two reasonable names
- [push] MR !123 created, pipeline running
```

## Park procedure (§6)

Run these steps when `simo-dev:mr-followup` settles (pipeline green or findings handled per its cap); a mid-flow ⚠ blocker park (`SKILL.md` §2, Red flags) skips the **Dirty-tree check** and runs the rest, its own blocker being the reason it parks:

1. **Dirty-tree check.** List `git -C "<worktree-abs>" status --porcelain --untracked-files=all` (`<worktree-abs>` as a literal). For every path it shows — the new path of a rename — except the plan's own decision-log file (`docs/plans/<plan-basename>-afk-log.md`), append `[⚠ blocker] <path> still uncommitted, not in the MR` to the decision-log: Write/Edit with a plan, the fenced `afk_log.py append` block in `SKILL.md` §5 without one. Any dirty path is reported, whoever left it. `git status` exits non-zero → append `[⚠ blocker] dirty-tree check could not run` instead, which counts as a logged entry below: an unread tree is never reported clean.
2. Disarm — in a Bash block opening with `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"`, since shell state does not survive between Bash calls: `python3 "$R/lib/afk_state.py" stop --repo "<worktree-abs>"` (writes a `stopped` entry for THIS repo+branch; a stale wakeup now exits at §0) and stop re-arming `ScheduleWakeup`. This file is Read-loaded, so `R` lands on the source tree here while the reader in `skills/afk/SKILL.md` §0 resolves the released cache — see that section's note on why the entry format has to stay frozen across a release boundary, and on which way the mismatch fails.
3. Print the recap in four buckets from the decision-log (no plan → read it with the `afk_log.py show` block in `SKILL.md` §1) — **decisions made** (numbered) / **⚠ blockers** / **🔖 deferred spawns awaiting your click** / **`deferred (needs decision)` In-scope improvements** — plus MR link + pipeline status. `[ledger-stop]` entries go under decisions only, never under blockers. Every other `[⚠ blocker]` entry goes under blockers as before, except the **Dirty-tree check**'s own entries: list a `still uncommitted, not in the MR` entry only when its path is in this park's listing, one line per path, and a `dirty-tree check could not run` entry only when this park's check failed — an earlier park's entry is not repeated. When the **Dirty-tree check** logged any entry, or the park is a mid-flow ⚠ blocker park, use the second opening instead of "Implementation complete", naming those paths, that the check could not run, or the mid-flow blocker. When no open MR exists yet, drop the MR link and the `merge` ask and ask the user to take over:

> "Implementation complete and on the MR, unattended. Decisions: 1) … 2) … . Blockers: … . Deferred spawns: … . In-scope improvements deferred (needs decision): … . MR: <link> (pipeline <status>). Reply `merge` to do the final merge, or a number to revisit that choice."
>
> "Not done, parked unattended: <uncommitted paths, or the blocker that stopped the run>. Decisions: 1) … (rest as above)."

Then stop and wait. `merge` → the ask is satisfied (user typed it) → run the merge. A number → revisit that decision, then re-present from the **Dirty-tree check** (step 1), since the revisit can change the tree.

**Protected-branch variant** (§0 guard hit): terminal state is `simo-dev:commit`'s commit+push gate instead — the same disarm and recap steps, without the **Dirty-tree check** (everything is uncommitted by design there), with the gate's own ask ("Commit + push to `<branch>`?") replacing the `merge` ask. `y` from the returning user satisfies the gate.
