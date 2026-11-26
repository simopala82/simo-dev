---
name: task-intake
description: Use only when the user explicitly starts the simo-dev flow — `/simo-dev:task-intake` or a clear request for it — from a ticket OR a free-text task prompt. Never invoke it proactively: it switches the flow on. Produces a detailed plan and (at hand-off) a worktree. On a checkout with work already in progress (changes, local commits, a plan, an open MR/PR) it proposes re-entering the flow at review-loop, plan-execute or mr-followup instead; `--adopt` skips that confirmation. First step of the canonical task flow.
---

# Dev Task Intake

Entry point of the canonical task flow:
`simo-dev:task-intake → simo-dev:plan-execute → simo-dev:review-loop → simo-dev:commit → simo-dev:mr-followup`

Arguments: a ticket key (e.g. `ACME-123`) or a free-text prompt. If missing, the repo's own `.claude/tracker-queue-cmd` (if present) is consulted first to propose a queue candidate before falling back to prompting the user — see step 1 below.

## Steps

### 1. Determine input mode

**Switch the flow on.** First action, before anything below. The simo-dev flow is opt-in: running this skill is what starts it. A user-typed `/simo-dev:task-intake` never reaches a Skill hook, so the skill writes the session flag itself, and the same call mints this task's ID (`lib/flow_telemetry.py`: the flow events of this task go to `~/.claude/state/dev-flow/<task-id>.jsonl`):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" start --session "$CLAUDE_CODE_SESSION_ID"
```

A prompt that carries a `task id: <id>` line (a Shared-tree stop hand-off) adds `--task-id "<id>"` to that `start` line, so the task keeps its ID.

A prompt that carries a `parent task: <id>` line (a follow-up started from another task, `docs/canonical-flow.md` "Parent task") adds `--parent-task "<id>"` to the same `start` line; both lines can appear together. Both apply only to the intake the spawned prompt asked for, the first one in the session, never to a later intake of another task in the same session. Both are metadata, and so is a `--adopt` token (`lib/resume-path.md`): drop them from `ARGUMENTS` right here, before the No-argument queue check, so its empty test and the ticket-mode regex below see only the key or the prompt.

**Decided brief.** A `direction: decided` block (`docs/canonical-flow.md` "Decided brief": that line, then `decisions:` with at least one `- <decision>` line, then an optional `risk:` line) in this intake's own `ARGUMENTS`, or in the spawned prompt for the first intake of the session only (the scope above), puts this intake on the **fast path**. Never inferred from prose, however decided it reads. A `direction: decided` line without a valid block (no `- <decision>` line, other bullet markers; a code fence around the block is fine) is no hit: say `Decided-brief block malformed → full intake` in one line, and still remove it. Remove the whole block, with its fence lines when fenced, from `ARGUMENTS` at the same point as the lines above and keep it aside as the brief's decisions; on a hit skip the No-argument queue check (the regex on what remains decides the mode; nothing left → prompt mode on the spawned prompt's text, minus its metadata: the spawned-task marker line, `task id:`/`parent task:`, the block itself and the invocation line). Right after `start`, record the path (`fast` on a hit, else `full`):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" event --type gate --field skill=simo-dev:task-intake --field gate=intake_path --field answer=<fast|full>
```

On a hit, Read `lib/fast-path.md` now and follow it: it names the steps below it replaces.

rc 2 from `start` (no session id, or the script missing) → one-line note, continue: step 6 writes the worktree marker anyway.

**Resume check.** Off the fast path, read `lib/resume-path.md` now and follow it, before the queue check: work already in progress in this checkout (uncommitted changes, commits ahead of the base, a plan, an open MR/PR) re-enters the flow at `review-loop`, `plan-execute` or `mr-followup` behind one confirmation, skipping analysis, direction and plan. A `--adopt` token (removed above) skips that confirmation when the arguments name no other work. Nothing to resume, or `full intake` picked → continue below.

**No-argument queue check.** Runs only when `ARGUMENTS` is empty; when `ARGUMENTS` is non-empty, skip straight to the regex check below.

Read `lib/queue-pick.md` and follow it: it runs `.claude/tracker-queue-cmd` when the repo has one and gates on its first candidate, then returns to the regex check below.

Argument matches `^[A-Z][A-Z0-9]+-[0-9]+$` → **ticket mode**; else → **prompt mode**.

### 2. Gather context

**ticket mode**: fetch by project prefix — look up `fetch_skill` for the ticket's prefix (`python3 "$R/lib/tracker_config.py" get <PREFIX> fetch_skill`); configured → invoke it. Not configured → ask the user which skill/CLI fetches tickets for that prefix, then offer to remember the answer (`python3 "$R/lib/tracker_config.py" set <PREFIX> fetch_skill <value>`). Capture title, description, AC, status, links, project key.

**Prompt mode**: use the prompt as task description. No fetch.

### 2.5. Umbrella split check

Work crossing **more than one project/repo or team** is not a single ticket: split it into one sub-task per boundary, leaving the original as an **umbrella** — a coordination ticket, never "the work to do" directly. Each sub-task carries its own branch, worktree, MR and status cycle.

Signals — any one of them fires the check: the ticket names more than one repo; it already has sub-tasks; the description carries a numbered "rollout steps" list; some steps belong to another team; some steps are blocked by external dependencies while others are not.

On a hit, **propose the split before opening the worktree** (step 6) and before planning (step 7): one line per sub-task, `[<repo>] <what>`, then ask. Never plan an umbrella as one unit.

Read `lib/umbrella-split.md` and follow it for the split itself: sub-task creation behind the `supports_subtasks`/`create_skill` gate, "blocked by" links, which sub-task to continue on, and the Epic escalation. The same file holds **Parent status sync** (the umbrella's status follows its sub-tasks: `--sync-parent`, exit 7, the user-confirmed close) and **Late promotion** (the same signals found after intake, or mid-intake at step 4's gate): read it whenever either one is cited.

### 3. High-level system-impact analysis

**Repo pre-flight (check first)** — one call (step 1's resume check already ran it: reuse its output, unless the cwd changed since):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/worktree_resolve.py" resolve --dir .
```

Keep its lines as literals for the rest of the run — `top:` = `<TOP>`, `headref:` = `<HEADREF>`, `gitdir:` = `<GITDIR>`, `real:` = `<REAL>` (the main checkout that owns the tree), `submodule:`, `real_ok:`, `row:` — and never re-read them bare later: the Bash cwd drifts. Verdicts:

- rc 1 `STOP:` with no `row:` → a different project (a plugin cache or marketplace copy, or a git-less rsync copy of one inside an unrelated repo) or no usable repo (unborn HEAD, not a repo). **STOP**; for the two plugin-copy STOPs the recovery is in `lib/repo-preflight.md`.
- rc 3 `NEED: shared-check` → a desktop-app tree: run step 6's **Shared-tree check** now and re-run this call with its flags, before Explore, `grill-me` or the plan touch the tree. `row: 3s` (a shared tree) → step 6's row 3s at once, `lib/shared-tree-stop.md`.
- any other row → continue; step 6 acts on it. This call passes no `--branch`, so row 4 and a failed `<REAL>` assert (`real_ok: no`) stop only there.

Also, if the task involves an existing path, verify it's tracked: `git ls-files <path>` (empty = not here → warn, wait for confirmation). Must clear before anything below writes artifacts to the tree.

> Recovery procedure (find the real clone, residual-case cleanup): `lib/repo-preflight.md` — load when either STOP or the tracked-path check fires.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
EXPLORE_MODEL=$(python3 "$R/lib/model_strategy.py" get explore_research)
[ -z "$EXPLORE_MODEL" ] && EXPLORE_MODEL="sonnet"
```

**Personal reference check** — before dispatching Explore, match external service/product names mentioned in the ticket/prompt against `~/.claude/reference/` filenames (`ls ~/.claude/reference/ 2>/dev/null | grep -i <name>` per name). This tree is outside any target repo, so neither Explore agent below covers it (Agent B is repo-scoped). Any match → read it now and fold relevant facts (auth, known gotchas, request/response shapes) straight into the step-3 summary — additive to the ≤200-word cap below, not counted against it — so the plan brief and `grill-me` inherit them instead of re-discovering them at runtime. Describe facts, never paste literal secrets (passwords, tokens, keys) that some reference files inline (e.g. `local-services.md`) — this summary can end up in a ticket comment or a committed plan file.

**Domain glossary check** — `CONTEXT.md` at the repo root (or the relevant file(s) under the context dir per `CONTEXT-MAP.md`) is written by `grill-me`'s domain capture and the commit knowledge-harvest, but until now neither this step nor `plan-write` ever read it back — Agent B's own scope is patterns/utilities/tests, not the glossary, and this check front-loads terms before Explore even returns. If present, read it now and use its terms when interpreting the ticket/prompt; fold any term relevant to this task into the summary, same cap treatment as above.

Dispatch **up to 2 `Explore` subagents in parallel** (single message; both pass `model: $EXPLORE_MODEL`; each prompt carries this line verbatim: "Write throwaway files only under a fresh `mktemp -d /tmp/claude_<slug>.XXXXXX` of your own; never write into or overwrite files in the Scratchpad directory your environment lists — it belongs to the caller's session."):

- Agent A: impacted repos / services / modules (file paths, boundaries).
- Agent B: existing relevant patterns (similar features, utilities, tests; reusable functions + paths).

Aggregate into a ≤200-word summary printed as normal reply text in chat (never only in tool-call narration or a question widget): 1-line gist, impacted systems, 2-4 bullet initial direction, open questions/risks.

### 4. Direction confirmation gate (always-on)

Fires unconditionally — the main defense against "implemented the wrong thing" — except on the fast path (`lib/fast-path.md`), where the brief already decided the direction.

**4a.** Append to the summary: 2-3 candidate directions as a lettered list, one line each, exactly one marked `(preferred)` (`a. (preferred) ...`). Surface any fuzzy signal explicitly ("⚠ AC empty / sync vs async unresolved / ...").

**4b. Fuzzy signals** (drive the `Recommended:` letter): AC empty/vague/contradictory; one-liner prompt with no clear target; multiple plausible interpretations; unresolved design choices; undocumented cross-team impact; unaddressed perf/migration/data-loss risk; open architecture-level decisions; **each non-trivial choice silently resolved inside the preferred direction** (a decision you made for the user is an ambiguity you hid, not one you removed — count it).

**4b-bis. Escalation path** (no line of its own — its verdict and reason fold into 4c's `Recommended:` line `<why>`):

| Signal | Path |
|---|---|
| 0-1 fuzzy signal, scope clear | `direct` — `Recommended:` picks the preferred direction |
| 2-4 discrete open decisions | `simo-dev:grill-me` (decisions mode) |
| open scope / architecture-level / ≥2 fuzzy signals / prompt-without-ticket | `simo-dev:grill-me` (design mode) |

Escalation is safe wherever grill-me writes artifacts — the step-3 pre-flight already confirmed the cwd is a legitimate repo. If it forwarded a misplaced worktree to step 6, the artifacts still survive (the move carries untracked files) but any absolute path already quoted to the user goes stale — re-state it after the move.

**Split signal found mid-gate.** If the investigation behind this gate — including a redirect loop chasing a fuzzy signal — surfaces one of step 2.5's split signals (e.g. an external dependency blocks only part of the work), apply **late promotion** (step 2.5's "Late promotion" section, mid-intake case) right here, before printing the `Recommended:` letter: propose the split, create sub-task(s), continue on the right key. Don't wait for the user to ask — worktree and plan don't exist yet at this point, so redirecting now is free.

**4c. Summarize, then ask (always).** Restate the preferred direction in 2-4 plain bullets (what, where, key choices) — the user must be able to approve from this block alone. Then a **`Choices made:` list** — every non-trivial decision embedded in the preferred direction (sync vs async, new field vs reuse, library picks, ...), one line each, ending with "— `deep-dive` to challenge these". Omit the list only when there is genuinely nothing decided beyond the literal request. **Do NOT use AskUserQuestion for this gate** (observed twice: the summary gets crammed into truncating widget fields). END THE TURN with plain markdown: bullets, then one lettered list — the candidate directions (4a), then `deep-dive`, then `redirect <reason>` — then one `Recommended: <letter> — <why>` line and the prompt:

> "Direction look right? `[y = <letter> / a–<last>]`"

`Recommended:` selection: the `deep-dive` letter when ≥2 fuzzy signals OR prompt mode without a ticket OR an open architecture decision, or when 4b-bis gives a `grill-me` row; else the preferred direction. Exception: once a deep-dive has already run in this intake, or 4b-bis now gives `direct`, recommend the preferred direction — otherwise "prompt mode without a ticket" would send `y` back into grill-me forever. `y` takes the `Recommended:` letter.

- The preferred direction's letter → step 4.5.
- Another direction's letter → re-present this gate with that direction restated (bullets + its own `Choices made:`) as the new `(preferred)`.
- `redirect <reason>` (answered as `<letter> <reason>` or `redirect <reason>`; `y` on a recommended `redirect` asks for the reason first) → incorporate, re-run Explore if needed, re-present. Loop until the preferred direction's letter.
- `deep-dive` → escalate per 4b-bis, announce in one line, invoke, re-present this gate.

Gate exits only on the preferred direction's letter. Record the answer as a flow event (`<answer>` = the letter picked):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" event --type gate --field skill=simo-dev:task-intake --field gate=direction --field answer=<answer>
```

### 4.5. Requirements write-back (always-on)

Runs once step 4's gate exits on the preferred direction's letter (direction approved), before step 5.

**Draft.** From the approved direction (the candidate directions plus the `Choices made:` list already produced at step 4c), write a **Requirements / Acceptance Criteria** block: 3-6 bullets, each a condition checkable true/false against the finished work — not a restatement of the direction prose. Keep it as its own block; it is what gets written to the ticket, not shown to the user again here. Write it to a temp file, same as step 5.5's own comment body does.

**Ticket mode** (key already known — from step 1's mode determination, or from a freshly-created sub-task key on the late-promotion path): invoke the hook now.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_requirements_review.py" "<KEY>" "<tmpfile>"
```

**`--replace` rule.** Use this block INSTEAD of the one above (never after it) only when step 7.5's `restart` sent THIS task-intake invocation (counted from its own step 1) back to step 4 or 3 after it had already announced `Requirements: posted: <KEY>` for the same key. A `posted:` line from an earlier task-intake invocation in the same session never counts — a follow-up intake must not overwrite the original ACs. In doubt (e.g. the announce line is no longer in context), no flag.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_requirements_review.py" "<KEY>" "<tmpfile>" --replace
```

Announce: `Requirements: <script's stdout line>`. `skipped: prior requirements block present on <KEY>` means a previous intake's ACs are kept: announce it as-is, never retry with `--replace` — this run's direction and ACs still reach the ticket via step 5.5's comment.

**Prompt mode** (no key yet — step 5 may create one): hold the drafted block for this session; the call happens at step 5.5 instead, once the key (if any) is resolved — see step 5.5.

No separate gate: like `comment_cmd`, the hook is a silent no-op when `requirements_review_cmd` is unconfigured for the prefix — configuring the field is what opts a prefix in, not a question asked here.

### 5. Ticket decision (prompt mode only)

Skip in ticket mode (an explicit key always wins; the marker does not apply to it).

`.claude/no-tracker` marker or `DEV_FLOW_NO_TRACKER=1` → skip the question silently, continue with no ticket (branch/plan naming without scope segment). Check both mechanically, using step 3's already-established `<TOP>` as `<repo-root>` — never rely on remembering the marker exists:

```bash
[ -f "<repo-root>/.claude/no-tracker" ] && echo "no-tracker marker present — skip the question silently"
[ "$DEV_FLOW_NO_TRACKER" = "1" ] && echo "DEV_FLOW_NO_TRACKER=1 — skip the question silently"
```

Either line printing → skip straight past the question below, no ticket.

Otherwise ask — never default to creating a ticket:

> "Create a ticket for this work? (y/N) Project/prefix?"

Default is **always `N`**, even if the task brief implies a ticket. On explicit `y` → look up `create_skill` for the given prefix (`python3 "$R/lib/tracker_config.py" get <PREFIX> create_skill`); configured → use it. Not configured → ask which skill/CLI creates tickets there, then offer to remember the answer (`python3 "$R/lib/tracker_config.py" set <PREFIX> create_skill <value>`). Title = step 3's 1-line gist, updated to the direction approved at step 4; description = analysis summary; capture the new key and that title (step 5.6 reuses it); continue in ticket mode. On `N` → continue without.

### 5.5. Direction comment

Runs always, both modes, right after step 5 resolves (so the ticket key — if any, including one just created in step 5 — is known). No key, or `comment_cmd` unconfigured for its prefix: `post_tracker_comment.py` (`lib/post_tracker_comment.py`) handles both as a silent no-op on its own — nothing here needs to special-case them.

Body = the direction approved at step 4's gate: the candidate directions with the chosen one marked, the final `Choices made:` list, and any `⚠` fuzzy-signal lines that were surfaced. If step 4.5 announced `skipped: prior requirements block present`, also append the Requirements / AC block drafted there, under its own heading. Write it to a temp file, then:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_tracker_comment.py" "<KEY-or-empty>" "<tmpfile>"
```

Announce the result in one line: `Direction: <script's stdout line>` (e.g. `Direction: posted: PROJ-1234` / `Direction: skipped: no ticket key`).

If step 4.5 ran without a key yet (its Prompt mode branch, not its Ticket mode branch), invoke the deferred requirements write-back now, using the block drafted at step 4.5 and the key just resolved by step 5:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_requirements_review.py" "<KEY-or-empty>" "<tmpfile>"
```

Only under step 4.5's `--replace` rule, use this block instead:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_requirements_review.py" "<KEY-or-empty>" "<tmpfile>" --replace
```

Announce: `Requirements: <script's stdout line>` (a `skipped: prior requirements block present` result is handled as in step 4.5).

### 5.6. Take ticket in charge

Runs always, both modes, right after step 5.5's comment resolves — by this point the direction has already been validated at step 4's gate and the ticket key, if any (including one just created at step 5), is known. No key: skip this block entirely — nothing to assign or transition.

Assign first, then transition, both best-effort:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/tracker_config.py" run "<ticket-prefix>" assign_cmd "<ticket-key>" || echo "WARNING: assign_cmd failed for <ticket-key> (continuing)"
python3 "$R/lib/tracker_config.py" run "<ticket-prefix>" transition_cmd "<ticket-key>" "In Progress" --sync-parent; rc=$?
if [ "$rc" -eq 8 ]; then echo "BLOCKED: transition_cmd reports a Blocked ticket for <ticket-key> or its parent, see the script's BLOCKED line for which key (continuing)"
elif [ "$rc" -ne 0 ]; then echo "WARNING: transition_cmd failed for <ticket-key> -> In Progress (continuing)"; fi
```

No-op if `assign_cmd`/`transition_cmd` is not configured for the prefix — this covers a read-only tracker (fetch works, writes fail) with no special-case in this skill body, and also if the ticket is already assigned/at-or-past target — except a non-terminal ticket flagged Blocked, which still gets rc 8 when already at/past target. This call deliberately omits `--clear-blocked`: a ticket (or its umbrella) flagged Blocked on the tracker side is left untouched and the script exits 8. Never re-run it with `--clear-blocked` on your own — only if the user asks. `DEV_FLOW_NO_TRACKER=1` / `.claude/no-tracker` marker skip both auto-assignment and auto-transition, AND the step-5 ticket question — not the session rename below. `--sync-parent`: when `<ticket-key>` is a sub-task (step 2.5 already routed here instead of the umbrella), this also syncs the umbrella's status — see step 2.5.

Announce the result in one line — the `|| echo "WARNING: ..."` on the assign line and the transition line's own `BLOCKED`/`WARNING` echoes only reach the Bash tool result, never the user, unless repeated here: `Ticket: assign <no WARNING|WARNING: ...>, transition <no WARNING|WARNING: ...|BLOCKED: <the script's own BLOCKED stderr line>>`. Don't claim "ok"/"skipped" — `tracker_config.py run`'s own exit code doesn't distinguish "not configured" from "ran successfully", so the only thing safe to report is whether the WARNING text fired. BLOCKED is safe to report: rc 8 is only produced by a configured script. A silent WARNING here is exactly the failure mode step 8's hand-off has no other way to catch.

On BLOCKED, tell the user in plain words which key is Blocked, reading it from the script's own BLOCKED line: `<ticket-key>` itself → it stayed in its current state and is still Blocked; its parent → `<ticket-key>` itself is at/past In Progress (or terminal) and only the umbrella was left untouched. Then say how to unblock, if wanted: by hand in the tracker, or by re-running the transition command above with `--clear-blocked` appended (with `--sync-parent`, that also lifts a Blocked flag on the umbrella). Non-blocking report: intake continues to step 6/7/8 without waiting for an answer. Left alone, the flag stays until a post-push CodeReview transition: `simo-dev:commit` and `simo-dev:mr-followup` pass `--sync-parent --clear-blocked` there (step 2.5, "Parent status sync"), so a Blocked `<ticket-key>` is lifted at its own CodeReview, and a Blocked umbrella at the first CodeReview of any of its sub-tasks, even when the umbrella itself does not move — say which applies in the report. Under `simo-dev:afk`, leave the ticket Blocked and note it in the afk decision-log, including that the CodeReview transition will lift it; never re-run with `--clear-blocked` — an application of afk's own "most conservative / most reversible option, logged" rule (its §2).

**Rename session.** With a ticket key, rename this desktop session so its title tracks the ticket. Runs even under `.claude/no-tracker` / `DEV_FLOW_NO_TRACKER=1` (a local rename, no tracker write). Skipped under `simo-dev:afk` — a user-set title makes the app wait for approval, stalling the run; log the skip in the afk decision-log. Tool: `mcp__ccd_session_mgmt__set_session_title`; not yet callable → `ToolSearch` `select:mcp__ccd_session_mgmt__set_session_title` once, never skipped because the name is unlisted; no schema, or no `ToolSearch` → skip silently (not the desktop app). Call it with `session_id: "self"` and `title: "<KEY> <summary>"`, cut to under 60 chars at a word boundary (e.g. `PROJ-123 Add retry to export job`). `<summary>` = the title of the key this step uses: step 2's fetch in ticket mode; the title passed to `create_skill` for a ticket created in this session (step 5, step 2.5, mid-intake late promotion — already in context); fetch it with the same `fetch_skill` only for a pre-existing sub-task whose title is not yet known; still unknown (fetch failed or unconfigured) → title is `<KEY>` alone. Skip the call when this intake already set that exact title on this session. Best-effort: an error or a decline (including "a subagent can't rename the session it runs in") → one-line note, continue; never retry, never block intake. A spawned child's re-run may ask approval again if the app treats the earlier title as user-set, and under bypass permissions the app overwrites a user-set title without asking — both accepted.

### 6. Branch and worktree

Invoked from step 8, once step 7.5's gate has decided the hand-off routing. In-session: the orchestrator runs it here, live. `start_session`: the spawned child runs it, from its own actual location — never using values the orchestrator computed.

Branch name: `<type>/<jira-key-lowercase>-<short-desc>` with a ticket key (e.g. `feat/proj-1234-topn-by-maxrank`), `<type>/<short-desc>` without. `<type>` ∈ `feat|fix|chore|refactor|docs|test|perf`; `<short-desc>` = 3-6 hyphenated lowercase tokens. `<name>` = the branch minus `<type>/`.

Always a worktree, no opt-out (the one exception is step 1's resume check, `lib/resume-path.md`). Never a second one when this session already sits in one: that gave two branches and two MRs for one change (`!35`/`!36`, `!20`/`!21`). ⚠ Never the native `EnterWorktree` tool (`lib/worktree-carry.md`). Every path below is a **literal absolute path** taken from the script's output, used as `git -C "<path>"`, never a shell variable or `$(pwd)` (CLAUDE.md "Worktree cwd gotcha"). The row table, its definitions and their evidence live in `lib/worktree_resolve.py`'s docstring: read it only when an outcome looks wrong.

**Resolve.** `<TOP>` = step 3's `top:` line; the cwd changed since (redirect to another repo, manual switch) → re-run step 3's call there first. Artifacts already written in a wrong repo → `lib/repo-preflight.md` (cp, never a cross-repo stash). On a desktop-app tree (step 3 needed the shared-tree check), run the **Shared-tree check** below again and pass its flags: step 3's answer can be many turns old.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/worktree_resolve.py" resolve --dir "<TOP>" --branch "<branch>" --base "<base>" <shared-check flags>
```

Run every `run:` line as printed, in order; a non-zero is a stop. `wt:` is `<WT>`, the worktree this step ends in; `next:` names what follows (`adopt`, `close` = the **Close** block, `follow-up`). rc 1 `STOP: <reason>` → nothing ran: report the reason and ask (a missing-directory STOP on a re-entry after a row-3b move: `lib/worktree-rare-paths.md` "Row 3b: the move fails, or ran in an earlier run"). By `row:` (first match wins):

| # | Tree | Action |
|---|---|---|
| 1 | main checkout or submodule working tree | `run:` fetch, `worktree add --no-track <repo>/.worktrees/<name> -b <branch> origin/<base>` (a local `.worktrees/` exclude when not ignored) |
| 2 | canonical `<REAL>/.worktrees/<x>` | same, `<repo>` = `<REAL>` |
| 3s | desktop-app tree another live session works in (a **shared tree**) | **STOP**: read `lib/shared-tree-stop.md` and follow it. Nothing is written, renamed or parked |
| 3a | this session's own desktop-app tree | **adopt in place**, `<WT>` = `<TOP>`; never moved, and never a sibling canonical worktree as a workaround (the app's isolation hook refuses Edits on both) |
| 3b | harness `.claude/worktrees/` tree, no desktop marker | `run:` move to `<REAL>/.worktrees/<name>` (non-zero: `lib/worktree-rare-paths.md` "Row 3b"), then **adopt** |
| 4 | any other linked worktree (release, bisect, hand-made) | **STOP and ask**; never move or rename it |

`next: follow-up` (rows 1/2: `origin/<branch>` exists, step 7.5's `follow-up` bypass) → no `run:` lines; read `lib/worktree-rare-paths.md` "Follow-up on an EXISTING branch/MR" and follow it instead.

**Adopt** (rows 3a and 3b, after the move): renames the harness branch to `<branch>` and freshens it, keeping the tree's pending work.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/worktree_resolve.py" adopt --wt "<WT>" --branch "<branch>" --base "<base>"
```

Keep its `headref:` as `<HEADREF>`. rc 4 `ASK:` → nothing renamed; ask, never pick silently, then re-run with the flag:

- `foreign-commits`: the `foreign:` commits (an earlier task's, unpushed) would ride into this task's MR. Report them; on the user's yes, `--foreign-ok`.
- `old-remote`: the `claude/*` branch is pushed and may carry an MR, so a rename opens a second MR (an MR's source branch cannot be retargeted). Either close that MR and re-run with `--old-remote-ok`, or keep the old name: `<branch>` = `<HEADREF>`, no rename, and the park's rebase then needs a force-push (a CLAUDE.md gate).

`next: merged-branch-path` (a tree left on another task's named branch) → read `lib/worktree-rare-paths.md` "Merged-branch path" and follow it; its `foreign:` lines are expected there. Otherwise run the one `run:` rename (after `switch -c` on a detached HEAD, a `claude/*` branch that exists but is not checked out survives: delete it by hand once adopted), then park the pending work and freshen the base — not a stash, `refs/stash` is shared by every worktree:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/park_and_rebase.py" park --repo "<WT>" --base <base>
# rc 3 → resolve, then continue WITH the hook suppression, NEVER `--skip`:
#   git -C "<WT>" -c core.hooksPath=/dev/null rebase --continue
# then restore — on rc 0, after a resolved rc 3, and on the rc 5 branches whose message
#   asks for it; never on rc 2 or rc 4. Re-bind R: the `--continue` above is a separate Bash
#   call, and an unbound R makes this exit rc 2, which reads as "refused, repo unchanged"
#   while the park commit is in fact still at HEAD:
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/park_and_rebase.py" restore --repo "<WT>"
```

rc `0` → run `restore`; rc `3` → resolve, `--continue`, then `restore`; rc `4` → nothing to restore, skip it; rc `2` → refused, repo unchanged, **stop**; rc `5` → the repo IS changed, read the message — two of its branches leave a park at `HEAD` and ask for `restore` once you have fixed the cause. Full table: `simo-dev:commit` `lib/failure-paths.md` (step 0.5 rc 1). Read `restore`'s stderr even at rc `0` — a path it could not re-stage is named only there — and the park does not preserve the index/worktree split.

**Shared-tree check** (a desktop-app tree; here and at step 3): `mcp__ccd_session_mgmt__get_session` with `session_id` `"self"`, and `mcp__ccd_session_mgmt__list_sessions` with `linked: true` and `limit: 200` (the default 20 can drop a row); not yet callable → `ToolSearch` `select:` once. Flags: `--self-worktree "<worktreePath>"` (`""` when absent) plus one `--linked-cwd "<cwd>"` per listed session, idle ones included (the tool already leaves out this session and archived ones); either tool unavailable → `--no-session-tools` (counts as shared: ownership cannot be shown). The user answered "adopt anyway" for this tree in this run → `--adopt-anyway`. The script compares directories with `-ef`. Evidence and blind spots: `lib/shared-tree-stop.md` "The check" — read it when a session in this tree was missed.

**Close** — on every row that did not stop: assert `<WT>`, mark it as in the flow (both keys: a `start_session` child runs this step but never step 1, so this is where it gets its session flag — it appends `--task-id "<task id from the prompt>"` to the `on` line, so the task keeps one ID; an orchestrator leaves it off), then bind the task to its branch. `simo-dev:mr-followup`'s post-merge cleanup clears the marker.

```bash
git -C "<WT>" rev-parse --show-toplevel   # MUST equal <WT>
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_state.py" on --session "$CLAUDE_CODE_SESSION_ID" --repo "<WT>"
python3 "$R/lib/flow_telemetry.py" event --type task_bound --cwd "<WT>" --field ticket="<KEY-or-empty>" --field branch="<branch>" --field repo="$(basename "$(dirname "$(git -C "<WT>" rev-parse --path-format=absolute --git-common-dir)")")"
```

On this run's own row 1 or row 2, the calling step-8 branch already built the carry commit: cherry-pick its `<carry-sha>` into `<WT>` now (`lib/worktree-carry.md`'s `cherry-pick -n`/`reset --quiet` steps, at that file's phase-marker comment). On row 3 the artifacts already live in the adopted tree — except in a `start_session` child given a `<carry-sha>` (its orchestrator was on row 1/2; the child itself can land on any row, row 1 included): it cherry-picks into its own `<WT>` whatever row it continues on (`lib/handoff-spawn.md`). "Nothing to cherry-pick" means no `<carry-sha>` was supplied.

Report one line: `<WT>` + `<branch>`, and whether the tree was moved (3b) or adopted where it stood (3a). A main checkout listing an adopted 3a tree as untracked: `lib/worktree-rare-paths.md` "Untracked `.claude/worktrees/` in the main checkout".

### 7. Detailed plan

If step 4c's `Choices made:` list came back empty (the same condition step 4c itself already uses to decide whether to print that list at all — no new judgment introduced here), skip the writer subagent and write the plan file directly instead, at the same path convention documented below (`docs/plans/YYYY-MM-DD-<jira-key-lowercase>-<short-desc>.md`; no ticket → drop the key segment).

The directly-written file must contain:
- A Goal line, taken from step 3's 1-line gist.
- A single `### Task 1: <the literal request, restated>` section listing the concrete edits already named in the ticket/prompt and confirmed present by step 3's Explore output (values, target files).
- A Files-touched block using the plan's own standard `- Modify: \`path\`` / `- Create: \`path\`` bullet syntax — never a plain prose list. This exact form matters: `check_base_freshness.py`'s `files_from_plan()` parses exactly that bullet form to know which paths a plan touches, so step 7.5's "base-freshness check runs unchanged" claim only holds if this bare-plan file is parseable the same way a `plan-write`-produced one is. Each path is the literal repo-relative path, never a placeholder (`<P>/...`, `$P/...`) — this file bypasses `plan-write`, whose "No placeholders" rule would otherwise forbid it, and the check reports such a path as an error.

This is the minimum shape `simo-dev:plan-execute` already knows how to consume — one task, a disjoint-by-construction file set — not a new plan format.

Otherwise (the list is non-empty — the default, and the only path before this fork):

A **writer subagent** writes the plan, so its code reading and the plan text never enter this session, which every later step re-reads. This session keeps the brief, the dispatches, the critique and the gate (step 7.5). Plan path: `docs/plans/YYYY-MM-DD-<jira-key-lowercase>-<short-desc>.md` (no ticket → drop the key segment), on the current checkout — no worktree exists yet; step 6 runs later, from step 8.

**Brief.** Create its folder once per run, `mktemp -d "${TMPDIR:-/tmp}/claude_plan-brief.XXXXXX"` (`<brief-dir>`, its printed absolute path), and Write `<brief-dir>/brief.md`. The writer knows only this file, so anything left out is lost:
- ticket key and summary (or "no ticket"), task type, the repo's absolute path, the plan path, `<base>` when known;
- the approved direction: step 4c's bullets and `Choices made:` copied as shown, plus every `redirect` reason folded in since;
- requirements: the ticket's acceptance criteria, or those step 4.5 wrote back;
- step 3's summary, the personal-reference facts (described, never literal secrets) and the `CONTEXT.md` terms;
- the Explore findings as paths with an identifying string each (files to change, patterns, utilities and tests to reuse), never pasted code: the writer reads the code itself;
- the grill-me outcome, when one ran: decisions mode → the resolved decisions, numbered, each with the rejected alternative and why in one line; design mode → the approved spec's path (and the mockup's), named as authoritative;
- the user's constraints and preferences from this conversation, quoted in their own words, not paraphrased; what was declared out of scope; In-scope improvements already known (`docs/canonical-flow.md` "In-scope improvements").

**Dispatch.** One `Agent` call: `subagent_type: simo-dev:plan-writer` (not listed → the default type, same prompt), `run_in_background: false`, no `model` (it inherits this session's). The prompt carries: an ownership line (the writer owns the plan file and its report); the brief's and the plan's absolute paths and the repo's; "load `simo-dev:plan-write` with the Skill tool and follow it; its Report section is your final message"; never commit, never ask the user, never dispatch an `Agent`, invoke no other flow skill; and the line "Write throwaway files only under a fresh `mktemp -d /tmp/claude_<slug>.XXXXXX` of your own; never write into or overwrite files in the Scratchpad directory your environment lists — it belongs to the caller's session." verbatim. Keep its agent id: steps 7.5 and `lib/gate-critique.md` resume it.

**Resume** (here, in step 7.5 and in `lib/gate-critique.md`): `SendMessage` to the writer's agent id, carrying the items to apply (when not callable, `ToolSearch` `select:SendMessage` once first). `SendMessage` returns at once with a `resumed … in the background` acknowledgement, not the report: wait for the task notification, and read that report as below. `SendMessage` unavailable → a fresh dispatch with the same prompt plus "the plan already exists at <plan path>: read it, apply only the items below, do not rewrite it" and the items; its agent id replaces the old one.

**Report.** Read the `status:` line:
- `DONE` → step 7.5 (or back to the step that resumed it), with the report's `assumptions:` and `rejected:` lines.
- `BLOCKED` → ask the user the decision it names (`simo-dev:afk` answers it itself); add the answer to the brief and **Resume** with it, then this item again.
- No `status:` line (a cut or failed turn) → **Resume** with "end with your Report" as the only item, once; still none → a fresh dispatch as in **Resume**.

Never Read the plan file in this session while a writer owns it, from step 7's dispatch to the gate's approving exit: the summary comes from `lib/plan_summary.py` (step 7.5), changes go through the writer. After that exit the plan is this session's again (`simo-dev:plan-execute`, `simo-dev:review-loop`'s plan drift).

### 7.5. Plan review gate (always-on)

Second structural check-point — last chance before implementation. This step never reads the plan file: the summary comes from `lib/plan_summary.py`, run after the critique below (and again after any edit), so it shows the post-fix plan. First run the base-freshness check against the plan's declared files — once, synchronously, right here; not repeated later in this same gate loop (an `edit`/`apply and re-present`/`restart` round-trip re-runs it naturally next time this step executes from the top):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/check_base_freshness.py" --repo "<repo>" --plan-files
```
It answers a file-overlap question (local `<base>` vs `origin/<base>`, on the plan's files), not citation staleness: what it cannot see here is checked later, in `plan-execute` step 1 and per task/commit (`lib/plan-gate-rare.md` "Freshness check scope").

- rc 1 → fold the printed overlap (the overlapping file paths — `analyze()` returns no commit data) into the Plan summary below, under a `⚠ Base advanced` heading, right after Risks.
- rc 0 or rc 2 → no change to the summary, except `⚠ Plan files` below.
- Any rc: stderr lines naming a `docs/plans/...` file with a placeholder **Files:** path → fold them into the Plan summary under a `⚠ Plan files` heading, after `⚠ Base advanced` when present, else after Risks (or Files touched when none). Only a line naming the plan under review asks for a fix here; one naming another session's plan is listed for information.

A fast-path plan follows `lib/fast-path.md`'s critique rule (at most one round, only with a `risk:` line) instead of the rounds below. A plan from step 7's writer is critiqued here, on its first entry and on every re-entry after `edit` or `apply and re-present`; its fixes go back to the writer (`lib/gate-critique.md`). If this plan file came from step 7's bare-plan fork (step 4c's `Choices made:` list came back empty), skip this model lookup and the dispatch below entirely — treat it exactly like the `PLAN_CRITIQUE_MODEL` empty case: no model lookup, no `plan-reviewer` `Agent` dispatch, no autofix rounds. Nothing was generated here to critique, only a restatement of material already confirmed earlier in the flow; a design decision it turns out to have missed → `lib/plan-gate-rare.md` "Bare plan: escape hatch". Otherwise (a writer's plan, or a fast-path plan whose `risk:` line asks for its round) read `lib/gate-critique.md` and follow it: the `plan_critique` model lookup (`none` disables critique), the `plan-reviewer` dispatch, the objective autofix rounds under the `Critique rounds` cap, and the **gate findings set** (every unapplied `→ decide:` finding, deduped by location and message) that the summary, the apply options and the `Recommended:` line below use.

**Derive the branch name now, before the pre-gate check below needs it.** `<type>/<jira-key-lowercase>-<short-desc>` (or `<type>/<short-desc>` without a ticket) — step 6's own naming rule (see its heading), a pure string computation from context already gathered by steps 1–5 (task type, ticket key, a short slug), needing no git operation and no worktree. Step 6 (whichever row it resolves to) and everything below in this step consume this exact string rather than re-deriving it.

**Pre-gate eligibility check — run before this gate's spawn option is decided.** `<row>` and `<submodule>` = step 3's `row:` and `submodule:` literals:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/plan_gate_precheck.py" --repo "<repo>" --branch "<branch>" --row "<row>" --submodule "<submodule>"
python3 "$R/lib/gate_reminder.py" arm --label "<label> plan gate"
```

`bypass: none` → record no bypass: row 1 (not a submodule) and row 2 are the only rows this gate's `start_session` option is offered for. Anything else (a branch that already exists on origin, rows 3s/3a/3b/4, a submodule; why each one: `lib/plan-gate-rare.md` "Bypass conditions") → record the bypass verdict as printed, to be announced once, by the gate below, not here. Either way the lettered gate below still runs — a bypass verdict only ever changes what an approving option (`proceed` / `apply and proceed`) routes to afterward: a bypass plan goes straight to step 8's In-session row once approved, and the gate hides its `proceed in a new session` option.

**Gate reminder.** The `gate_reminder.py` line rides in the same Bash call as the check above — never a call of its own, which would cost one more request on the full context. Skipped under `simo-dev:afk`, which answers this gate itself: drop that line.

`<label>` is the ticket key, else the branch's `<short-desc>` slug: never free text, which the shell would expand.

A printed `Gate reminder set: answer by ~HH:MM …` line means a detached watcher sends one desktop notification about 15 minutes before this session's prompt cache expires, unless the user writes first: an answer that lands after the expiry rewrites the whole context. Show that line, in the session language, right above the `Recommended:` line. Nothing printed → no line.

Then show:

- Clickable absolute path; 1-line gist + ticket key + branch.
- **Plan summary** from `lib/plan_summary.py`'s output, run on the plan as it stands after the critique (never from memory, never by reading the plan):
  ```bash
  R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
  python3 "$R/lib/plan_summary.py" "<plan-abs-path>"
  ```
  Shown in this order: Goal, `Bare plan (no open design decisions — plan-writer/plan-reviewer skipped)` (only when this plan file came from step 7's bare-plan fork: the shortcut must always be visible in the transcript), Approach (absent for a bare plan — it has no Architecture line), Tasks, Files touched, Risks (when printed), `⚠ Base advanced` (only on rc 1 above — the overlapping file paths), `⚠ Plan files` (only when the check printed placeholder lines — one per flagged plan), `⚠ Writer assumptions` (the latest writer report's `assumptions:` lines, when not `none` — choices the brief did not settle), `⚠ Writer rejected` (every `rejected:` line from this gate pass's writer reports — a finding the writer judged wrong and did not apply), `✓ Plan critique auto-fixed` (only when ≥1 objective finding was applied above — one line per round, `<n> objective finding(s) fixed`), `⚠ Plan critique` (only when the gate findings set above is non-empty — list each as `[severity] location — message → fix:/decide: …` with its tail, so the user sees what an apply option would apply), and `Critique rounds: <used>/<cap>` (only for a plan whose header carries `Critique rounds`; omit it for a bare plan or a fast-path plan). Cap ~25 lines for the summary as a whole; if the `⚠ Plan critique` list alone would blow that → `lib/plan-gate-rare.md` "Summary overflow".

Path + gist + summary MUST be visible markdown in the chat reply BEFORE the gate prompt. Do NOT use AskUserQuestion for this gate either, same as 4c: the widget holds at most 4 options and has no `y = <letter>` mapping. Multiple plan files → one summary block each, single gate.

**Option visibility.** No gate findings, or no finding with a recommended option → omit both apply options (`apply and proceed`, `apply and re-present`). Any decide finding with no recommendation → omit `apply and proceed` (`apply and re-present` stays). A bypass verdict, or `mcp__ccd_session__start_session` absent from this session's tools (neither callable nor listed as deferred; no `ToolSearch` needed to tell) → omit `proceed in a new session`.

**Recommended priority table** — exactly one line, `Recommended: <letter> — <why>`, printed immediately above the gate prompt; first match wins:

| # | Condition | Recommended |
|---|---|---|
| 1 | base advanced (rc 1 above) | `restart` |
| 2 | no gate findings | `proceed` |
| 3 | every finding has a recommended option, none `high`/`critical` | `apply and proceed` |
| 4 | every finding has one, some `high`/`critical` | `apply and re-present` |
| 5 | some findings have one | `apply and re-present` |
| 6 | none has one | `edit` |

`proceed in a new session` is never recommended (execution and review already run in subagents; a hand-off child adds its turn-end notes here). The `<why>` names the reason (base overlap, `<n>` finding(s), the severity).

The "Tasks" and "Files touched" counts printed by `plan_summary.py` give the plan's size — reuse them, do not recompute. A plan is "large" when its task count is greater than 5, or its file count is greater than 8; when it is, fold `plan has <n> tasks across <m> files` into the `<why>` — never a separate line.

If the bypass verdict is present, print the one-line announcement of which condition fired — the **only** place it is printed. Then show:

> "Plan written to `<path>`. Review it:
> - **a. `proceed`** — approve, continue in this session (step 8).
> - **b. `apply and proceed`** — apply every finding's `fix:` or recommended option, then continue in this session.
> - **c. `apply and re-present`** — apply the same edits, re-run critique (only while rounds remain under `Critique rounds`; after that, no new critique), show this gate again.
> - **d. `edit <feedback>`** — change the plan per your feedback, re-present this gate.
> - **e. `restart`** — back to step 4; editing won't fix it.
> - **f. `proceed in a new session`** — approve as is, hand off to a fresh session (start_session, rooted at the repo root — the child stays linked, and this session is archivable only as `lib/handoff-spawn.md` **Keep the child linked** says).
>
> `[y = <letter> / a–<last>]`"

Letters are assigned over the options actually shown, in the listed order; `y` takes the `Recommended:` letter. `edit` is answered as `<letter> <feedback>` or `edit <feedback>`; `y` on a recommended `edit` asks for the feedback before acting.

Hand-off routing: `spawn` on `proceed in a new session`; `in-session` on `proceed` and `apply and proceed`.

Every plan change at this gate goes through the writer when step 7 dispatched one (a bare or fast-path plan: targeted `Edit`s here, since this session wrote it): step 7's **Resume** carrying the items, and read its report — its `rejected:` lines join `⚠ Writer rejected`, its `assumptions:` replace `⚠ Writer assumptions`. `apply and proceed` → one item per gate finding that has a recommended option (its `fix:` text or that option); `(no recommendation)` findings are left for the next pass; re-run `plan_summary.py` (its output feeds the plan comment below). A report with any `rejected:` line → the user approved something the plan does not carry: show this gate again with those lines (no new critique round), never exit. Otherwise route as `proceed`. `apply and re-present`, `edit` or `restart` → `lib/plan-gate-rare.md` "Loop-back answers".

The `Recommended:` line is a recommendation, not a block — picking any approving option hands off regardless of what was recommended; `y` only means "take the Recommended letter". Gate exits on any approving option — never on `edit`, `apply and re-present` or `restart`, which loop back instead. Once the gate exits on an approving option: post a plan comment, same mechanism as step 5.5 — unconditional on which approving option was picked. Body = the "Plan summary" block already built for the gate prompt (goal, approach, tasks, files touched, risks) — never the raw plan file. Write it to a temp file, then post it with step 5.5's call and record the approving option as a flow event, in one call (`<answer>` = the option's own name, hyphenated: `proceed`, `apply-and-proceed`, `proceed-in-a-new-session`, …):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_tracker_comment.py" "<KEY-or-empty>" "<tmpfile>"
python3 "$R/lib/flow_telemetry.py" event --type gate --field skill=simo-dev:task-intake --field gate=plan --field answer=<answer>
```

Announce `Plan: <post_tracker_comment.py's stdout line>`.

### 8. Hand-off

Discipline: `simo-dev:tdd` (default for feature/fix) + `simo-dev:debug` on unexpected red.

The hand-off routing was already decided at step 7.5's gate: `in-session` → the In-session worktree row below; `spawn` → the start_session row below. No question asked here.

- **In-session worktree**: classify by step 3's `row:` line, already computed this run (no action yet). On a row 1 or row 2 classification, build the carry commit first (`lib/worktree-carry.md`'s mechanism, `<repo>` = step 3's own `<TOP>` — equal to `<REAL>` on a main checkout, not on a submodule — through the `<pre-sha>`/`<carry-sha>` capture and the `switch` back, stopping at that file's own phase-marker comment). Then run step 6's own procedure in full (whichever row it resolves to — this reclassification is authoritative, the one above was only to decide whether to carry first). Then — row 1/2 only — resume at `worktree-carry.md`'s `cherry-pick -n <carry-sha>` + `reset --quiet` line, into step 6's `<WT>` — on row 1/2 `<repo>/.worktrees/<name>`, `<repo>` being the toplevel on row 1 and `<REAL>` on row 2 (`<TOP>` was only row 2's carry *source*, never the destination) — step 6 itself, not this bullet, confirms the exact path. Row 3/4: skip the cherry-pick, nothing to carry (rows 3s and 4 stop inside step 6; a bare "row 3" is 3a/3b). Only then continue with `simo-dev:plan-execute`; Bash cwd does NOT follow the worktree — `git -C "<worktree-abs>"` + absolute paths for everything. State the worktree path and this rule. Never suggest start_session from here: the user chose this session at the gate.
- **start_session**: reached only via the `spawn` routing (no bypass condition held, so this is a main checkout or row 2, never a submodule). `mcp__ccd_session__start_session` not yet callable → `ToolSearch` `select:mcp__ccd_session__start_session` once, never skipped because the name is unlisted; no schema, or no `ToolSearch`, counts as not available → say so in one line and take the In-session worktree row. `<REAL>` is not this session's project → the same fallback (a `cwd` start needs another project's folder). Invoke it with `initiation` `user_asked` (the user picked the new session), the child rooted at the repo root (`<REAL>` — the main checkout). On this session's own row 1/2 (same classify-only-first reuse as the In-session row), build the carry commit first, same mechanism, `<repo>` = `<TOP>`. The prompt (`lib/handoff-spawn.md`) must NOT carry this session's step 3 values — the child's location is something this session cannot predict, so the child runs step 3 itself. It only needs the branch name and `<base>`, plus — row 1/2 only — `<carry-sha>`. Print `<carry-sha>` (when built) and its one-line recovery pointer (`git -C <repo> cherry-pick -n <carry-sha>` from the base checkout) in this session's own output before spawning, so a failed child doesn't leave it unrecoverable-looking. Field detail: `lib/handoff-spawn.md` — load when building the call. After the call the child is running; it stays linked, and this session is announced archivable only under `lib/handoff-spawn.md` **Keep the child linked**'s condition — archiving it can archive the child too.

## Notes

- This skill never edits source code or commits — editing happens in `simo-dev:plan-execute`, plan-file commits are handled by the caller's gates.
- Step 3 opens with the repo pre-flight, then Explore runs on the current tree BEFORE the worktree exists (intentional: the analysis answers "what changes relative to base"). For an existing feature branch, check it out before invoking.
