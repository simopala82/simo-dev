# Canonical task flow

The default end-to-end flow for a ticket-driven task. Each step is a skill in `.claude/local-marketplace/simo-dev/skills/`.

## Steps

1. `simo-dev:task-intake <TICKET-KEY-OR-PROMPT>` — accepts a ticket key OR a free-text prompt. Runs high-level system-impact analysis (parallel Explore subagents on Sonnet), then opens the **Direction confirmation gate** (always-on): user can `y` / `redirect` / `deep-dive`. On `deep-dive`, escalates to `simo-dev:grill-me` (design mode for open scope, decisions mode for punctual decisions) per heuristic. Optionally creates a ticket (prompt mode), produces a detailed plan via `simo-dev:plan-write` (on the current checkout — no worktree yet), which runs its own plan-critique and **Plan review gate** before returning — one gate with two shapes: `y`/`edit`/`restart` normally, or `y`/`n`/`edit`/`restart` when the hand-off signal fires, its `y`/`n` deciding in-session vs `spawn_task` (new branches only) (ADR 0015) — the worktree is still created only once hand-off executes, by whichever side executes. Hand-off prompt to the implementation session carries the TDD-default and systematic-debugging-on-red discipline instructions.
2. `simo-dev:plan-execute` — implement the plan in the worktree. Default mode: `simo-dev:tdd` for feature/fix work. On any unexpected red: switch to `simo-dev:debug` (hypothesis → isolate → verify → patch).
3. `simo-dev:review-loop` — gates on `simo-dev:verify-done` first (tests/build green, fresh evidence). Then auto-fix medium+ findings via `simo-dev:code-review-protocol`, with its validity filter applied to each finding BEFORE the fix (drops hallucinations / context-blind suggestions). Cap = `CAP`: 5, or a lower value the invoker passes for a prose-only diff (step 1.5); any surviving `high`/`critical` finding restores 5. Presents aggregated diff + rejected list before exit. A clean ship-flow run cascades into `simo-dev:commit` (standalone review requests and headless runs stop at the report).
4. `simo-dev:commit` — auto-runs `simo-dev:review-loop` first unless a review already covers the pending files (no ask; skips when no file changed after the last review), then step 2 runs the full three-question knowledge harvest (`skills/commit/lib/knowledge-harvest.md`), commits with conventional format scoped to the ticket, pushes (no gate on feature branches; a pre-push MR-state guard stops the push if the branch's MR was merged/closed mid-session — `lib/check_mr_state.py`; a guard that never answered stops first — the script is not there, or it printed nothing whatever it exited with — and only then the codes: exit 0 open / 1 terminal → stop / 2 unverifiable → stop / 3 absent → push), renders an informational mermaid preview, and creates the MR unconditionally (no gate). Protected branches keep their single commit+push confirmation gate instead (no MR).
5. `simo-dev:mr-followup` — after push, watches the pipeline, dispatches a local `simo-dev:code-reviewer` on the branch diff, and reads the MR/PR review comments. Applies the same `simo-dev:code-review-protocol` validity filter to medium+ findings BEFORE auto-fixing (remote comments come from reviewers without this session's context). Findings that pass the filter are fixed, committed, and re-pushed unconditionally (no gate; same MR-state guard before each re-push). Cap = `CAP`, same default, floor and escalation as review-loop (step 1.5); what the last permitted round *does* differs — here it surfaces without fixing. A clean-exit round runs a harvest catch-up pass against the same file before reporting (see below). Step 8 (post-merge) transitions the ticket to its configured `post_merge_state` (default `Ready for Testing`), writes the "Verification notes" comment (consumed at release time by the release skill's per-ticket verify step), cleans up the worktree, and appends the dev-flow journal entry (step 8.5).

## Ticket auto-transitions

Forward-only, idempotent, opt-out, best-effort. The canonical flow walks the ticket through `In Progress` (intake) → `CodeReview` (post-push when MR exists) → a per-prefix `post_merge_state` (post-merge; defaults to `Ready for Testing` when unset). `Ready for Deploy` and `Done` happen at release time: the release skill's post-deploy per-ticket verify step (QA deploy → `Ready for Deploy`, prod deploy → `Done`), driven by the "Verification notes" comment mr-followup step 8 leaves on the ticket. Each transition looks up `transition_cmd` for the ticket's prefix (`lib/tracker_config.py`, `~/.claude/simo-dev/tracker.json`) and invokes it if configured; not configured (e.g. a read-only tracker) → the step is a no-op, no special-case in any skill body. Never moves the ticket backward. A `transition_cmd` failure — any non-zero exit, including a `--leaf-only` refusal on an **umbrella** (a key with sub-tasks) or a `--sync-parent` exit 7 (the umbrella's sub-tasks are all at/past Done — verify the whole feature and close it by hand) — only prints a warning; the flow always continues. `--leaf-only` never advances the umbrella key directly; `--sync-parent` (added on every leaf transition) keeps it in sync with the aggregate of its sub-tasks instead — see `simo-dev:task-intake` step 2.5. Set `DEV_FLOW_NO_TRACKER=1` (or drop a `.claude/no-tracker` marker) to skip all transitions for the session.

## Configuring the tracker

Nothing above is JIRA-specific — `lib/tracker_config.py` maps a ticket-key prefix (`CUS`, `ACME`, whatever a project uses) to the commands that fetch, transition and create tickets for it. Config lives at `~/.claude/simo-dev/tracker.json` (global per user, not versioned in this repo; override for tests only via `SIMO_DEV_TRACKER_CONFIG` or `--config`).

```bash
python3 lib/tracker_config.py get <PREFIX> <FIELD>
python3 lib/tracker_config.py set <PREFIX> <FIELD> <VALUE>
python3 lib/tracker_config.py run <PREFIX> <FIELD> [ARGS...]
```

Fields, one per prefix:

- `fetch_skill` — resolves a ticket's title/description/AC/status/links given its key (`task-intake` step 2).
- `transition_cmd` — invoked with the ticket key and a target state to move it forward (see above).
- `create_skill` — creates a new ticket (`task-intake` step 5, `simo-dev:task-intake` step 2.5 for sub-tasks).
- `supports_subtasks` — `true`/`false`; gates automated sub-task creation for cross-repo/team work.
- `post_merge_state` — the state a ticket lands on post-merge; defaults to `Ready for Testing` when unset.
- `scope_fetch_cmd` — fetches ticket-view text for `lib/jira_scope.py`; `-` means explicitly declined, treated the same as unconfigured.

An unconfigured field is a silent no-op, never an error — a read-only tracker, or no tracker at all, both work out of the box. To disable tracker integration entirely for a repo or session: `DEV_FLOW_NO_TRACKER=1` or a `.claude/no-tracker` marker file.

Example — wiring `CUS` to an existing `jira-cli` wrapper (illustrative; any command that accepts the same arguments works):

```bash
python3 lib/tracker_config.py set CUS transition_cmd "jira-cli transition"
python3 lib/tracker_config.py set CUS post_merge_state "Ready for Testing"
```

## Proactive review-loop auto-run — runtime behavior

When implementation work concludes, the main thread auto-invokes `simo-dev:review-loop` and proceeds — no up-front ask — BEFORE the user asks to commit/push/merge. Catches issues earlier. It skips only when the loop is clearly not useful (trivial doc/typo edit, empty diff, or a review already covering the pending files). CLAUDE.md carries the eager trigger list — the linguistic phrases, the implementation triggers and the pre-commit/push/merge points — because it is the only surface that carries rule *bodies* on every turn (skill `description` fields are in the listing every turn too, and some do carry an imperative — `simo-dev:commit`'s says never to run `git commit` directly — but a description is budgeted in sentences, not in rule sets). `simo-dev:guide` restates the list for whoever loads that file; neither it nor this one loads by itself. The one mechanical backstop is `hooks/review_loop_warn.py`, self-gated to `git commit` alone. Two skip conditions are decided by code — "a review already covers the pending files" (`lib/review_marker.py` compares each pending file's mtime against the last review record) and "empty diff" (the hook exits silently on an empty candidate list); "trivial doc/typo edit" is the judgment call. The loop still gates on unresolved findings; a clean run hands off to `simo-dev:commit` directly (step-8 exceptions: standalone review request, headless run, commit pre-flight).

If review-loop runs clean, the next `simo-dev:commit` invocation will default the review-loop gate to skip (per the deterministic `lib/review_marker.py check` in `skills/commit/SKILL.md` step 1). The record lives in `~/.claude/state/review-loop`, written by `hooks/review_record.py` when the code-reviewer subagent *concludes* (`SubagentStop`) — an observed event, not a claim, and deliberately not the `Agent` tool returning, which for a background dispatch is only the launch acknowledgement. The event is necessary and not sufficient: the hook also gates on a non-empty `last_assistant_message` (absent when the subagent was interrupted), on a line-1 `REPO:` marker naming an existing git repo, and on a session id (read the hook for the full set). There is no TTL: a commit is covered when no file in it is newer than the last review.

## Model strategy

`lib/model_strategy.py` (get/set on `~/.claude/simo-dev/model-strategy.json`, four fixed categories) lets a user pick which model backs each of this plugin's own `Agent` dispatch points, independently of whatever model the session itself is running. `simo-dev:model-strategy` wraps it interactively — easier than the raw CLI shown below.

- Main thread: runs whatever model the session is configured with (e.g. via `/model`) — this plugin does not configure or auto-switch it. The categories below are this plugin's own dispatch points, not the session model.
- `explore_research`: `Explore`'s one dispatch site (`task-intake` step 3) reads `lib/model_strategy.py get explore_research`, defaulting to `sonnet` when unconfigured (today's behavior, unchanged) — no longer a hardcoded pin at that call site. `general-purpose` has no dispatch site anywhere in this plugin, so it stays pinned to `sonnet` in its own agent frontmatter (`~/.claude/agents/general-purpose.md`) with no config hook.
- `code_review`: `simo-dev:code-reviewer`'s three dispatch sites (`code-review-protocol`, `mr-followup` local review, `mr-review` step 5) each read `lib/model_strategy.py get code_review` before dispatch and pass `model:` only when non-empty — still no `model:` pin in `agents/code-reviewer.md` itself; an empty config reproduces today's inherit-the-caller's-tier behavior. `Plan` (the built-in agent) has no dispatch site in this plugin, so it is unaffected and keeps inheriting the caller's tier with no config hook.
- `plan_critique`: `simo-dev:plan-write`'s own review gate dispatches a new `simo-dev:plan-reviewer` agent after writing the plan, reading `lib/model_strategy.py get plan_critique` and defaulting to `fable` when unconfigured — a call-site default, same pattern as `explore_research`. Set it to `none` to disable automatic plan critique dispatch entirely.
- `plan_execute`: `simo-dev:plan-execute`'s own execution delegation gate (step 1) reads `lib/model_strategy.py get plan_execute` before each per-task/per-batch `Agent` dispatch and passes `model:` only when non-empty — same inherit-the-caller's-tier default as `code_review`, no `none` sentinel (that concept is `plan_critique`-specific).
- Background `Agent` calls (not `Bash`) doing mechanical work (log tail, doc generation, wide read-only scans) are not wired to any category above, and no script in this plugin dispatches one today — treat that as a documented intention for a future dispatch, not enforced behavior.
- Every subagent this plugin dispatches (`Explore`, `code-reviewer`, `plan-reviewer`, or any other `Agent`-tool child) must work in-line within its own turn and return its findings as its final message — never spawning its own `Agent` children, never using `run_in_background`. A subagent can't await its own background children, so the dispatching skill would get an empty punt instead of the results.

Example — opting `code_review` into a higher-tier model (illustrative; the config file does not exist until the first `set`, so nothing here changes behavior by default):

```bash
python3 lib/model_strategy.py set code_review opus
```

## Dev-flow journal & knowledge harvest

After each merged task, `simo-dev:mr-followup` step 8.5 appends one block to `~/.claude/state/dev-flow-journal.md`. Each entry records: `mode` (flow path: direct/grill-me, derived from the skill logger), ticket, task, model, effort, tokens, skills used, review-loop iterations, post-MR auto-fixes, pipeline-first-try, problems/friction, notes. No A/B experiment framing — this is a personal operational log.

The knowledge harvest — a three-question session review routing durable facts to their home:
domain terms → `CONTEXT.md`, hard-to-reverse decisions → `docs/adr/`, gotchas/invariants →
`CLAUDE.md` or a reference file — runs pre-merge, not here. `commit` step 2 runs the primary,
full pass before every commit; `mr-followup` step 7's clean-exit branch runs a lighter catch-up
pass scoped to what emerged during the review/fix cycle. Both against
`skills/commit/lib/knowledge-harvest.md`. Existing homes, or a newly *approved* one (the
procedure asks once before creating `CONTEXT.md` / `docs/adr/` / a repo-specific file the first
time a repo lacks one) — never an invented one. Skip when nothing qualifies.

## Code review severity

Used by `simo-dev:review-loop` and `simo-dev:mr-followup`.

- `low` — style/cosmetic. Never auto-fixed; surfaced as informational.
- `medium` — code smell, missing test coverage, minor bug, perf concern. Auto-fixed.
- `high` — correctness bug, security issue, data-loss risk. Auto-fixed.
- `critical` — blocks merge. Auto-fixed; surfaced even if iteration cap is reached.

Reviewer subagent must emit findings as `[severity] file:line — message` (one per line).

# Install & maintenance

## Install (teammate)

See [`README.md`](../README.md) → Installation for how to install (GitHub is the only documented route), and → "What to paste into your CLAUDE.md" for the external-dependency checklist (the eager rules and the `Explore` agent definition) that isn't bundled with the plugin.

## Local dev refresh

The `simo-local` marketplace is a directory source: the installed copy is cached at `~/.claude/plugins/cache/simo-local/simo-dev/<version>/` and `claude plugin update` is a **no-op unless `plugin.json` `version` is bumped**. After editing the plugin source in place, force a fresh cache with:

```bash
claude plugin uninstall simo-dev@simo-local && claude plugin install simo-dev@simo-local
```

(or bump `version` in `plugin.json`). Restart Claude Code / `/reload-plugins` to load the refreshed skills.
