---
name: review-loop
description: Use after implementation, before commit. Iteratively requests code review via the simo-dev:code-reviewer subagent, auto-applies fixes for medium/high/critical findings (plus `low` on diff files, per canonical-flow "In-scope improvements"), re-reviews until clean (cap 5; pass `cap=2` when the diff is prose only — no code and no shell/python inside the prose; optional `--target <branch>` overrides the base, else the stacked parent or `origin/HEAD` as in simo-dev:commit), then presents the aggregated diff. Clean ship-flow runs cascade straight into simo-dev:commit; standalone or headless runs stop at the report. Third step of the canonical task flow.
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

**0b. Run it fresh** in the worktree — never trust a previous run.

**0c.** All exit 0 → step 1. Any non-zero → **stop**: surface command, exit code, last ~20 output lines; tell the user to fix and re-run (reviewing broken code wastes the reviewer's budget). Explicit `skip` → note it in the summary and continue.

On the success branch of 0c (all commands exit 0), post a "Tests performed" comment. Derive the ticket key from the current branch, same pattern `simo-dev:commit` uses:

```bash
W="<worktree-abs>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
BRANCH=$(git -C "$W" branch --show-current)
KEY=$(python3 "$R/lib/derive_ticket_key.py" "$BRANCH" --cwd "$W")
```

Body = the command(s) run, their exit codes, and a one-line result per command — the same evidence 0c already surfaced. Write it to a temp file, then — rebind `R` and substitute the `KEY` derived above as a literal, not `$KEY`: shell state, including variables, does not survive between Bash calls, so a bare `$KEY` here is empty and silently posts nothing:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/post_tracker_comment.py" "<KEY-or-empty>" "<tmpfile>"
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
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
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
- **confirmed correct** — code an earlier iteration examined and the caller judged right, with the reason. From the caller's own reading; no reviewer output carries it.
- **verified by the caller** — what was asserted and the exact command, including the checks run while applying fixes and not step 0's pre-flight alone. Instruct the reviewer to attack those checks (does the command discriminate? would it fail if the behavior broke?), never to repeat them.

Anything left **unresolved** belongs in the prompt too — `escalated-during-fix:` lines, step 5's **ambiguous** drops, step 3's malformed findings — under their own label, "still open — re-raise if the code is still wrong", and **outside** the clause above: they are open, not settled. Listing them under that clause tells the reviewer to stop raising a live medium+ finding.

### 3. Parse findings

Regex `^\[(low|medium|high|critical)\]\s+([^:]+):(\d+)\s+—\s+(.+)$` (file:line required). Malformed → surface as unresolved. Group by severity.

### 4. Filter and decide

**A clean exit requires the reviewer's `verdict:` line.** No findings AND no verdict is not a clean review — it is what a `SendMessage` acknowledgement (`resumed … in the background`) parses to, and step 3's regex matches nothing in it. Treat a missing verdict as "the review has not arrived yet": wait for the agent's task notification, never exit on it. This is the mechanical guard behind step 2's resume instruction; the prose there alone would let the default path false-pass into `simo-dev:commit`.

Verdict present, no findings ≥ medium and no applicable `low` → **clean exit**, step 8. A `low` is **applicable** when it sits on a file in the diff, an iteration remains under `CAP` (judged against the current `CAP`), and it was **not already eligible in an earlier iteration**: same file and message as a `low` that was eligible before (an iteration remained under `CAP` then) → not applicable, whatever happened to it — applied, rejected, declined, deferred, ambiguous or escalated. This bounds each `low` to deferring the clean exit at most once. A `low` only listed because it first appeared on the last iteration stays eligible if step 7's escalation later raises `CAP`: the next review re-emits it and this rule lets it in. Else collect medium+ and applicable `low`s for step 5 — including when the applicable `low`s are the only findings (the clean exit is then deferred); note the other `low`s for the summary.

Applicable `low`s are applied; one is an In-scope improvement only on an all-`pre-existing` branch-point check (step 5; `docs/canonical-flow.md` "In-scope improvements"). On the last iteration a `low` is listed only, never applied: editing after a clean exit invalidates the review record (`lib/review_marker.py check` flags the file as changed after the last review) and costs a full extra loop. A `low` needing a user decision → ask; unattended → `deferred (needs decision)`, listed in the report.

### 5. Validity filter (code-review-protocol rigor)

For each medium+ finding and each collected `low`, before fixing: (1) technically correct for THIS codebase (file/line exists, problem real, fix matches the language semantics)? (2) would the fix break existing functionality (check callers/tests)? (3) is the current code deliberate (comments, commits, sibling code)? (4) does the reviewer have full context (it saw a diff, not the repo)?

- **Pass** → step 6. **Fail** → drop, log `rejected: [severity] file:line — message → reason`. **Ambiguous** → drop, surface as unresolved for the user.

No "you're absolutely right" / "great point" — just decide.

Then, before step 6 applies anything, run the branch-point check once on every passing `low`'s targets — its cited `file:line` plus every line its fix will replace or remove, or both sides of a pure insertion (`docs/canonical-flow.md` "In-scope improvements"):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "<BASE_SHA>" "<file1>:<line1>" "<file2>:<line2>" ...
```

Skip the call when no `low` passed. Keep the verdicts for step 6. rc ≠ 0, or a target with no output line → `error` for that `low`.

### 6. Auto-apply fixes

Apply passing fixes via Edit/Write; log each as `[severity] file:line — fix description`. A fix that turns out to need behavior changes not visible at filter time → abort that fix, log `escalated-during-fix: [severity] file:line — reason`, move to unresolved.

Right after applying each In-scope improvement (an applied `low` whose step-5 targets were all `pre-existing` and that does not complete the branch's own change, a dead-code deletion with its `rg -uu` evidence), append it to the Also-fixed ledger (`docs/canonical-flow.md` "In-scope improvements") — one single-quoted `file — what — why` argument per improvement, `file` relative to the repo root exactly as `git status --porcelain` prints it, a `'` inside a line written as `’`:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/also_fixed_ledger.py" add --repo "<worktree-abs>" '<file> — <what> — <why>' \
  || echo "LEDGER_FAILED rc=$? — the improvement is not recorded; fix the --repo literal and re-run"
```

A later iteration that reverts one runs the same block with `drop` in place of `add` — only for a line actually added to the ledger. Skipped on a protected branch (`simo-dev:commit`'s protected-branch detection): that path never reads or clears the ledger.

After applying every passing fix for the round, run the plan-drift check on the files just written:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/check_plan_drift.py" --repo "<worktree-abs>" --base "<BASE>" --changed-file <file1> <file2> ...
```

`<BASE>` = the `REVIEW BASE:` literal step 2 printed: a stacked parent's plan is not in `origin/<default>`, so the default base would flag it as live.

`<file1> <file2> ...` = every unique file path from this round's **applied-fix** log entries only (never the `escalated-during-fix:` entries this same step can also log — that file was never written, so it has nothing to check), in the same repo-root-relative form `git -C "<worktree-abs>" diff --name-only` prints (the same form a plan's `**Files:**` bullet declares) — the fix log's own `[severity] file:line` entries are observed to carry paths in that form in this plugin's existing diff-derived findings, just strip the trailing `:line`. Never a `lib/`-relative shorthand or an absolute path: `plans_mentioning()` does an exact string match against the plan's declared paths, so a differently-rooted path silently matches nothing (skip the call entirely when the round applied zero fixes — nothing changed to check). rc 0 → nothing to do. rc 1 → for each `<plan> mentions <file>` line printed, read that plan around the embedded target for `<file>` and, if the embedded code/text still shows the pre-fix version, resync it with a targeted `Edit` before continuing to step 7 — not a full plan-write regeneration, and never a reason to abort the round: a false positive (the plan cites the file but the reviewed passage is unaffected) costs a few seconds of reading, nothing to fix. rc 2 → note the error in the fix log and continue; a script failure never blocks the round.

### 7. Iterate

Apply step 1.5's escalation first: if this iteration produced a `high` or `critical` finding that survived the validity filter, `CAP` is now 5 — recompute the condition against that value, so the loop continues automatically rather than reaching step 8 and asking the user to iterate again.

An iteration whose only collected findings were applicable `low`s, none of them applied (whatever the reason), left the tree unchanged → **clean exit**, step 8 — or, when any is left unresolved, step 8's unresolved branch without its "iterate again" option: the next review would see the same tree, and none of those `low`s is applicable again.

Then increment N. N ≤ CAP → back to step 2. N > CAP with remaining medium+ → stop, step 8 with the unresolved list.

### 8. Present and hand off

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/diff_capture.py" "<worktree-abs>" --include-untracked -- HEAD
DIFF_CAPTURE_RC=$?
[ $DIFF_CAPTURE_RC -eq 0 ] || echo "WARNING: diff capture failed (rc $DIFF_CAPTURE_RC) for the report — show this failure in place of item (5)'s content, do not block the rest of step 8 on it."
```

Show, in order: (1) summary line (iterations, fixed by severity, unresolved); (2) fix log; (3) rejected findings with rationale; (4) unresolved findings by severity (critical always shown); (5) the script's output above (full diff if small, `--stat` + slices + temp-file path if large — same threshold, no special case for this report versus any other call site); this is the full working-tree-vs-HEAD diff — implementation plus any fixes applied during this loop combined (`review-loop` never commits, per this file's own Notes, so nothing here is committed separately) — not only the incremental per-iteration fixes in isolation; (6) `low` findings NOT applied (last iteration, deferred, rejected — decided at step 4, not here); (7) Also fixed — the lines this loop added to the ledger; (8) on its own labelled line, any `low` whose branch-point check was `error` (applied, not ledgered).

Then decide:
- Clean, ship-flow invocation (proactive trigger) → announce "Review loop clean → `simo-dev:commit`" and invoke `simo-dev:commit` directly (the merge remains the one hard gate downstream), passing `--target <branch>` when review-loop itself received one, so commit does not re-detect a different base. Three cases stop at the report instead:
  - standalone review request (the user asked for a review only, e.g. "do a review") — report and stop;
  - headless / non-interactive run (an unattended headless caller, `claude -p`) — apply fixes only; commit/push/MR belong to the caller;
  - invoked as `simo-dev:commit` step-1 pre-flight — return to the calling commit flow, never re-invoke it (recursion).
- Unresolved, N ≤ CAP → "(1) skip → `simo-dev:commit`, (2) fix manually, (3) iterate again."
- Unresolved, N > CAP (cap reached) → "(1) skip → `simo-dev:commit`, (2) fix manually." No further iteration.

Whichever of the three branches above applies, including the "invoked as `simo-dev:commit` step-1 pre-flight" case — `simo-dev:commit` never posts this comment on its own (`grep -n post_tracker_comment` on `commit/SKILL.md` returns nothing), so skipping it there would silently drop the gate output for that path — before executing the branch, post an "Implementation summary" comment. Reuse the same `KEY`/`R`/`W` derivation as step 0's "Tests performed" comment (re-derive rather than trust a variable carried from step 0 — the Bash cwd does not persist between calls):

```bash
W="<worktree-abs>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
BRANCH=$(git -C "$W" branch --show-current)
KEY=$(python3 "$R/lib/derive_ticket_key.py" "$BRANCH" --cwd "$W")
```

Body = the summary line (iterations, fixed by severity, unresolved) + fix log + rejected-findings-with-rationale + unresolved-findings-by-severity — never the aggregated diff (too large and noisy for a ticket comment). Write it to a temp file, then — rebind `R` and substitute the `KEY` derived above as a literal, not `$KEY`: shell state, including variables, does not survive between Bash calls, so a bare `$KEY` here is empty and silently posts nothing:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/post_tracker_comment.py" "<KEY-or-empty>" "<tmpfile>"
```

Announce `Implementation summary: <script's stdout line>`. Post once per loop invocation, at this final step — never per iteration (N can reach `CAP`=5; only the final state matters to the ticket).

## Notes

- Never commits or pushes — hand-off is `simo-dev:commit`.
- The 5-iteration ceiling is non-negotiable — nothing may exceed it. Step 1.5 only lets the invoker pass a lower one, and any `high`/`critical` finding that survives the validity filter restores the full 5.
- Modifies only files already in the diff; new files only if a finding requires one (e.g. missing test).
