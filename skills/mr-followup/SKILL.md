---
name: mr-followup
description: Use after a push to check pipeline status and code review comments on the current GitLab MR or GitHub PR. Auto-fixes medium+ findings (plus `low` on MR-diff files, per canonical-flow "In-scope improvements"), up to `CAP` rounds (5; pass `cap=2` when the diff is prose only — no code and no shell/python inside the prose). With argument `merge` (a fresh session started from its merge card): merge gate + post-merge only. Fifth step of the canonical task flow.
---

# Check MR / PR Review

Fifth and final step of the canonical task flow:
`simo-dev:task-intake → simo-dev:plan-execute → simo-dev:review-loop → simo-dev:commit → simo-dev:mr-followup`

Checks the MR/PR for pipeline status and review comments, auto-fixes medium+ findings (plus `low` on MR-diff files per In-scope improvements), commits them (no gate — unconditional) and auto-pushes. Works on GitLab (including self-hosted) and `github.com`. A local `simo-dev:code-reviewer` subagent reviews the branch diff every round (step 6), except on round 1 when this session's `simo-dev:review-loop` record already covers it.

Iteration cap: `CAP` — 5, or a lower value passed by the invoker (step 1.5). Every round but the last detects + fixes + commits + auto-pushes; the last one surfaces what is left without committing. At `CAP` = 2 that means round 1 fixes and round 2 surfaces.

No Also-fixed ledger work inside a run (canonical-flow "In-scope improvements", Transparency): this skill never reads the ledger. During a run the only edits are step 7's own (round fixes, item 3 plan-drift resyncs, clean-exit harvest), and nothing the run does is a review-loop trigger: its edits, test runs, commits and pushes, and the user's answers to its own questions. A user-requested ad-hoc edit, or a `simo-dev:review-loop` / `simo-dev:plan-execute` run, mid-run → read `lib/rare-stops.md` "Round-boundary stop". Entry check, on every invocation (a `<ci-monitor-event>` re-entry included), before step 1: a pending ledger line whose file is still dirty (the same per-file test as `simo-dev:commit` step 3) is uncommitted work a round commit would carry while its line stays pending, later dropped as stale — stop and report; never invoke `simo-dev:commit` from here, the work may be half done: whoever made it commits it through `simo-dev:commit` once finished, which re-enters this skill. A pending line whose file is clean is stale; `simo-dev:commit` drops it, ignore it here. `LEDGER_FAILED` → warn and continue (the check could not run).

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
P=$(python3 "$R/lib/also_fixed_ledger.py" pending --repo "<worktree-abs>") || echo "LEDGER_FAILED rc=$?"
S=$(git -C "<worktree-abs>" status --porcelain --untracked-files=all | cut -c4- | sed 's/^[^"]* -> //; s/^"[^"]*" -> //')
while IFS= read -r l; do
  [ -n "$l" ] && printf '%s\n' "$S" | grep -qxF -- "${l%% — *}" \
    && { echo "STOP: pending ledger work on ${l%% — *} — commit it through simo-dev:commit first"; exit 1; }
done <<< "$P"
```

## Severity model

Per `~/.claude/local-marketplace/simo-dev/docs/canonical-flow.md` "Code review severity" — `low` on a file in the MR diff applied per "In-scope improvements" (same file; step 7), otherwise informational; `medium`/`high`/`critical` auto-fixed (see step 7).

## Steps

### 1. Detect forge + target

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/mr_followup.py" detect --repo "<worktree-abs>"
```

`<worktree-abs>` is the absolute worktree path. Read `<forge>`, `<slug>`, `<api-host>`, `<branch>`, `<base>`, `<mr-iid>` (`iid:`, GitLab) or `<pr-num>` (`pr:`, GitHub) and `<mr-url>` (`url:`) off its lines, and substitute them, with `<worktree-abs>`, as literals in every later block — never as shell variables or an empty `git -C ""`: shell state dies between Bash calls (CLAUDE.md "Worktree cwd gotcha"). A forge call this body issues itself runs as `cd "<worktree-abs>" && glab|gh …`, never with `-R` (a bare `owner/repo` pins the path, not the host); the scripts take `<worktree-abs>` as an argument. What the script decides (view fallback, state authority, CodeReview transition, stacked-parent check): its docstring.

- rc `0` → continue. `draft: yes` → proceed, flag it (step 7.4 item 7). `warning:` lines only warn — step 7 item 6's push guard is what stops. `stack: parent-merged` → read `lib/parent-merged.md` and follow it, never unattended.
- rc `4` → no MR/PR yet: print its `STOP:` line (it names the create command) and exit 0.
- rc `3` → `ASK:` — ask the user. Several open PRs share the head (`candidate:` lines): re-run the block with `--number <the picked PR number>` appended, which prints the fields the ASK withheld. The CLI resolved an MR/PR of another source branch: only one whose source branch is `<branch>` can be followed from here (`--number` refuses any other).
- rc `1` → stop and report the `STOP:` line: nothing was established.
- rc `2` → `R` unbound or a placeholder left (`can't open file '/lib/…'`, an argparse error): re-issue the block with the literals.

### 1.5. Round cap

`CAP` = **5** unless the invoker passed a lower value (`cap=<n>`). 2 is the floor: a passed 1 or 0 is clamped up to 2 (below it no fix round runs at all), never rejected into the default of 5, which would cost more than asked.

Pass `cap=2` when the diff is prose and nothing else: no code, and no changed line inside a fenced `bash`/`sh`/`python` block, in any file. Never infer it from file extensions — the bodies this flow edits most are dense with runnable blocks. The invoker judges; it has the diff.

A `high` or `critical` finding that survives the validity filter raises `CAP` back to 5 for the rest of the rounds, so a cap passed in error cannot weaken the review. Evaluate that at step 7, before the cap-reached branch. Announce the value in one line so the report is auditable.

### 2. Launch pipeline poll (background, non-blocking)

Launch the poll with `run_in_background: true` — tell the user it's running; do NOT sleep or poll manually, and do NOT wait for it here: continue straight to step 5. Its result is collected at step 7, the flow's one sync point for it. GitLab: `lib/pipeline_poll_gitlab.py "<worktree-abs>" "<branch>"`; GitHub: `lib/pipeline_poll_github.py "<pr-num>" "<worktree-abs>"`. Both are bounded (`POLL_NOPIPE_MAX_WAIT` 300s while nothing for HEAD is visible, `POLL_MAX_WAIT` 1800s once it is) and report only on the worktree HEAD's own pipeline/checks.

> Launch commands + env var/exit-code contract: `lib/pipeline-scripts.md` — load when launching the poll.

> Steps 3-4 retired with the CI AI-review experiment. Numbering preserved — grep `step 8` across the plugin before renumbering anything.

### 5. Fetch review comments

Both helpers print `## <author> — <path> line <N>\n<body>\n`, or `No open review comments found.` when the forge really has zero open comments. Capture as `REMOTE_COMMENTS`, and capture the rc **and print it** in the same call: a block ending in an assignment exits 0 with empty output, which is how a failed fetch once passed as zero comments.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
REMOTE_COMMENTS=$(python3 "$R/skills/mr-followup/lib/fetch_discussions.py" "<slug>" "<mr-iid>" "<api-host>")   # GitLab
FETCH_RC=$?; printf 'rc=%s\n%s\n' "$FETCH_RC" "$REMOTE_COMMENTS"
```

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
REMOTE_COMMENTS=$(python3 "$R/skills/mr-followup/lib/fetch_pr_threads.py" "<slug>" "<pr-num>")    # GitHub
FETCH_RC=$?; printf 'rc=%s\n%s\n' "$FETCH_RC" "$REMOTE_COMMENTS"
```

Read `rc=` from the first output line; stderr is unredirected, so the helper's own message tells the causes apart. **Any non-zero `FETCH_RC` is a STOP for the whole followup — never read it as zero comments:**

- `0` → the comments, or the zero-comments sentinel. Continue to step 6.
- `2` → fail-closed: the fetch did not complete (bad/missing token, network down, `gh` missing, forge error); stdout carries a `REVIEW COMMENTS UNAVAILABLE` line — on GitHub *appended* after comments already printed, so match it anywhere in the capture. Report: "Could not fetch review comments for '<branch>' — the forge was not reached, so 'no comments' is unknown, not confirmed. Fix the token/network and re-invoke; the fetch is idempotent."
- any other → same STOP, and stdout may be **empty** (a bad argument, an uncaught error): report the rc and the helper's stderr verbatim rather than guessing a cause.

GitLab token: `GITLAB_TOKEN` or macOS keychain (`gitlab-api-token`); one-time setup with ACL recipe in `lib/keychain-setup.md`. GitHub: `gh` handles auth.

### 6. Local code review

First the guards, every round:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/mr_followup.py" review-gate --repo "<worktree-abs>" --base "<base>"
```

**Round-1 skip — review-loop already covered the diff.** On round 1 only, append `--round1 --session "$CLAUDE_CODE_SESSION_ID"` when `simo-dev:review-loop`'s step 8 output is in context with an empty unresolved list (an unresolved entry must reach a reviewer as "still open", see the carry below; output no longer in context counts as unknown) and step 1's `stack: parent-merged` did not end on a "no" to the rebase (the diff then carries a parent's commits review-loop never saw). Never from round 2 on.

- rc `1` with `ahead:` lines → the local `<base>` is ahead of `origin/<base>` and those commits are in this branch's ancestry (the guard of `simo-dev:commit` step 7, `lib/mr_create.py prepare`). No bypass: ask the user before pushing the local base (a protected-branch push), never push it unasked; once pushed, re-run the block (it re-fetches first). Any other `STOP:` → stop and report it. rc `2` → re-issue with the literals.
- `local_review: skip (covered by review-loop)` → no dispatch and no diff capture this round, `LOCAL_REVIEW` empty; step 7 still collects the pipeline and reads step 5's comments.
- `local_review: dispatch …` → the dispatch block below.

The skip needs this session's review-loop record (written by `hooks/review_record.py` when its reviewer concluded, keyed on work tree + `CLAUDE_CODE_SESSION_ID`) and no branch file touched since: a run in another session, a re-entry after a fix round, or a rebase or base merge that rewrites a branch file dispatches. A committed plugin version bump dispatches too (`plugin.json` reads as changed after the review) — one reason a repo's release commit goes after this skill's clean exit (step 7.4 item 7), not in `simo-dev:commit`.

Dispatch block — every round that dispatches:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
DIFF=$(python3 "$R/lib/diff_capture.py" "<worktree-abs>" -- "origin/<base>...HEAD")
DIFF_RC=$?
printf 'rc=%s\n%s\n' "$DIFF_RC" "$DIFF"
[ $DIFF_RC -eq 0 ] || { echo "STOP: diff capture failed (rc $DIFF_RC) — do not dispatch the reviewer on a partial/empty diff."; exit 1; }
CODE_REVIEW_MODEL=$(python3 "$R/lib/model_strategy.py" get code_review)
```

Agent prompt: review the diff, one finding per line as `[severity] file:line — message` (low|medium|high|critical), real problems only. Output → `LOCAL_REVIEW`. Pass `model: $CODE_REVIEW_MODEL` when non-empty (omitted when empty: the caller's tier). Forward `$DIFF` unmodified — it already passed `lib/diff_capture.py`'s size gate (~50k chars, same number everywhere in this plugin); a trailing `Full diff written to: <path>` line names the full diff, readable with `Read` when needed. This dispatch deliberately does **not** carry the `REPO:` marker line `simo-dev:code-review-protocol` requires: it reviews `origin/<base>...HEAD` only, while the record that marker writes certifies the working tree and untracked files too. Do not "fix" the missing marker.

**Carry the prior rounds — from round 2, and on round 1 whenever `simo-dev:review-loop` already ran on this diff** (its step 8 printed the fix log, the rejected findings with rationale and the unresolved list — skip the round-1 carry when that output is no longer in context; a round 1 that ignores them re-derives up to five settled iterations). The reviewer is stateless; open the prompt with "The following were already evaluated, each with the reason given. Do NOT re-flag them unless you can refute the rationale with new evidence.", then three **cumulative** lists — every round's entries, carried forward on every later round, never just the last one's: **dropped by the validity filter** (step 7 item 2's `rejected:` lines, or `review-loop` step 5's on the round-1 path, with their reasons — `low`s rejected, declined by the user or `deferred (needs decision)` travel here too, with that reason); **confirmed correct** — code an earlier round examined and the caller judged right, with the reason, from the caller's own reading since no reviewer output carries it; **verified by the caller** — what was asserted and the exact command, instructing the reviewer to attack those checks (does the command discriminate? would it fail if the behavior broke?), never to repeat them. Anything left **unresolved** goes in too — step 7 item 2's **ambiguous** drops, findings without `file:line`, and on the round-1 path `review-loop`'s own unresolved entries — under their own label, "still open — re-raise if the code is still wrong", and **outside** the clause, same as `review-loop` step 2. A skipped round 1 dispatches nothing, so its carry moves to round 2: `review-loop`'s lists enter there as the round-1 entries, plus round 1's own item-2 lines on remote comments.

### 7. Iteration loop (cap = CAP, step 1.5)

**Collect the pipeline poll launched at step 2** — the flow's one sync point for it: if its task notification hasn't arrived yet, wait for it now (never proceed on an assumed result). Report its status whichever branch below the round takes — folded into the round-N summary on the fix branch, beside the report on the others: a clean review with zero comments still has something to say if the pipeline failed. On both forges the poll's **exit code is the verdict**: `0` concluded (report the status word or the failed count; a `failed` pipeline is rc `0` too); `3` no pipeline/checks for HEAD — one line, not a failure, but check the push landed first; `4` a ceiling came due with no conclusion; `5` the read that hit the ceiling failed — unverified, **never** green; `1` the poll refused to start — fix the precondition its last line names and relaunch here, synchronously (steps 5-6 already ran this round). What each last line means: `lib/pipeline-scripts.md` "Exit codes". The loop re-enters step 2 after every push (item 6), so each round collects the poll its own step 2 launched for its own pushed commit.

Build the round-N body: concatenate `REMOTE_COMMENTS` + `LOCAL_REVIEW` (header-separated) when both have content; use the non-empty one otherwise; both empty (a local review legitimately returns no findings) → "Nothing to review": report via step 7.4, stop. Exception: a round-1 skip (step 6) with no remote comments is a **clean exit** below, not "Nothing to review" — the harvest catch-up still runs.

Parse findings: regex `^\[(low|medium|high|critical)\]\s+([^:]+):(\d+)\s+—\s+(.+)$`. Group by severity; findings without `file:line` → unresolved.

**Decision per round:**

**Applicable `low`**: a `low` on a file in the MR diff while round N < CAP, and **not already eligible in an earlier round, or in the `review-loop` run carried into round 1 (round 2 after a round-1 skip)** (same file and message) — such a `low` never re-enters the fix branch, whatever happened to it. Applicable `low`s are treated like medium below; one is an In-scope improvement only on an all-`pre-existing` branch-point check (item 2 below; `docs/canonical-flow.md` "In-scope improvements"), never when it came from a remote comment; on round CAP they are listed only. One needing a user decision → ask; unattended → `deferred (needs decision)`.

- No findings ≥ medium and no applicable `low` → **clean exit** (a round whose only findings are non-applicable `low`s is a clean exit, never a no-op commit). First the harvest catch-up: the three-question review of `skills/commit/lib/knowledge-harvest.md`, scoped to what emerged since `commit` step 0.7's pass, the review-loop `commit` step 1 may have run included (session-memory dedup per that file's framing, not a full re-review). Nothing written in this repo (nothing new, or only facts routed out of it — one-line ask, then `start_session` on a yes) → the report below. Something written in this repo and round N < CAP → treat the edits as this round's fixes: items 3, 5 and 6, then step 2 for round N+1, whose local review covers them; it counts against `CAP`, and the next clean exit's catch-up skips what this one captured, so the loop ends. The `review_loop_warn` reminder on that commit is expected. Round N == CAP → do not write: list the would-be edits in the final report. Then report via step 7.4 (collected pipeline status, `low` findings not applied) and continue to step 7.5. Never edit after the clean exit: no round reviews it, and it costs another commit + push.
- Round N < CAP with medium+ findings or applicable `low`s →
  1. Show a summary (count by severity, top 5 messages).
  2. **Validity filter** — `simo-dev:code-review-protocol` rigor per medium+ finding and applicable `low` BEFORE fixing (remote comments come from reviewers without this session's context): (a) technically correct for THIS codebase? (b) fix breaks existing functionality? (c) deliberate design? (d) reviewer had full context? Fails → drop, log `rejected: [severity] file:line — message → reason`. Ambiguous → unresolved.

     Then, before item 3 applies anything, run the branch-point check (`docs/canonical-flow.md` "In-scope improvements") once on every passing `low` from `LOCAL_REVIEW` — targets: its cited `file:line` plus every line its fix will replace or remove, or both sides of a pure insertion. A `low` from `REMOTE_COMMENTS` is never an In-scope improvement and gets no check. `LOCAL_REVIEW` lines are HEAD coordinates while the script reads the working tree, so uncommitted tracked edits skip the script and make every target `error`:

     ```bash
     R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
     git -C "<worktree-abs>" diff --quiet HEAD || { echo "uncommitted tracked edits: every target is error"; exit 0; }
     python3 "$R/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "$(git -C "<worktree-abs>" merge-base HEAD "origin/<base>")" "<file1>:<line1>" "<file2>:<line2>" ...
     ```

     `origin/<base>` is fresh from step 6's fetch. Skip the block when no `LOCAL_REVIEW` `low` passed. rc ≠ 0, or a target with no output line → `error` for that `low`.
  3. Auto-apply passing fixes via `Edit`/`Write`. Track each fix. Then, before item 5's commit, run the plan-drift check on every file just fixed (skip it when the round applied zero fixes):

     ```bash
     R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
     python3 "$R/lib/check_plan_drift.py" --repo "<worktree-abs>" --base "<base>" --changed-file <file1> <file2> ...
     ```

     Never omit `--base "<base>"`: without it the script resolves `origin/HEAD`, which misjudges which plans are already merged whenever the MR targets another branch. Paths in the repo-root-relative form `git diff --name-only` prints (the findings' paths minus `:line`): the match is exact, so an absolute or `lib/`-relative path silently matches nothing. rc 0 → nothing to do. rc 1 → for each `<plan> mentions <file>` line, read that plan around the embedded target for `<file>` and, if it still shows the pre-fix version, resync it with a targeted `Edit` — before item 5's commit, which is unconditional and immediate. rc 2 → note the error and continue; a script failure never blocks the round.
  4. Show the applied fixes as `git -C "<worktree-abs>" diff --stat` + one line per fix (full diff only if the user asks), the rejected list, branch + remote, the proposed commit message, and on its own labelled line any `low` whose branch-point check was `error` (applied, not listed as an In-scope improvement).
  5. **No gate** — zero fixes applied this round → no commit, no push: every finding rejected, declined by the user or `deferred (needs decision)` → take the clean-exit branch above; any **ambiguous** one → not clean, report via step 7.4 (unresolved list, collected pipeline status) and stop, as the cap-reached branch does. Otherwise commit unconditionally: NEW commit (no amend), conventional message scoped to the ticket. Its body lists the In-scope improvements this round applied — `low`s whose item-2 branch-point check was all `pre-existing` and that do not complete the branch's own change, and dead-code deletions — one line each (file — what — why) — the MR body is not rewritten.
  6. **Pre-push MR-state guard + push** — the MR/PR must still be open (incident 2026-07-17: a push after a mid-session merge recreates the deleted branch as an orphan — `git push` prints `[new branch]`, easy to miss):

     ```bash
     R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
     python3 "$R/lib/mr_followup.py" push --repo "<worktree-abs>" --forge "<forge>" --iteration <N+1>
     ```

     The script decides, so never re-issue the push from a separate call; force-push stays forbidden, merge never auto. rc `0` (`pushed:`) → back to step 2 for round N+1. rc `4` → the push was rejected: fetch, rebase, retry the round. Every other code ends the loop with its own `STOP:` line, the fix commit local: rc `1` the MR/PR is merged or closed (cherry-pick onto a fresh branch); rc `3` the guard answered "could not verify" (bad token, network, transient forge error) — re-run once it can verify again; rc `2` the guard never ran or never answered — unattended, park rather than retry, except when the evidence is the caller's own substitution: a `can't open file '/lib/…'` (`R` unbound) or an argparse usage line on stderr → re-issue the block once with the literals in place, then park.

  **Thread reply policy (parallel to item 5)** (GitHub only) — for findings from inline comments: reply in-thread (`cd "<worktree-abs>" && gh api .../comments/{id}/replies`) "Fixed in <sha>." after the commit, or "Not applied — <rationale>." at filter-reject time. Never "thanks"/"great point" — technical acknowledgment only. GitLab: resolve via `glab` if straightforward, else leave to the user.
- Round N == CAP with medium+ findings → **cap reached**: read `lib/rare-stops.md` "Cap reached" (step 1.5's escalation runs first, so a surviving `high`/`critical` keeps the loop going).

### 7.4. Final report

First record how the run stopped (`<answer>` = `clean`, `cap`, `ambiguous`, `nothing` or `round-boundary`):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" event --type gate --cwd "<worktree-abs>" --field skill=simo-dev:mr-followup --field gate=followup_exit --field answer=<answer>
```

Ends every pre-merge stop that reports: the round-boundary stop, step 7's "Nothing to review", clean exit, item 5's ambiguous stop, cap reached. Every other STOP (e.g. the entry ledger check, step 1's `STOP:`/`ASK:` lines, step 5's fetch, step 6's base-ahead or diff capture, item 6's guard) keeps its own message.

Before printing the report, on every stop it ends — skipped when step 1 printed `open: no` (notes for a merged or closed MR would mislead the release consumer) — run the **Supersede check** of `skills/commit/lib/verification-notes.md` (path relative to the plugin root; load on demand) with its **Inputs**: the step-1 `<mr-url>`; the MR head SHA read at that moment (`cd "<worktree-abs>" && glab mr view "<branch>" -F json | python3 "$R/lib/json_field.py" - sha` / `cd "<worktree-abs>" && gh pr view "<branch>" --json headRefOid -q .headRefOid` — right after a push the forge SHA can lag a few seconds, and the spec's "differs from local HEAD" rule keeps content and SHA on the same commit); the `derive_ticket_key.py` key step 8 uses; a fresh `origin/<base>...HEAD` diff (not step 6's capture, which predates a round's fix commit). It covers commits from any source — this run's fix rounds, or a follow-up `simo-dev:commit` through the existing-MR path. List its announce line under Verifications only when it posted.

Print it as normal chat text, before any call that ends the turn (e.g. a host's wrap-up or next-step tool: its recap is short and nothing prints after it). Never only inside that call. In this order:

1. **MR/PR link** — `[!<mr-iid>](<mr-url>)` / `[#<pr-num>](<mr-url>)`. Empty `<mr-url>` → `!<mr-iid>` / `#<pr-num>` on `<slug>` plus "link unavailable", never an empty link.
2. **Change** — 1-3 lines.
3. **Verifications** — this run's local review (a skipped round 1 reads `local review: covered by review-loop`), plus any verify-done or test runs of earlier flow steps still in session context, each with its outcome. Never invent an entry. Pipeline goes under 5.
4. **Review summary** — `simo-dev:review-loop` iterations carried into round 1, or round 2 after a round-1 skip (when in context), rounds run here, findings applied vs rejected (counts), the unresolved list on an ambiguous stop or cap reached.
5. **Pipeline** — the status collected at step 7. On a round-boundary stop after that round pushed: it is the pre-fix commit's, the new push is not polled. Stop before step 7: report the poll's status if its notification has arrived, else "not collected (poll still running)".
6. **Skipped steps** — flow steps skipped (e.g. review-loop already covered; the round-1 local review when step 6 skipped it, as "round-1 local review — covered by review-loop"), or "none". Step 7.5 is not stated here.
7. **Next step** — the merge is the user's; on an ambiguous stop, cap reached or a round-boundary stop, what needs a decision or a commit first. On a clean exit in a repo whose own rules require a release or version-bump commit in the MR, that commit is made now, once, before the merge — never earlier in `simo-dev:commit`, where any fix round here would force a second release. It is machine-written: after pushing it, start no new round for it (the push reminder does not apply); only check its pipeline, if the repo has one. When step 1 printed `draft: yes`, say the forge refuses to merge a Draft, so it must be marked ready first — `cd "<worktree-abs>" && glab mr update <branch> --ready` / `cd "<worktree-abs>" && gh pr ready <branch>` — before the user's merge.

Also include what the exit itself reports: `low` findings not applied, would-be harvest edits at `CAP`, the unresolved list.

### 7.5. Pre-merge QA verify gate (optional)

Runs after step 7 exits clean, before the merge (human gate, outside this skill — unchanged). Active only in repos with a release skill that can deploy an **arbitrary branch** to QA (verify arbitrary-branch support first); not detected → skip silently, zero behavior change elsewhere.

Runs after step 7.4's report. When it asked the user, end with one chat line, before any turn-closing call: `QA gate: declined` / `go` / `no-go`. A silent skip prints nothing.

> Full procedure (capability list, heuristic patterns, multi-track deploy gotcha, QA-vs-PROD scheduler gotcha): `lib/pre-merge-qa-gate.md` — load when the gate triggers.

### 7.6. Merge brief + fresh-session card

Runs last on a **clean exit** with the MR/PR open, after step 7.4's report, any release commit and step 7.5. Skipped on every other stop, after a QA gate `no-go`, and in a Merge entry run. The merge often waits more than an hour, and the prompt cache dies after one: answering `merge` here then rewrites this whole context. The merge and steps 8-8.7 need only literals, so a fresh session can do them.

The brief's `head` is the MR head **now**, after any release push (step 7.4's view predates it): read it with the view call of step 7.4's Supersede check (`... glab mr view "<branch>" -F json | python3 "$R/lib/json_field.py" - sha` / `... gh pr view "<branch>" --json headRefOid -q .headRefOid`) and compare it with `git -C "<worktree-abs>" rev-parse HEAD`. Different → the forge lags a push: re-read once after a few seconds; still different → no brief and no card (a stale head would stop the Merge entry), say so in one line.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/merge_brief.py" --repo "<worktree-abs>" --mr "<mr-url>" --branch "<branch>" --base "<base>" --head "<MR head, read as above>"   # + --qa-gate <YYYY-MM-DD> when step 7.5 passed
```

Print the brief as chat text in a fenced block, then one line: `Merge here: reply merge. Cheaper after a long wait: start the merge card (a new session).` rc `2` → print its stderr line, no card; `merge` here still works.

Then, unless under `simo-dev:afk` (it starts no sessions; its park recap follows this report and the brief stays above for a manual paste): `mcp__ccd_session__start_session` not yet callable → `ToolSearch` `select:mcp__ccd_session__start_session` once; still not callable → no card. Callable → `initiation` `own_initiative`, `context` `fresh`, `use_worktree` `true` when this session runs inside a worktree (a desktop-app tree), else `false` (a child in the parent's tree cannot detach; the Merge entry reaches the task tree through the brief's literals), `title` `Merge <ticket key, else branch>`, `background` one paragraph (≥80 chars: MR, clean follow-up, only the merge and post-merge steps left), `prompt` = the spawned-task marker line (session language, same rule as `simo-dev:task-intake`'s `lib/handoff-spawn.md`), a blank line, the brief verbatim (its `task id:` line lets this skill's Skill hook join the task in the new session), a blank line, and `Invoke simo-dev:mr-followup with argument merge, using the brief above. Its own gate asks before merging; nothing else runs first.` The card starts nothing by itself. Keep its `task_id`: when the user answers `merge` here instead, call `mcp__ccd_session__dismiss_task` with it before merging, so the card cannot run post-merge a second time. Dismiss refused (the card was already started) → do not merge here: point the user to that session.

### Merge entry (argument `merge`)

A fresh session started from a step 7.6 card, invoked with argument `merge`: read `lib/merge-entry.md` and follow it; skip the ledger entry check and steps 1.5-7.6 (the parked session already ran them). Its merge gate is this skill's own question, so the user's answer to it is no review-loop trigger.

### 8. Post-merge transition + verification notes + worktree cleanup

Runs only after the user has explicitly merged: a human gate outside this skill, or the Merge entry's own merge gate after the user's `y`. Then read `lib/post-merge.md` and run it in order: the ticket transition (step 8), the verification-notes comment, the worktree cleanup (pre-approved exception — runs ONLY after the user-approved merge), the journal (8.5), the next sub-task / parent close (8.6) and the release hook (8.7).

## References

- GitLab keychain one-time setup: `lib/keychain-setup.md`.
- PostToolUse hook to auto-trigger this skill after `git push`: `lib/hook-setup.md`.
- GitLab vs GitHub quick-reference: `lib/quick-reference.md`.
- Pre-merge QA verify gate detail (step 7.5): `lib/pre-merge-qa-gate.md`.
- Round-boundary stop, cap reached: `lib/rare-stops.md`.
