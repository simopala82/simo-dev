---
name: afk
description: Use when stepping away mid-task and wanting the canonical dev flow driven to completion unattended — phrases "afk", "unattended", "simo-dev:afk". Auto-answers content gates, retries blockers, arms a wakeup before the token limit, parks before the final merge.
---

# Dev AFK — unattended completion of the canonical flow

Mid-session toggle: detect where you are in the canonical flow and drive it to **MR + `simo-dev:mr-followup`** without interruption, leaving the user only the final merge. A **driver over the existing flow** — it calls the same skills (`simo-dev:plan-execute`, `simo-dev:review-loop`, `simo-dev:commit`, `simo-dev:mr-followup`) and injects one rule: replace each gate's interactive prompt with its documented recommended/default answer, and log it.

## When to use

- User is stepping away and wants the task carried to MR without stalls; works from ANY point in the flow.
- Not for: starting from scratch unattended (an unattended from-scratch build tool from a different plugin); tasks needing interactive direction approval.
- Only inside an active simo-dev flow (`simo-dev:task-intake` already ran): §0's flow check stops it otherwise.

**Launch as `/loop simo-dev:afk`** (no interval — self-paced): the §4 dead-man's-switch uses `ScheduleWakeup`, honored only inside `/loop`. Invoked plain → works but no auto-resume after a rate-limit pause; say so once and continue.

Every `afk_state.py` and `afk_log.py` call below runs in a Bash block opening with `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"` — shell state does not survive between Bash calls — and always passes `--repo "<worktree-abs>"`, the absolute path of the worktree this task is using (the main checkout, if the task has none), so repo+branch identity never depends on the Bash tool's own cwd. Same requirement in `lib/wake-park.md`. On a wakeup re-fire, take `<worktree-abs>` (and the plan path, if any) from context / the compaction summary first — the no-plan log cannot be found without it — then confirm it against the `RESUME:` line at the top of the decision-log (§5); never assume it from the ambient cwd.

## Boundaries (inviolable — even unattended, even under "no interruptions" pressure)

| Action | AFK behavior |
|---|---|
| push, MR-create, re-push | **Always automatic** on feature branches. Commit+push to a protected branch always asks — that gate is the park point (§0 guard, §6). |
| `git merge` / `glab mr merge` / `glab api …/merge` / `gh pr merge` | **NEVER.** Hard human gate at the park point. |
| `git push --force[-with-lease]` | **NEVER.** Forbidden in every mode. |
| a session-spawn follow-up | **Suppressed** → logged as 🔖 deferred (needs a manual session start). |
| a fresh user message mid-run | **Stop AFK** — disarm, go interactive (below). |

"The MR is green, I'll just merge to finish" is exactly the rationalization this table forbids.

## User interrupt = stop (overrides "never stop")

While AFK is active (`afk_state.py is-active` exits 0), any **genuine user message** (not the wakeup re-fire) = the user is back → **stop driving**:

1. `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"; python3 "$R/lib/afk_state.py" stop --repo "<worktree-abs>"` — tombstone for THIS repo+branch (already-armed wakeups exit at §0; other sessions untouched). Binding spelled out here rather than left to the convention above, same as `lib/wake-park.md`'s disarm: an unbound `R` exits rc 2 and writes no tombstone, while items 2-4 still announce "AFK off" — after which a queued wakeup re-fires and AFK resumes driving over the user.
2. No `ScheduleWakeup` this turn → the `/loop` winds down.
3. Append `[interrupt] user took over → AFK disarmed` to the decision-log (no plan → `afk_log.py append`, §5); keep the `RESUME:` line.
4. One-line ack ("AFK off — you're driving. `simo-dev:afk` to resume."), then answer interactively — §2 no longer applies.

## 0. Arm (or honor a prior stop)

**Flow check — before the stop-check, on every entry.** AFK drives an existing flow, never starts one:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_state.py" is-active --session "$CLAUDE_CODE_SESSION_ID" --cwd "<worktree-abs>"
```

rc 0 → continue. Any other rc (1 = not active; 2 = the script never ran) → print `afk needs an active simo-dev flow: run /simo-dev:task-intake first.` and **exit**, doing nothing else.

**Stop-check — first action on every entry, including a wakeup re-fire.** `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"; python3 "$R/lib/afk_state.py" is-stopped --repo "<worktree-abs>"` (checks ONLY this repo+branch). Binding spelled out at the site, not left to the convention above: this is the one call whose rc the body reads as a boolean, so an unbound `R` exits 2, falls through to Arm, and drives over a stop the user asked for. Exits 0 →
- user explicitly (re)started AFK this turn → `afk_state.py arm` and continue below;
- else (stale wakeup) → print "AFK is off for this task — `simo-dev:afk` to resume." and **exit**.

**The tombstone this reads can be written by a different copy of `afk_state.py`.** The `afk_state.py` calls in this body run the released cache while that directory exists — a rendered body gets the placeholder substituted — while `lib/wake-park.md`'s park-time disarm runs the source tree (or, on an install without one, the highest cached version), because the Read path substitutes nothing. (Once orphan cleanup removes the cache dir both sides resolve source and there is no split at all.) Both write `~/.claude/state/afk`, whose entries are `<repo-id>:<branch>:<state>:<epoch>` parsed with `rsplit(':', 3)`, with malformed entries purged on write. So between a merge and the next release **plus** a session restart the two can disagree, and the two legs of that format fail in opposite directions — the same split `hooks/review_record.py` documents for `review-loop`:

- **Structural**, by either of two mechanisms, both landing on `absent` — the "never armed" answer — so the bullet above does not fire and AFK keeps driving unattended. Toward *more* automation. A field added or dropped fails `_parse` outright; a `repo_identity` change parses cleanly and the lookup simply misses, which is the same distinction `hooks/review_record.py` draws for its own store.
- **The epoch's units or meaning**: it still parses, but `_live_entries` keeps an entry while `now - epoch <= TTL_SECONDS`, so re-basing seconds to milliseconds makes that difference negative and the `stopped` entry never expires. `is-stopped` then keeps exiting 0 and AFK refuses to resume. Toward *less*.

Treat the entry format, the epoch's units and `repo_identity` as frozen unless you land both sides and restart.

**Arm.** The publish path is unconditional for everyone (`commit` step 10 always enters `mr-followup`); AFK only arms its own liveness state. A protected-branch repo parks at its own commit+push gate (hard human gate). Emit one line, then:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/afk_state.py" arm --repo "<worktree-abs>"
```

## 1. Detect current state & resume

Check for a `RESUME:` marker in the decision-log first (fresh window lands here): the plan-sibling log when the task has a plan, otherwise:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/afk_log.py" show --repo "<worktree-abs>"
```

rc 3 = no log yet → infer below. rc 1 with a `repo_identity:` line = no current branch (e.g. mid-rebase) or `--repo` not a git repo → check `<worktree-abs>`, then resolve HEAD; rc 1 with a traceback = a `⚠` blocker; rc 2 = unbound `R` or bad `--repo`. None of these means "no log". Else infer:

| Observed | Resume at |
|---|---|
| intake analysis shown, no `y` | answer direction gate → continue |
| plan written, plan-review pending | answer plan-review → hand off |
| plan exists, code partially done | resume `simo-dev:plan-execute` |
| implementation done, not reviewed | `simo-dev:review-loop` |
| review clean, not committed | `simo-dev:commit` |
| pushed + MR exists | `simo-dev:mr-followup` |

Then run forward to the park point. A `simo-dev:mr-followup` resume, from the `RESUME:` line or the table, can stop on its entry check (`STOP: pending ledger work on …`): handled in §2, never as settled.

## 2. Gate-override rule (the core)

While AFK is active, auto-answer every gate and **append each answer to the decision-log**:

- **Direction gate** (task-intake step 4c) → `y` when the `Recommended:` letter is a direction. Otherwise (it recommends `deep-dive`) pick the `(preferred)` direction's letter, log `deep-dive skipped → took preferred <letter>`. Never `redirect`. Never enter brainstorming/grill (they block on the user).
- **Plan writer `BLOCKED`** (task-intake step 7) → answer it yourself with the option closest to the approved direction, log `[gate] plan-writer BLOCKED → <option>` (the decision-log is where the user revisits it).
- **Plan-review gate** (task-intake step 7.5) → `y`, except:
  - Never `proceed in a new session`: the gate never recommends it, and a new session would trigger the hand-off §3 suppresses.
  - Recommended `edit` or `restart` → `proceed`, log `[gate] plan-review → proceed (despite <n> finding(s) from plan critique)`, `<n>` = the `⚠ Plan critique` entry count; no findings (e.g. a recommended `restart` from a base overlap) → log the recommendation skipped instead.
  - A recommended `apply and re-present` is taken as-is, subject to the cap and logged only with the cap line below: `apply and re-present` at most 2 times per gate. Every apply-and-re-present pass, whether reached by `y` or by name, logs exactly `[gate] plan-review <plan-basename> → apply and re-present`; the cap counts those lines for the current plan basename in the decision-log (append-only across AFK runs, so the basename scopes it). Cap reached → `apply and proceed` if shown, else `proceed`, logged.
- **`simo-dev:review-loop`** → run its capped iterations; a clean run cascades into `simo-dev:commit` on its own (step 8); on unresolved findings pick option 1 (skip → `simo-dev:commit`), logged.
- **`simo-dev:commit` step 10** → enters `simo-dev:mr-followup` on its own (unconditional). Push automatic. A protected-branch commit+push gate IS hard — never auto-answer, park there (§6).
- **`simo-dev:mr-followup` entry check prints `STOP: pending ledger work on <file>`** → uncommitted Also-fixed ledger work. Never re-invoke mr-followup on it and never treat it as settled (§6 would park with that work uncommitted); `simo-dev:commit` step 10's re-entry is the way back. No `[ledger-stop]` entry in the decision-log yet → in one step append `[ledger-stop] <file> → review-loop` and point `RESUME:` at that leg (§5), then run `simo-dev:review-loop`, whose clean run cascades into `simo-dev:commit` without `--no-mr`. The work gets reviewed and committed whoever left it, so the recap lists that `[ledger-stop]` entry under decisions, where the user can revisit it by number. Park as a ⚠ blocker (§6), never re-running the leg, when the leg ends without a commit after §4's retry discipline, or the STOP comes back with a `[ledger-stop]` entry already logged (any file, including one from an earlier AFK run of the same task — the plan's decision-log keeps it). The leg's commit can succeed on other files and clear the ledger while leaving `<file>` out (e.g. an untracked file it did not stage), so mr-followup then settles without stopping again: §6's dirty-tree check catches it, and any other pending file the single-file STOP line never named.
- **Any other fork with no clear recommendation** → most conservative / most reversible option, logged.
- **An In-scope improvement needing a decision** (`docs/canonical-flow.md` "In-scope improvements") → skip it, log `[deferred (needs decision)] …` — never answered as a `[fork]`.

## 3. Hand-off override

- Intake's plan-review gate (task-intake step 7.5) → always an in-session option (§2), so step 8 always takes its In-session row. Never a session-spawn follow-up.
- Any session-spawn follow-up the flow would fire → suppressed, logged 🔖 `deferred spawn: <title> — accept manually after review`.
- Allowed: parallel-`Agent` (`subagent_type`), background `Agent`/`Bash` (`run_in_background: true`) — except a `simo-dev:code-reviewer` dispatch, always `run_in_background: false` (`simo-dev:code-review-protocol` item 2).

## 4. Blocker handling — retry, don't stop

Wrap external actions in retry discipline (log `⚠`): re-auth + retry on SSO failures, backoff on transient failures, `ScheduleWakeup` for long waits. **Keep one `ScheduleWakeup` always armed** (prompt `simo-dev:afk`, delay 1800-3600s), refreshed every turn — the token-limit dead-man's-switch.

> Retry/wakeup detail: `lib/wake-park.md` — load when arming the wakeup or handling a blocker.

## 5. Decision-log (single source of truth)

Append-only; survives compaction. Single `RESUME:` line at the top, updated each step. Entry format examples: `lib/wake-park.md`.

- **With a plan**: `docs/plans/<plan-basename>-afk-log.md`, written with Write/Edit inside the worktree and committed with the task.
- **No plan**: `~/.claude/state/afk-<branch>.md` (`/` in the branch → `-`), read and written **only** through `lib/afk_log.py` — never redirects, `cat`, Read, Write or Edit on that path: a command naming it gets a non-persistable permission prompt in sessions whose cwd is another repo. Keep that path out of entry and `RESUME:` text too (the check reads the heredoc body; the script prints the path, don't copy it in).

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/afk_log.py" append --repo "<worktree-abs>" <<'AFKLOG'
- [gate] direction → y (a, preferred)
AFKLOG
```

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/afk_log.py" resume --repo "<worktree-abs>" <<'AFKLOG'
<current step + enough context to continue cold>
AFKLOG
```

Read it with `show` (§1). rc 4 = empty input, nothing written. rc 1 with a `repo_identity:` line = no current branch (detached HEAD, e.g. a rebase stopped on a conflict): nothing written — hold the entry and retry once HEAD is back on the branch, never fall back to Write/Edit on the state path.

## 6. Park (terminal state — before merge)

When `simo-dev:mr-followup` settles: first list `git -C "<worktree-abs>" status --porcelain --untracked-files=all` and, for every path it shows (the new path of a rename) except the plan's own decision-log file (§5, written after the last commit), append `[⚠ blocker] <path> still uncommitted, not in the MR` to the decision-log through §5's route. No classification: whatever is dirty is not in the MR, whoever left it (on the main checkout it can be another session's). This check runs only here — not at the mid-flow ⚠ blocker parks (§2, Red flags), which park on their own named blocker, nor in the protected-branch variant. Then disarm (`afk_state.py stop`, stop re-arming wakeups), print the 4-bucket recap (decisions / ⚠ blockers / 🔖 deferred spawns / `deferred (needs decision)` In-scope improvements) + MR link + pipeline status (chat text before any turn-closing call, MR as a markdown link when one exists, after mr-followup's step 7.4 report when it ran), ask the user to reply `merge` (or a number to revisit a decision), then stop and wait. Protected-branch variant parks at the commit+push gate instead.

> Full park procedure + recap wording: `lib/wake-park.md` — load when parking.

## Red flags — STOP

- About to merge unattended → NO. Park, wait for `merge`.
- About to `git push --force` → NO, never.
- About to fire a session-spawn follow-up → suppress and log.
- About to enter `brainstorming`/`grill-me` → NO (they block on the user); take the recommended option, log.
- User message arrived and you're tempted to keep driving → NO: that IS the stop signal, disarm and go interactive.
- About to stop because a *command* failed → retry/reschedule first; only park at the terminal state. **Exception, and the rule carries no attempt cap so this is the whole brake:** `simo-dev:commit` step 5's pre-push guard classifies its own `STOP:` messages (`skills/commit/lib/failure-paths.md`), and two of them park instead — a guard that never ran or never answered will answer the same way forever. Follow that paragraph, not this line, on any `STOP: could not verify MR state`. Same for any non-zero exit of `simo-dev:commit` step 9's `lib/mr_create.py create` (rc 1 `STOP:`, rc 2 `R` unbound or a placeholder left: re-issue once, then park, rc 4 failed create call, rc 5 verification STOP): follow `skills/commit/lib/failure-paths.md` "Step 9: create block failed", not this line. A step 7 `prepare` STOP routes three ways: `STOP: local base ahead` → failure-paths "Step 7: local base ahead of origin"; `STOP: target: …` → `SKILL.md` step 7 **MR target** (afk: stop, record it, never guess); any other prepare STOP → failure-paths "Step 7: other prepare stops". `prepare` rc 3 `ASK: scope_fetch_cmd prefix=<P> key=<K>` → answer skip: run `python3 "$R/lib/tracker_config.py" set <P> scope_fetch_cmd -`, re-run `prepare`, record it in the afk summary. Same for `simo-dev:mr-followup`'s `STOP: pending ledger work on …` and a failed or incomplete `[ledger-stop]` leg: follow §2, not this line.

## Honors

`DEV_FLOW_NO_*` env vars (tracker, worktree). Ticket forward-transition to `In Progress` stays (idempotent, part of intake).
