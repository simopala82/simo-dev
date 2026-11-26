---
name: review-loop
description: Use after implementation, before commit, while the simo-dev flow is active (outside it, only when the user asks for a review). Iteratively requests code review via the simo-dev:code-reviewer subagent, auto-applies fixes for medium/high/critical findings (plus `low` on diff files, per canonical-flow "In-scope improvements"), re-reviews until clean (cap 5; pass `cap=2` when the diff is prose only — no code and no shell/python inside the prose; optional `--target <branch>` overrides the base, else the stacked parent or `origin/HEAD` as in simo-dev:commit; optional `--draft`/`--no-draft`, only forwarded to simo-dev:commit), then presents the aggregated diff. Clean ship-flow runs cascade straight into simo-dev:commit; standalone or headless runs stop at the report. Third step of the canonical task flow.
---

# Dev Review Loop

Pre-commit review iteration. Auto-fix medium+ issues, plus `low` on diff files per In-scope improvements, until clean, then hand off to `simo-dev:commit`.

## Severity model

Per `~/.claude/local-marketplace/simo-dev/docs/canonical-flow.md` "Code review severity" — `low` on a file in the diff applied per "In-scope improvements" (same file; step 4), otherwise informational; `medium`/`high`/`critical` auto-fixed (`critical` surfaced even at cap). Findings format: `[severity] file:line — message`, one per line.

## Steps

### 0. Verification gate (pre-flight)

`simo-dev:verify-done` BEFORE any reviewer subagent — no "ready for review" without fresh evidence that the code compiles and tests are green.

**0a. Identify the verification command** from the worktree root:

| Project signal | Verification command |
|---|---|
| `*.sln` / `*.csproj` | `dotnet build` + `dotnet test` |
| `package.json` with `test` script | `npm test` (or pnpm/yarn per lockfile) + `npm run lint` if defined |
| `pyproject.toml` / `uv.lock` | `uv run pytest` + `uv run ruff check` if configured |
| `Cargo.toml` | `cargo test` + `cargo clippy` |
| `go.mod` | `go test ./...` + `go vet ./...` |
| none of the above | if a `tests/` dir or `*_test.py` files exist, suggest those; else ask once: "No standard test target detected. Verification command? (skip to proceed without verification)" |

**0b. Run it fresh** in the worktree — never trust a previous run. A project in a subdirectory (a `web/` package, a nested solution): the tool's directory flag, else a subshell, never a bare `cd` (`simo-dev:verify-done` gate step 2).

**0c.** All exit 0 → step 1. Any non-zero → **stop**: surface command, exit code, last ~20 output lines; tell the user to fix and re-run (reviewing broken code wastes the reviewer's budget). Explicit `skip` → note it in the summary and continue.

On the success branch of 0c (all commands exit 0), post a "Tests performed" comment. Body = the command(s) run, their exit codes, and a one-line result per command — the same evidence 0c already surfaced. Write it to a temp file, then (`--branch-of` derives the ticket key from the worktree's branch, as `simo-dev:commit` does):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_tracker_comment.py" --branch-of "<worktree-abs>" "<tmpfile>"
```

Announce `Tests performed: <script's stdout line>`. An explicit `skip` at 0c posts nothing — there is no evidence to report.

### 1. Empty-diff guard

Diff vs the merge base with the base resolved by step 2's block (stacked parent when there is one, else the default branch): run that block's lines up to `echo "REVIEW BASE: $BASE"` first. Empty **and** `git status --porcelain --untracked-files=all` also empty → nothing changed → "Nothing to review — diff is empty." and stop. Empty **but** the status is non-empty → most commonly no commits yet on this branch, but any range that nets to no change (e.g. a no-op commit) hits the same guard — not a real empty diff, proceed to step 2: its dispatch, via `code-review-protocol` step 1, applies that step's working-tree fallback (gated on the same range-diff-empty test, not on SHA equality).

### 1.5. Iteration cap

`CAP` = **5** unless the invoker passed a lower value (`cap=<n>`). 5 is the ceiling and **2 is the floor** — a passed value may only lower it
within that range. Below 2 `simo-dev:mr-followup` would run no fix round at all, so the value stops meaning anything (the other one would surface immediately), so a passed 1 or 0 is
clamped up to 2 — never rejected into the default of 5, which would silently cost more than asked.
The invoker may also pass `--target <branch>` (optional): it overrides step 2's base detection
(stacked parent, else `origin/HEAD`), exactly as `simo-dev:commit`'s own `--target` does.
It may also pass `--draft` or `--no-draft`: review-loop ignores it and only forwards it to every
`simo-dev:commit` invocation below, so the MR opens Draft (or not) as `simo-dev:commit` documents.

Pass `cap=2` when the diff is prose and nothing else: no code, and no executable content inside the
prose either. Do not infer it from which file changed: the bodies this flow edits most are dense
with runnable blocks (`commit` 15 `bash` fences, `mr-followup` 11, `task-intake` 11 — measured
2026-08-22, indented fences included). So judge by whether a changed line falls inside a fenced
`bash`/`sh`/`python` block, in any file. The judgment belongs to the invoker, which has the diff in
context; deriving it from file extensions was tried and abandoned (see the plan behind this rule).

Know the cost before lowering it: a **cap-reached** exit stops with that iteration's fixes never
re-reviewed, while a clean exit does not — that one ran a review which found nothing. A lower cap
makes exhaustion likelier, so it trades one unreviewed round of fixes for the rounds it saves.
`simo-dev:mr-followup` does not share this: its last permitted round surfaces without fixing.

A `high` or `critical` finding that survives the validity filter raises `CAP` back to 5 for the rest
of the iterations, so a cap passed in error cannot weaken the review. Evaluate that at step 7,
before the cap-reached branch. Announce the value in one line so the report is auditable.

### 2. Run review (iteration N, starting at 1)

```bash
git -C "<worktree-abs>" fetch origin --prune --quiet
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
TARGET_ARG="<the --target skill arg, or empty>"
BASE=$(python3 "$R/lib/build_mr_ctx.py" --repo "<worktree-abs>" --target "$TARGET_ARG" --print-target) || { echo "STOP: could not resolve the base branch (stderr above)."; exit 1; }
if [ -z "$BASE" ]; then
  # origin/HEAD unset: --print-target prints an empty line; same fallback as
  # lib/check_base_freshness.py's default_base().
  for cand in main master; do
    if git -C "<worktree-abs>" rev-parse --verify --quiet "origin/$cand" >/dev/null 2>&1; then
      BASE="$cand"
      break
    fi
  done
fi
[ -n "$BASE" ] || { echo "STOP: cannot determine base (origin/HEAD unset, no origin/main|master)"; exit 1; }
echo "REVIEW BASE: $BASE"
BASE_SHA=$(git -C "<worktree-abs>" merge-base HEAD "origin/$BASE")
DIFF=$(python3 "$R/lib/diff_capture.py" "<worktree-abs>" --include-untracked -- "$BASE_SHA")
DIFF_RC=$?
printf 'rc=%s\nBASE_SHA=%s\n%s\n' "$DIFF_RC" "$BASE_SHA" "$DIFF"
[ $DIFF_RC -eq 0 ] || { echo "STOP: diff capture failed (rc $DIFF_RC) — do not dispatch the reviewer on a partial/empty diff."; exit 1; }
```

The base is the one `simo-dev:commit` targets (`--target` → stacked parent → `origin/HEAD`), so a stacked branch is reviewed against its parent, without the parent's commits in the diff. Not detected, with no `note:`: a branch with no commits of its own yet (all work in the working tree) whose parent advanced on the remote after the cut — the base silently falls back to the default branch and the parent's commits are in the diff; pass `--target <parent>` there. A `note:` on stderr (a diverged stacked candidate) is relayed in one line, not asked about here — `simo-dev:commit` step 0.5 asks. A `STOP:` (rc 2/3) → ask the user which branch this one targets and re-run with `--target <branch>`; under `simo-dev:afk` stop and record it, never guess.

The printed `BASE_SHA=` value is the literal step 5's branch-point check uses.

Invoke `simo-dev:code-review-protocol` (requesting side), passing `$DIFF` as the diff content for the dispatch prompt and `$BASE_SHA` for its `REPO:`/marker use — in place of that protocol's own default `origin/develop`, avoiding a base mismatch between the two. This dispatch already has a gated, computed diff in hand, so code-review-protocol's own item-1 capture (including its fallback) does not run a second time for this caller — per the note that fallback carries. `$DIFF` is already gated by size at capture (`lib/diff_capture.py`, ~50k-char threshold, same number everywhere in this plugin) — forward it unmodified, never re-sliced downstream. If a trailing `Full diff written to: <path>` line is present, that path can be read with `Read` if the full content is ever needed. Dispatching the reviewer agent directly (instead of via this protocol) skips its anchored `REPO:` first-line rule, and a dispatch without that line writes no review record, so `simo-dev:commit` step 1 runs another full loop.

**Iterations ≥ 2 — memory of prior iterations.** A resume of iteration 1's reviewer needs `SendMessage` callable this session. When it is not already callable — listed only as a deferred tool, or not listed at all — run `ToolSearch` `select:SendMessage` once before choosing; never skip that load because the name is not listed, or every re-review silently becomes a fresh reviewer. Once callable, a resume is cheaper, still holds its own iteration-1 reasoning, and records coverage like a fresh dispatch (`SubagentStop`); when the load returns no schema (or no `ToolSearch` tool exists), always fall back to the fresh `Agent` dispatch described below — never a workaround that fakes a resume (see the trap described below). But `SendMessage` **returns immediately** with `resumed … in the background` — that acknowledgement is not the review. Resume means calling the `SendMessage` tool itself with that agent's id — dispatching a fresh `Agent` call instead, even one whose prompt opens with `to: <agentId> ...` to **simulate** a resume, spawns a brand-new session with none of iteration 1's memory and no `REPO:` line as its first line, so it writes no attributable review record at all. It can still read the diff fresh and return a coherent-looking review, so nothing fails at runtime — the gap surfaces only later, at `simo-dev:commit` step 1's `lib/review_marker.py check` (rc 1, "changed after the last review"). Parse the agent's task notification when it arrives; parsing the return value yields zero findings and step 4 reads that as a clean exit. **Never edit files while ANY review is in flight** — resumed or dispatched, and a dispatch is background unless it passes `run_in_background: false`: the record's epoch is the reviewer's conclusion time, so an edit made in that window reads as covered by a review that never saw it. A fresh dispatch is stateless instead. **Both paths carry the memory block** — the caller produced or judged all of it after the resumed agent concluded — in the resume message, or, for a fresh dispatch, **below the `REPO:` line**, never above it: above, the marker stops being line 1 and the record is never written. Open it with "The following were already evaluated, each with the reason given. Do NOT re-flag them unless you can refute the rationale with new evidence.", then three **cumulative** lists — every iteration's entries, never just the last one's:

- **dropped by the validity filter** — each prior `rejected:` line, with its reason; `low`s rejected, declined by the user or `deferred (needs decision)` go here too, with that reason.
- **confirmed correct** — code an earlier iteration examined and judged right, with the reason: the fixer's `confirmed-correct:` lines (step 4.5), or this session's own reading when steps 5-6 ran here. No reviewer output carries it.
- **verified by the caller** — what was asserted and the exact command (the fixer's `verified:` lines, step 4.5), including the checks run while applying fixes and not step 0's pre-flight alone. Instruct the reviewer to attack those checks (does the command discriminate? would it fail if the behavior broke?), never to repeat them.

Anything left **unresolved** belongs in the prompt too — `escalated-during-fix:` lines, step 5's **ambiguous** drops, step 3's malformed findings — under their own label, "still open — re-raise if the code is still wrong", and **outside** the clause above: they are open, not settled. Listing them under that clause tells the reviewer to stop raising a live medium+ finding.

### 3. Parse findings

Regex `^\[(low|medium|high|critical)\]\s+([^:]+):(\d+)\s+—\s+(.+)$` (file:line required). Malformed → surface as unresolved. Group by severity.

### 4. Filter and decide

**A clean exit requires the reviewer's `verdict:` line.** No findings AND no verdict is not a clean review — it is what a `SendMessage` acknowledgement (`resumed … in the background`) parses to, and step 3's regex matches nothing in it. Treat a missing verdict as "the review has not arrived yet": wait for the agent's task notification, never exit on it. This is the mechanical guard behind step 2's resume instruction; the prose there alone would let the default path false-pass into `simo-dev:commit`.

Verdict present, no findings ≥ medium and no applicable `low` → **clean exit**, step 8. A `low` is **applicable** when it sits on a file in the diff, an iteration remains under `CAP` (judged against the current `CAP`), and it was **not already eligible in an earlier iteration**: same file and message as a `low` that was eligible before (an iteration remained under `CAP` then) → not applicable, whatever happened to it — applied, rejected, declined, deferred, ambiguous or escalated. This bounds each `low` to deferring the clean exit at most once. A `low` only listed because it first appeared on the last iteration stays eligible if step 7's escalation later raises `CAP`: the next review re-emits it and this rule lets it in. Else collect medium+ and applicable `low`s for step 5 — including when the applicable `low`s are the only findings (the clean exit is then deferred); note the other `low`s for the summary.

Applicable `low`s are applied; one is an In-scope improvement only on an all-`pre-existing` branch-point check (step 5; `docs/canonical-flow.md` "In-scope improvements"). On the last iteration a `low` is listed only, never applied: editing after a clean exit invalidates the review record (`lib/review_marker.py check` flags the file as changed after the last review) and costs a full extra loop. A `low` needing a user decision → ask; unattended → `deferred (needs decision)`, listed in the report.

### 4.5. Fixer subagent (runs steps 5-6)

Steps 5 and 6 run in a clean-context fixer subagent, not in this session: checking and fixing findings means reading code, and that reading would otherwise stay in this session's context, which every later call (the next review round, commit, mr-followup) re-reads. This session keeps the reviewer dispatches, the parsing and step 4's decisions, and gets back only the fixer's report. `Agent` tool unavailable → read `lib/fixer-steps.md` and run steps 5-6 here, as written there.

Resolve the two paths once:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
echo "$R/skills/review-loop/lib/fixer-steps.md"; echo "$R/docs/canonical-flow.md"
```

Dispatch one `Agent` (default `subagent_type`), `run_in_background: false`, `model:` the `plan_execute` value (`lib/model_strategy.py get plan_execute`, omitted when empty). The prompt carries:
- the two printed paths: read the first file's sections `## 5. Validity filter` and `## 6. Auto-apply fixes` and follow only them, plus `canonical-flow.md`'s "In-scope improvements" before the first branch-point check;
- the literals those sections use: `<worktree-abs>`, `<BASE_SHA>` (step 2's `BASE_SHA=`), `<BASE>` (step 2's `REVIEW BASE:`), whether the branch is protected, and the printed plugin root (the first path above minus `/skills/review-loop/lib/fixer-steps.md`), with the instruction to replace every `R=...` line of those sections with `R="<that root>"`, so its scripts are the version that rendered this body;
- the findings step 4 collected, verbatim, each marked `medium+` or `low`, plus any fix the user approved for a `deferred (needs decision)` finding, marked `approved` (apply it, no decision left);
- the earlier iterations' `applied:`, `rejected:` and `ledger-add:` / `ledger-drop:` lines, cumulative, so it neither re-applies what was turned down nor undoes an earlier fix unknowingly, and runs `drop` only on a line actually added;
- the rule that it may modify only files already in the diff, and new files only when a finding requires one;
- the line "Write throwaway files only under a fresh `mktemp -d /tmp/claude_<slug>.XXXXXX` of your own; never write into or overwrite files in the Scratchpad directory your environment lists — it belongs to the caller's session." verbatim;
- invoke no skill (not `simo-dev:review-loop`, `simo-dev:commit` or any other, whatever a trigger rule says about green tests), never commit, never push, never dispatch an `Agent`, never ask the user: a `low` needing a user decision is reported `deferred (needs decision)`, a `medium+` one `ambiguous` (unresolved, as before);
- the report, as its final message, only lines of these kinds, every finding it received on exactly one of the first five:
  ```
  applied: [severity] file:line — fix description
  rejected: [severity] file:line — message → reason
  ambiguous: [severity] file:line — message → what is unclear
  escalated-during-fix: [severity] file:line — reason
  deferred (needs decision): [severity] file:line — message → the decision
  ledger-add: <file> — <what> — <why>
  ledger-drop: <file> — <what> — <why>
  ledger-error: [low] file:line — <branch-point verdict error, or LEDGER_FAILED>
  plan-resync: <plan> — <what was resynced>
  plan-drift-error: <check_plan_drift.py rc 2 output>
  confirmed-correct: <code examined and judged right> — <why>
  verified: <command> → rc <n>
  ```

The fixer is the only writer while it runs (foreground, one at a time), so it appends to the ledger itself, unlike `simo-dev:plan-execute`'s parallel dispatches. A return that is a background-launch acknowledgement, not a report → the fixer is still running: wait for its task notification, and edit nothing, dispatch no review and capture no diff until it arrives.

Its report feeds the rest of the loop: `applied:` (and `plan-resync:`, `plan-drift-error:`) is the fix log; `rejected:`, `ambiguous:` and `escalated-during-fix:` go where steps 5-6 send them; a received finding on none of the first five kinds counts as unresolved; `confirmed-correct:` and `verified:` go into step 2's memory block for the next iteration; `ledger-add:` lines are step 8's item (7), minus any later `ledger-drop:` of the same line, `ledger-error:` its item (8). `deferred (needs decision)` → ask the user (unattended: listed in the report as `deferred (needs decision)`); approved fixes go to a second fixer dispatch in this same round, with only those findings plus this round's first report (so it sees what was just applied and ledgered), before step 7, so a clean exit or the cap cannot drop them; a `deferred (needs decision)` line in that second report is unresolved, with no third dispatch. A declined or unanswered deferral is a `low` listed in step 8's item (6). A report with no recognizable line → the round's findings are unresolved, step 8. Then step 7.

### 5-6. Validity filter and auto-apply fixes

Run by step 4.5's fixer from `lib/fixer-steps.md`. This session reads that file only when step 4.5 runs them here (`Agent` unavailable).

### 7. Iterate

Apply step 1.5's escalation first: if this iteration produced a `high` or `critical` finding that survived the validity filter, `CAP` is now 5 — recompute the condition against that value, so the loop continues automatically rather than reaching step 8 and asking the user to iterate again.

An iteration whose only collected findings were applicable `low`s, none of them applied (whatever the reason), left the tree unchanged → **clean exit**, step 8 — or, when any is left unresolved, step 8's unresolved branch without its "iterate again" option: the next review would see the same tree, and none of those `low`s is applicable again.

Then increment N. N ≤ CAP → record the retry, then back to step 2:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" event --type retry --cwd "<worktree-abs>" --field skill=simo-dev:review-loop --field iteration=<N>
```

N > CAP with remaining medium+ → stop, step 8 with the unresolved list.

### 8. Present and hand off

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/diff_capture.py" "<worktree-abs>" --include-untracked -- HEAD
DIFF_CAPTURE_RC=$?
[ $DIFF_CAPTURE_RC -eq 0 ] || echo "WARNING: diff capture failed (rc $DIFF_CAPTURE_RC) for the report — show this failure in place of item (5)'s content, do not block the rest of step 8 on it."
```

Show, in order: (1) summary line (iterations, fixed by severity, unresolved); (2) fix log; (3) rejected findings with rationale; (4) unresolved findings by severity (critical always shown); (5) the script's output above (full diff if small, `--stat` + slices + temp-file path if large — same threshold, no special case for this report versus any other call site); this is the full working-tree-vs-HEAD diff — implementation plus any fixes applied during this loop combined (`review-loop` never commits, per this file's own Notes, so nothing here is committed separately) — not only the incremental per-iteration fixes in isolation; (6) `low` findings NOT applied (last iteration, deferred, rejected — decided at step 4, not here); (7) Also fixed — the lines this loop added to the ledger; (8) on its own labelled line, any `low` whose branch-point check was `error` (applied, not ledgered).

Then decide:
- Clean, ship-flow invocation (proactive trigger) → announce "Review loop clean → `simo-dev:commit`" and invoke `simo-dev:commit` directly (the merge remains the one hard gate downstream), passing `--target <branch>` when review-loop itself received one, so commit does not re-detect a different base, and `--draft` / `--no-draft` when it received one. Three cases stop at the report instead:
  - standalone review request (the user asked for a review only, e.g. "do a review") — report and stop;
  - headless / non-interactive run (an unattended headless caller, `claude -p`) — apply fixes only; commit/push/MR belong to the caller;
  - invoked as `simo-dev:commit` step-1 pre-flight — return to the calling commit flow, never re-invoke it (recursion).
- Unresolved, N ≤ CAP → "(1) skip → `simo-dev:commit`, (2) fix manually, (3) iterate again." Option (1) passes `--target`, `--draft` / `--no-draft` as review-loop received them.
- Unresolved, N > CAP (cap reached) → "(1) skip → `simo-dev:commit`, (2) fix manually." No further iteration. Option (1) passes `--target`, `--draft` / `--no-draft` as review-loop received them.

Whichever of the three branches above applies, the `simo-dev:commit` step-1 pre-flight case included (commit never posts this comment itself), post an "Implementation summary" comment before executing the branch, and record how the loop ended (`<answer>` = `clean`, `cap` or `unresolved`). Body = the summary line (iterations, fixed by severity, unresolved) + fix log + rejected-findings-with-rationale + unresolved-findings-by-severity — never the aggregated diff (too large and noisy for a ticket comment). Write it to a temp file, then, in one call:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/post_tracker_comment.py" --branch-of "<worktree-abs>" "<tmpfile>"
python3 "$R/lib/flow_telemetry.py" event --type gate --cwd "<worktree-abs>" --field skill=simo-dev:review-loop --field gate=review_exit --field answer=<answer>
```

Announce `Implementation summary: <post_tracker_comment.py's stdout line>`.

Post once per loop invocation, at this final step — never per iteration (N can reach `CAP`=5; only the final state matters to the ticket).

## Notes

- Never commits or pushes — hand-off is `simo-dev:commit`.
- The 5-iteration ceiling is non-negotiable — nothing may exceed it. Step 1.5 only lets the invoker pass a lower one, and any `high`/`critical` finding that survives the validity filter restores the full 5.
- Modifies only files already in the diff; new files only if a finding requires one (e.g. missing test).
