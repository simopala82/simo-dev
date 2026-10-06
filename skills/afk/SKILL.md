---
name: afk
description: Use when stepping away mid-task and wanting the canonical dev flow driven to completion unattended — phrases "afk", "unattended", "simo-dev:afk". Auto-answers content gates, retries blockers, arms a wakeup before the token limit, parks before the final merge.
---

# Dev AFK — unattended completion of the canonical flow

Mid-session toggle: detect where you are in the canonical flow and drive it to **MR + `simo-dev:mr-followup`** without interruption, leaving the user only the final merge. A **driver over the existing flow** — it calls the same skills (`executing-plans`, `simo-dev:review-loop`, `simo-dev:commit`, `simo-dev:mr-followup`) and injects one rule: replace each gate's interactive prompt with its documented recommended/default answer, and log it.

## When to use

- User is stepping away and wants the task carried to MR without stalls; works from ANY point in the flow.
- Not for: starting from scratch unattended (an unattended from-scratch build tool from a different plugin); tasks needing interactive direction approval.

**Launch as `/loop simo-dev:afk`** (no interval — self-paced): the §4 dead-man's-switch uses `ScheduleWakeup`, honored only inside `/loop`. Invoked plain → works but no auto-resume after a rate-limit pause; say so once and continue.

Every `afk_state.py` call below runs in a Bash block opening with `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"` — shell state does not survive between Bash calls — and always passes `--repo "<worktree-abs>"`, the absolute path of the worktree this task is using (the main checkout, if the task has none), so repo+branch identity never depends on the Bash tool's own cwd. Same requirement in `lib/wake-park.md`. On a wakeup re-fire, re-derive `<worktree-abs>` from the `RESUME:` line at the top of the decision-log (§5) — never assume it from the ambient cwd.

## Boundaries (inviolable — even unattended, even under "no interruptions" pressure)

| Action | AFK behavior |
|---|---|
| push, MR-create, re-push | **Always automatic** on feature branches. Commit+push to a protected branch always asks — that gate is the park point (§0 guard, §6). |
| `git merge` / `glab mr merge` / `gh pr merge` | **NEVER.** Hard human gate at the park point. |
| `git push --force[-with-lease]` | **NEVER.** Forbidden in every mode. |
| a session-spawn follow-up | **Suppressed** → logged as 🔖 deferred (needs a manual chip click). |
| a fresh user message mid-run | **Stop AFK** — disarm, go interactive (below). |

"The MR is green, I'll just merge to finish" is exactly the rationalization this table forbids.

## User interrupt = stop (overrides "never stop")

While AFK is active (`afk_state.py is-active` exits 0), any **genuine user message** (not the wakeup re-fire) = the user is back → **stop driving**:

1. `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; python3 "$R/lib/afk_state.py" stop --repo "<worktree-abs>"` — tombstone for THIS repo+branch (already-armed wakeups exit at §0; other sessions untouched). Binding spelled out here rather than left to the convention above, same as `lib/wake-park.md`'s disarm: an unbound `R` exits rc 2 and writes no tombstone, while items 2-4 still announce "AFK off" — after which a queued wakeup re-fires and AFK resumes driving over the user.
2. No `ScheduleWakeup` this turn → the `/loop` winds down.
3. Append `[interrupt] user took over → AFK disarmed` to the decision-log; keep the `RESUME:` line.
4. One-line ack ("AFK off — you're driving. `simo-dev:afk` to resume."), then answer interactively — §2 no longer applies.

## 0. Arm (or honor a prior stop)

**Stop-check — first action on every entry, including a wakeup re-fire.** `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; python3 "$R/lib/afk_state.py" is-stopped --repo "<worktree-abs>"` (checks ONLY this repo+branch). Binding spelled out at the site, not left to the convention above: this is the one call whose rc the body reads as a boolean, so an unbound `R` exits 2, falls through to Arm, and drives over a stop the user asked for. Exits 0 →
- user explicitly (re)started AFK this turn → `afk_state.py arm` and continue below;
- else (stale wakeup) → print "AFK is off for this task — `simo-dev:afk` to resume." and **exit**.

**The tombstone this reads can be written by a different copy of `afk_state.py`.** The `afk_state.py` calls in this body run the released cache while that directory exists — a rendered body gets the placeholder substituted — while `lib/wake-park.md`'s park-time disarm runs the source tree, because the Read path substitutes nothing. (Once orphan cleanup removes the cache dir both sides resolve source and there is no split at all.) Both write `~/.claude/state/afk`, whose entries are `<repo-id>:<branch>:<state>:<epoch>` parsed with `rsplit(':', 3)`, with malformed entries purged on write. So between a merge and the next release **plus** a session restart the two can disagree, and the two legs of that format fail in opposite directions — the same split `hooks/review_record.py` documents for `review-loop`:

- **Structural**, by either of two mechanisms, both landing on `absent` — the "never armed" answer — so the bullet above does not fire and AFK keeps driving unattended. Toward *more* automation. A field added or dropped fails `_parse` outright; a `repo_identity` change parses cleanly and the lookup simply misses, which is the same distinction `hooks/review_record.py` draws for its own store.
- **The epoch's units or meaning**: it still parses, but `_live_entries` keeps an entry while `now - epoch <= TTL_SECONDS`, so re-basing seconds to milliseconds makes that difference negative and the `stopped` entry never expires. `is-stopped` then keeps exiting 0 and AFK refuses to resume. Toward *less*.

Treat the entry format, the epoch's units and `repo_identity` as frozen unless you land both sides and restart.

**Arm.** The publish path is unconditional for everyone (`commit` step 10 always enters `mr-followup`); AFK only arms its own liveness state. A protected-branch repo parks at its own commit+push gate (hard human gate). Emit one line, then:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/afk_state.py" arm --repo "<worktree-abs>"
```

## 1. Detect current state & resume

Check for a `RESUME:` marker in the decision-log first (fresh window lands here). Else infer:

| Observed | Resume at |
|---|---|
| intake analysis shown, no `y` | answer direction gate → continue |
| plan written, plan-review pending | answer plan-review → hand off |
| plan exists, code partially done | resume `executing-plans` |
| implementation done, not reviewed | `simo-dev:review-loop` |
| review clean, not committed | `simo-dev:commit` |
| pushed + MR exists | `simo-dev:mr-followup` |

Then run forward to the park point.

## 2. Gate-override rule (the core)

While AFK is active, auto-answer every gate and **append each answer to the decision-log**:

- **Direction gate** → `y` (recommended direction). If `deep-dive` would be the default, take the recommended candidate instead, log `deep-dive skipped → took recommended <X>`. Never enter brainstorming/grill (they block on the user).
- **Plan-review gate** → shape-aware (task-intake step 7.5's own gate). On Shape A (no spawn offered — the blockquote has only `y`/`edit <feedback>`/`restart`, no `n` option, and no `Recommended: spawn_task` line above it) → `y`, with the same conditional log line as today: when it printed `Recommended: edit — <n> finding(s) from plan critique.`, also log `[gate] plan-review → y (despite <n> finding(s) from plan critique)`; when it printed no such line (`plan_critique` explicitly set to `none`, or the dispatch returned zero findings), no extra log line. On Shape B (recognizable by the `n` option and the `Recommended: spawn_task — ...` line above the gate) → `n` (never `y`, which now means spawn and would trigger the suppressed hand-off in §3) — log `[gate] plan-review (spawn offered) → n (afk never spawns)`.
- **`simo-dev:review-loop`** → run its capped iterations; a clean run cascades into `simo-dev:commit` on its own (step 8); on unresolved findings pick option 1 (skip → `simo-dev:commit`), logged.
- **`simo-dev:commit` step 10** → enters `simo-dev:mr-followup` on its own (unconditional). Push automatic. A protected-branch commit+push gate IS hard — never auto-answer, park there (§6).
- **Any other fork with no clear recommendation** → most conservative / most reversible option, logged.
- **An In-scope improvement needing a decision** (`docs/canonical-flow.md` "In-scope improvements") → skip it, log `[deferred (needs decision)] …` — never answered as a `[fork]`.

## 3. Hand-off override

- Intake's plan-review gate (task-intake step 7.5) → forced **in-session** via §2's `n` answer whenever the spawn-offered shape appears. Intake step 8's profile/mode table (reached only via the `table-pick` routing, when step 7.5's gate offered no spawn choice at all) → still forced **in-session** regardless of what the table would otherwise silently pick for a "Multi-step / verbose / context-heavy" plan; afk never lets the table's own `spawn_task` pick stand. Never a session-spawn follow-up, through either path.
- Any session-spawn follow-up the flow would fire → suppressed, logged 🔖 `deferred spawn: <title> — accept manually after review`.
- Allowed: parallel-`Agent` (`subagent_type`), background `Agent`/`Bash` (`run_in_background: true`) — except a `simo-dev:code-reviewer` dispatch, always `run_in_background: false` (`simo-dev:code-review-protocol` item 2).

## 4. Blocker handling — retry, don't stop

Wrap external actions in retry discipline (log `⚠`): re-auth + retry on SSO failures, backoff on transient failures, `ScheduleWakeup` for long waits. **Keep one `ScheduleWakeup` always armed** (prompt `simo-dev:afk`, delay 1800-3600s), refreshed every turn — the token-limit dead-man's-switch.

> Retry/wakeup detail: `lib/wake-park.md` — load when arming the wakeup or handling a blocker.

## 5. Decision-log (single source of truth)

Append-only; survives compaction. Path: `docs/plans/<plan-basename>-afk-log.md` (or `~/.claude/state/afk-<plan-or-branch>.md` if no plan). Single `RESUME:` line at the top, updated each step. Entry format examples: `lib/wake-park.md`.

## 6. Park (terminal state — before merge)

When `simo-dev:mr-followup` settles: disarm (`afk_state.py stop`, stop re-arming wakeups), print the 4-bucket recap (decisions / ⚠ blockers retried / 🔖 deferred spawns / `deferred (needs decision)` In-scope improvements) + MR link + pipeline status, ask the user to reply `merge` (or a number to revisit a decision), then stop and wait. Protected-branch variant parks at the commit+push gate instead.

> Full park procedure + recap wording: `lib/wake-park.md` — load when parking.

## Red flags — STOP

- About to merge unattended → NO. Park, wait for `merge`.
- About to `git push --force` → NO, never.
- About to fire a session-spawn follow-up → suppress and log.
- About to enter `brainstorming`/`grill-me` → NO (they block on the user); take the recommended option, log.
- User message arrived and you're tempted to keep driving → NO: that IS the stop signal, disarm and go interactive.
- About to stop because a *command* failed → retry/reschedule first; only park at the terminal state. **Exception, and the rule carries no attempt cap so this is the whole brake:** `simo-dev:commit` step 5's pre-push guard classifies its own `STOP:` messages, and two of them park instead — a guard that never ran or never answered will answer the same way forever. Follow that paragraph, not this line, on any `STOP: could not verify MR state`. Same for any `simo-dev:commit` step 9 `STOP: could not resolve …` (GitLab username, MR title, MR target branch) and for a failed step-9 `glab mr create` / `gh pr create` call: follow that step's per-cause recovery paragraph, not this line.

## Honors

`DEV_FLOW_NO_*` env vars (tracker, worktree). Ticket forward-transition to `In Progress` stays (idempotent, part of intake).
