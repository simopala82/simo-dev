---
name: mr-followup
description: Use after a push to check pipeline status and code review comments on the current GitLab MR or GitHub PR. Auto-fixes medium+ findings (plus `low` on MR-diff files, per canonical-flow "In-scope improvements"), up to `CAP` rounds (5; pass `cap=2` when the diff is prose only — no code and no shell/python inside the prose). Fifth step of the canonical task flow.
---

# Check MR / PR Review

Fifth and final step of the canonical task flow:
`simo-dev:task-intake → simo-dev:plan-execute → simo-dev:review-loop → simo-dev:commit → simo-dev:mr-followup`

Checks the MR/PR for pipeline status and review comments, auto-fixes medium+ findings (plus `low` on MR-diff files per In-scope improvements), commits them (no gate — unconditional) and auto-pushes. Works on GitLab (including self-hosted) and `github.com`. A local `simo-dev:code-reviewer` subagent always reviews the branch diff (step 6).

Iteration cap: `CAP` — 5, or a lower value passed by the invoker (step 1.5). Every round but the last detects + fixes + commits + auto-pushes; the last one surfaces what is left without committing. At `CAP` = 2 that means round 1 fixes and round 2 surfaces.

No Also-fixed ledger work inside a run (canonical-flow "In-scope improvements", Transparency): this skill never reads the ledger. During a run the only edits are step 7's own (round fixes, item 3 plan-drift resyncs, clean-exit harvest), and nothing the run does is a review-loop trigger: its edits, test runs, commits and pushes, and the user's answers to its own questions. A user-requested ad-hoc edit, or a `simo-dev:review-loop` / `simo-dev:plan-execute` run, waits for a round boundary (finish the current round through its commit and push, or stop before applying anything); then report and stop, overriding item 6's loop-back to step 2, and the work is committed through `simo-dev:commit` without `--no-mr`, which re-enters this skill. Entry check, on every invocation (a `<ci-monitor-event>` re-entry included), before step 1: a pending ledger line whose file is still dirty (the same per-file test as `simo-dev:commit` step 3) is uncommitted work a round commit would carry while its line stays pending, later dropped as stale — stop and report; never invoke `simo-dev:commit` from here, the work may be half done: whoever made it commits it through `simo-dev:commit` once finished, which re-enters this skill. A pending line whose file is clean is stale; `simo-dev:commit` drops it, ignore it here. `LEDGER_FAILED` → warn and continue (the check could not run).

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
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
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
DETECT_OUT=$(python3 "$R/skills/mr-followup/lib/detect_remote.py" "<worktree-abs>") || exit 1
# DETECT_OUT is `FORGE=gitlab|github  SLUG=owner/repo (or group/.../repo)  API_HOST=resolved hostname`
BRANCH=$(git -C "<worktree-abs>" branch --show-current)
printf '%s  BRANCH=%s\n' "$DETECT_OUT" "$BRANCH"
[ -n "$BRANCH" ] || { echo "empty BRANCH (detached HEAD, or the git call above failed) — refusing to continue"; exit 1; }
```

Shell state does not survive between separate Bash calls (CLAUDE.md "Worktree cwd gotcha"). `<worktree-abs>` is the absolute repo/worktree path — `detect_remote.py` and the `git branch` above now require it explicitly rather than relying on an ambient cwd, which does not necessarily match. `glab`/`gh` resolve their project from the cwd too, and **`-R` is not a substitute** — a bare `owner/repo` pins the path but not the host, so it resolves against `glab config get host` (measured 2026-08-15: `glab mr list -R <user>/<repo>` gives 404 from `/tmp`, the real list from the worktree, host `gitlab.com` vs origin `<your-gitlab-host>`); on `glab mr create` it does not even reach the source project (`simo-dev:commit` step 9). So the `glab`/`gh` calls below are scoped with `cd "<worktree-abs>" &&` instead, while the `git` ones take `-C "<worktree-abs>"` — `pipeline_poll_gitlab.py` (`lib/pipeline-scripts.md`) takes `<worktree-abs>` as an argument and scopes its own `glab` call internally instead (`pipeline_poll_github.py` takes it too and runs its `gh` calls there) — with the two side effects `simo-dev:commit` step 9 lists for the `cd` form: the Bash cwd stays there for later calls, and a `cd` inside a compound command can raise a permission prompt on the headless paths that drive this skill (`simo-dev:afk`, an unattended headless caller). Assume nothing about the rest — observed still unscoped: the `gh api .../comments/{id}/replies` of step 7's thread-reply policy. (`check_mr_state.py` was one of these; it now takes a required `--cwd` and applies it to both forges.) Scope any forge call you add. `<branch>`, `<forge>`, `<slug>`, `<api-host>` are not derivable from the block alone — the `DETECT_OUT=$(...)` assignment and the `git branch` assignment print nothing themselves, so read them off the `FORGE=...  SLUG=...  API_HOST=...  BRANCH=...` line the block's own `printf` prints. That line always prints, even with `BRANCH=` empty — the guard right after it is what stops the block from returning control on an empty branch, so no later step ever receives an empty literal. `<base>` and `<mr-iid>`/`<pr-num>` come from the GitLab/GitHub block right below. Substitute all of them — and `<worktree-abs>` itself — as literals in every later block, never as shell variables (`$BRANCH`/`$BASE`/`$FORGE`/`$SLUG`/`$API_HOST`, or an empty `git -C ""`).

**GitLab (`FORGE=gitlab`):**

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
cd "<worktree-abs>" && glab mr view "<branch>" -F json | python3 "$R/skills/mr-followup/lib/format_mr_view.py"
```

Extract the `<base>` / `<mr-iid>` literals from `BASE` / `IID`. `SOURCE != <branch>` → glab resolved a different MR, ask. Draft → proceed, flag it. `STATE` is informational; the check below is the authority.

Non-zero exit → no JSON on stdout, so `<base>` / `<mr-iid>` stay unset while steps 5-6 need them. It does not prove there is no MR: `format_mr_view.py` exits non-zero on empty or invalid stdin, and an expired token, a network error or a wrong cwd all land on the same non-zero exit — stderr is deliberately unredirected because glab's own message is what tells them apart. Run the state-check block below — **whole, never a line of it on its own**: it binds `$G` before it uses it, so a lone capture line runs `python3 ""`. `<mr-iid>` is still unbound here — leave it unsubstituted, do not guess one. Then branch on what it printed: `no MR found` → print "No MR for branch `<branch>` on `<slug>`. To open one: `glab mr create`, then re-invoke this skill." and exit 0; any `NOT verified` warning → stop, nothing was established; a state line, or silence (rc `0`, the MR is open) → the first call failed transiently and this one reached the MR, so re-run the block above once to fill the fields, then stop if it fails again.

State-check (the block only warns and never exits — intake step, not a push; the fallback path above still stops on a `NOT verified`):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
G="$R/lib/check_mr_state.py"
if [ ! -f "$G" ]; then
  echo "WARN: MR state NOT verified — guard unreachable at $G. State unknown."
else
  MR_STATE=$(python3 "$G" "<branch>" --forge glab --cwd "<worktree-abs>")
  MR_STATE_RC=$?
  if [ -z "$MR_STATE" ]; then
    echo "WARN: MR state NOT verified for '<branch>' — rc $MR_STATE_RC, no output: the helper never answered."
  else
    case $MR_STATE_RC in
      0) ;;
      1) echo "MR !<mr-iid> for branch '<branch>' is in state '$MR_STATE' (expected 'opened')." ;;
      3) echo "WARN: no MR found for branch '<branch>' — nothing to follow up on yet." ;;
      *) echo "WARN: MR state NOT verified for '<branch>' — rc $MR_STATE_RC, reported '$MR_STATE'." ;;
    esac
  fi
fi
```

The block itself never exits — on the normal path intake warns and continues, and the guard that stops is step 7 item 6. **`[ -z "$MR_STATE" ]` is the correctness test**: an rc does not tell you the helper answered. A crashing script exits `1` with empty stdout, which the rc-`1` arm would render as `is in state ''`; a truncation left with valid syntax exits `0` the same way, which the rc-`0` arm would render as silence — read as "MR is open" (both measured 2026-08-21). Only past that test does the rc pick the wording, and unknown codes fall to "NOT verified" rather than to a fabricated state. **`[ -f "$G" ]` is the diagnosability test**, not a second correctness one — rc `2` already routes a missing script correctly, but conflates it with a bad token or a dead network, and the operator action differs: restart the session, versus fix the token. Restart and not `unset CLAUDE_PLUGIN_ROOT` — this block is a rendered body, so the placeholder is already a literal path by the time any shell runs and unsetting it changes nothing here. How a live session's script path vanishes under it: worth logging in a personal notes file if you keep one — section "Releasing a plugin kills the hooks of every already-open session" in the author's own (by title — sections get renumbered).

**GitHub (`FORGE=github`):**

```bash
cd "<worktree-abs>" && gh pr view --json number,state,isDraft,baseRefName,headRefName,url
```

Extract `PR_NUM=.number`, `BASE=.baseRefName`. `state != OPEN` → warn. Draft → proceed, flag it. No PR (non-zero exit) → print "No PR for branch `<branch>` on `<slug>`. To open one: `gh pr create`, then re-invoke this skill." and exit 0. Multiple PRs → `cd "<worktree-abs>" && gh pr list --head "<branch>" --state open --json number`; still ambiguous → ask.

**Transition ticket to CodeReview** (idempotent, forward-only — catches MRs opened manually after the push):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
KEY=$(python3 "$R/lib/derive_ticket_key.py" "<branch>" --cwd "<worktree-abs>")
if [ -n "$KEY" ]; then
  PREFIX="${KEY%%-*}"
  python3 "$R/lib/tracker_config.py" run "$PREFIX" transition_cmd "$KEY" "CodeReview" --leaf-only --sync-parent --clear-blocked || echo "WARNING: transition_cmd failed for $KEY -> CodeReview (continuing)"
fi
```

`DEV_FLOW_NO_TRACKER=1` / `.claude/no-tracker` marker → empty `KEY` → skips. No `transition_cmd` configured for the prefix (e.g. a read-only tracker) → also skips, no special-case needed. A `transition_cmd` failure — any non-zero exit, including a `--leaf-only` refusal on an umbrella ticket or a `--sync-parent` exit 7 (all the parent's sub-tasks at/past Done — the close is gated with the user at post-merge, step 8.6; see `simo-dev:task-intake` step 2.5) — only prints the warning above and the flow continues.

### 1.5. Round cap

`CAP` = **5** unless the invoker passed a lower value (`cap=<n>`). 5 is the ceiling and **2 is the floor** — a passed value may only lower it
within that range. Below 2 this skill would run no fix round at all, so the value stops meaning anything (the other one would still run a fix round), so a passed 1 or 0 is
clamped up to 2 — never rejected into the default of 5, which would silently cost more than asked.

Pass `cap=2` when the diff is prose and nothing else: no code, and no executable content inside the
prose either. Do not infer it from which file changed: the bodies this flow edits most are dense
with runnable blocks (`commit` 15 `bash` fences, `mr-followup` 11, `task-intake` 11 — measured
2026-08-22, indented fences included). So judge by whether a changed line falls inside a fenced
`bash`/`sh`/`python` block, in any file. The judgment belongs to the invoker, which has the diff in
context; deriving it from file extensions was tried and abandoned (see the plan behind this rule).

A `high` or `critical` finding that survives the validity filter raises `CAP` back to 5 for the rest
of the rounds, so a cap passed in error cannot weaken the review. Evaluate that at step 7,
before the cap-reached branch. Announce the value in one line so the report is auditable.

### 2. Launch pipeline poll (background, non-blocking)

Launch the poll with `run_in_background: true` — tell the user it's running; do NOT sleep or poll manually, and do NOT wait for it here. Steps 5-6 (fetch remote comments, local code review) don't depend on the pipeline outcome — continue straight to step 5 once the poll is launched. Its result is collected at step 7, the flow's one sync point for it.

GitLab: `lib/pipeline_poll_gitlab.py "<worktree-abs>" "<branch>"`, which scopes its own `glab pipeline list` call to `<worktree-abs>` internally, transitional states (`running|pending|created|preparing|waiting_for_resource|scheduled|canceling`) = still going. Bounded on both ends — `POLL_NOPIPE_MAX_WAIT` (300s) while no pipeline for HEAD matches the branch, cut short to the first read when a one-shot probe finds no CI config for the branch (`lib/pipeline-scripts.md` "GitLab no-CI probe"), `POLL_MAX_WAIT` (1800s) once a pipeline is visible. GitHub: `lib/pipeline_poll_github.py "<pr-num>" "<worktree-abs>"`, bounded on both ends with the same knobs — `POLL_NOPIPE_MAX_WAIT` (300s) while no check has been reported for HEAD, `POLL_MAX_WAIT` (1800s) once one has, `POLL_INTERVAL` (30s) between polls. Both polls report only on the worktree HEAD's own pipeline/checks, never the newest run on the branch (`lib/pipeline-scripts.md` "Pinned to HEAD").

> Launch commands + env var/exit-code contract: `lib/pipeline-scripts.md` — load when launching the poll. Exit-code interpretation is read at step 7, not here.

> Steps 3-4 retired with the CI AI-review experiment. Numbering preserved — grep `step 8` across the plugin before renumbering anything.

### 5. Fetch review comments

Both helpers print `## <author> — <path> line <N>\n<body>\n`, or `No open review comments found.` when the forge really has zero open comments. Capture as `REMOTE_COMMENTS`.

Capture the rc **and print it** in the same call — a bare run whose output you read and whose status you drop is what let a failed fetch pass as zero comments. The `printf` is not cosmetic: shell state dies between calls, and a block ending in an assignment exits 0, so without it the block hands back empty output at rc 0 (measured) and the branch below is unreachable:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
REMOTE_COMMENTS=$(python3 "$R/skills/mr-followup/lib/fetch_discussions.py" "<slug>" "<mr-iid>" "<api-host>")   # GitLab
FETCH_RC=$?; printf 'rc=%s\n%s\n' "$FETCH_RC" "$REMOTE_COMMENTS"
```

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
REMOTE_COMMENTS=$(python3 "$R/skills/mr-followup/lib/fetch_pr_threads.py" "<slug>" "<pr-num>")    # GitHub
FETCH_RC=$?; printf 'rc=%s\n%s\n' "$FETCH_RC" "$REMOTE_COMMENTS"
```

Read `rc=` from the first output line; stderr is unredirected, so the helper's own message is what tells the causes apart.

**Any non-zero `FETCH_RC` is a STOP for the whole followup — never read it as zero comments.** Branch on it before using `REMOTE_COMMENTS`:

- `FETCH_RC=0` → the capture is the comments, or the zero-comments sentinel. Continue to step 6.
- `FETCH_RC=2` → fail-closed, same meaning as `MR_STATE_RC=2` in step 7: the fetch did not complete (bad/missing token, network down, `gh` missing, forge error), and stdout carries a `REVIEW COMMENTS UNAVAILABLE` line. On GitHub that marker is *appended* — comments already printed by the threads query sit above it — so match the marker anywhere in the capture, not just on the first line. **STOP**, do not continue to step 6. Report: "Could not fetch review comments for '<branch>' — the forge was not reached, so 'no comments' is unknown, not confirmed. Fix the token/network and re-invoke; the fetch is idempotent."
- any other non-zero → same STOP, and stdout may be **empty**: a bad argument, or an uncaught error the fetcher's net did not convert (both exit 1). Report the rc and the helper's stderr verbatim rather than guessing a cause.

GitLab token: `GITLAB_TOKEN` or macOS keychain (`gitlab-api-token`); one-time setup with ACL recipe in `lib/keychain-setup.md`. GitHub: `gh` handles auth.

### 6. Local code review

Always runs — dispatch `simo-dev:code-reviewer` against the diff:

```bash
git -C "<worktree-abs>" fetch origin "<base>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
BASE_AHEAD_MSG=$(python3 "$R/lib/check_local_base_ahead.py" --repo "<worktree-abs>" --base "<base>" 2>&1)
BASE_AHEAD_RC=$?
[ $BASE_AHEAD_RC -eq 0 ] || { echo "$BASE_AHEAD_MSG"; [ $BASE_AHEAD_RC -eq 2 ] && echo "STOP: could not verify local base state (rc 2) — treating as unsafe."; exit 1; }
CHANGED=$(git -C "<worktree-abs>" diff --name-only "origin/<base>"...HEAD)
DIFF=$(python3 "$R/lib/diff_capture.py" "<worktree-abs>" -- "origin/<base>...HEAD")
DIFF_RC=$?
printf 'rc=%s\n%s\n' "$DIFF_RC" "$DIFF"
[ $DIFF_RC -eq 0 ] || { echo "STOP: diff capture failed (rc $DIFF_RC) — do not dispatch the reviewer on a partial/empty diff."; exit 1; }
CODE_REVIEW_MODEL=$(python3 "$R/lib/model_strategy.py" get code_review)
```

This guard mirrors the one in `commit/SKILL.md` step 7 (same failure mode: local `<base>` ahead of `origin/<base>` with those commits already in this branch's ancestry) — there is no bypass: ask the user for explicit confirmation before pushing the local base (a protected-branch push), never push it unasked; once pushed, re-run (the block already re-fetches `origin` first).

Agent prompt: review the diff, one finding per line as `[severity] file:line — message` (low|medium|high|critical), real problems only. Output → `LOCAL_REVIEW`. Dispatch also passes `model: $CODE_REVIEW_MODEL` whenever that variable is non-empty (omitted when empty, same inherit-the-caller's-tier behavior as today). `$DIFF` already passed through the diff capture gate at the point it was computed above (`lib/diff_capture.py`, ~50k-char threshold, same number everywhere in this plugin) — forward it to the reviewer prompt unmodified, never re-sliced here. If a trailing `Full diff written to: <path>` line is present (the large-diff case), that path can be read with `Read` if the full content is ever needed beyond what's already in context. This dispatch deliberately does **not** carry the `REPO:` marker line that `simo-dev:code-review-protocol` requires: it reviews `origin/<base>...HEAD` only, while the record that marker writes certifies the working tree and untracked files too — uncommitted work no reviewer here saw. Do not "fix" the missing marker.

**Carry the prior rounds — from round 2, and on round 1 whenever `simo-dev:review-loop` already ran on this diff** (its step 8 printed the fix log, the rejected findings with rationale and the unresolved list — skip the round-1 carry when that output is no longer in context; a round 1 that ignores them re-derives up to five settled iterations). The reviewer is stateless; open the prompt with "The following were already evaluated, each with the reason given. Do NOT re-flag them unless you can refute the rationale with new evidence.", then three **cumulative** lists — every round's entries, carried forward on every later round, never just the last one's: **dropped by the validity filter** (step 7 item 2's `rejected:` lines, or `review-loop` step 5's on the round-1 path, with their reasons — `low`s rejected, declined by the user or `deferred (needs decision)` travel here too, with that reason); **confirmed correct** — code an earlier round examined and the caller judged right, with the reason, from the caller's own reading since no reviewer output carries it; **verified by the caller** — what was asserted and the exact command, instructing the reviewer to attack those checks (does the command discriminate? would it fail if the behavior broke?), never to repeat them. Anything left **unresolved** goes in too — step 7 item 2's **ambiguous** drops, findings without `file:line`, and on the round-1 path `review-loop`'s own unresolved entries — under their own label, "still open — re-raise if the code is still wrong", and **outside** the clause, same as `review-loop` step 2.

### 7. Iteration loop (cap = CAP, step 1.5)

**Collect the pipeline poll launched at step 2** — this is the flow's one sync point for it; if its task notification hasn't arrived yet, wait for it now (do not proceed on an assumed result). Report its status regardless of which branch below the round takes: fold it into the round-N summary on the fix branch (medium+ findings or applicable `low`s, item 1), or alongside the clean-exit report on the other branch — a clean local review and zero remote comments do not mean the round has nothing to say if the pipeline failed or its state came back unverified. On **both forges** the poll's **exit code is the verdict** and the codes are identical, so read it and not just the last line: `0` a pipeline concluded, or every check concluded — report the status word or the failed count, a `failed` pipeline is rc `0` too; `3` no pipeline exists for this branch at HEAD, or the PR has no checks for HEAD — say so in one line and continue, it is not a pipeline result and not a failure; it also covers a HEAD that never reached the forge (never pushed, or on GitHub a PR head still not HEAD — its `NO CHECKS for <sha>` line), so check that the push landed before reporting "no checks"; on GitLab the last line says whether the ceiling came due (`NO PIPELINE`) or the probe found no CI config for the branch (`NO CI CONFIG`, returned at the first read), report it the same way either way; `4` a ceiling came due with no conclusion — on GitLab a pipeline still transitional, one that vanished from the list, or a line whose status did not parse; on GitHub checks still pending, or checks seen once and then gone, which `checks=` (nonzero vs `0`) on that line tells apart, not `checks_seen=` (always `1` there by construction), or — at once, no ceiling due — a PR head that moved off HEAD after its checks were seen (`HEAD MOVED` line); the poll's last lines say which — and `5` the read that hit the ceiling failed, so the state is unverified (the forge may well have answered earlier); report both and **never** treat either as green; `1` the poll refused to start — read its last line for which precondition failed, on GitLab an empty `<branch>` literal, a `<worktree-abs>` that does not resolve to a real directory, or a `glab` that cannot run there, on GitHub a missing `<pr-num>` or `<worktree-abs>` argument or a `gh` that cannot run there, on both an empty `<worktree-abs>` literal or a HEAD that does not resolve in `<worktree-abs>`; fix that one and relaunch right here, synchronously — steps 5-6 already ran this round, so looping back to step 2 would only re-run them for nothing. Codes and rationale: `lib/pipeline-scripts.md`. The loop re-enters step 2 after every push (item 6 below), so this collection happens every round: round N's poll here is the one launched at round N's own step 2, for round N's own pushed commit — never a stale result carried over from an earlier round.

Build the round-N body: concatenate `REMOTE_COMMENTS` + `LOCAL_REVIEW` (header-separated) when both have content; use the non-empty one otherwise; both empty (a local review legitimately returns no findings) → "Nothing to review", stop.

Parse findings: regex `^\[(low|medium|high|critical)\]\s+([^:]+):(\d+)\s+—\s+(.+)$`. Group by severity; findings without `file:line` → unresolved.

**Decision per round:**

**Applicable `low`**: a `low` on a file in the MR diff while round N < CAP, and **not already eligible in an earlier round, or in the `review-loop` run carried into round 1** (same file and message) — such a `low` never re-enters the fix branch, whatever happened to it. Applicable `low`s are treated like medium below; one is an In-scope improvement only on an all-`pre-existing` branch-point check (item 2 below; `docs/canonical-flow.md` "In-scope improvements"), never when it came from a remote comment; on round CAP they are listed only. One needing a user decision → ask; unattended → `deferred (needs decision)`.

- No findings ≥ medium and no applicable `low` → **clean exit** (a round whose only findings are non-applicable `low`s is a clean exit, never a no-op commit): before reporting, run the harvest catch-up pass — the same three-question review from `skills/commit/lib/knowledge-harvest.md`, scoped to everything that emerged since `commit` step 0.7's pass, the review-loop `commit` step 1 may have run included (session-memory dedup against what `commit` step 0.7 already captured, per that file's dedup framing — not a full re-review). Nothing written in this repo (nothing new, or only facts routed to `spawn_task`, which run on every round) → proceed straight to the clean-exit report below, no behavior change. Something written in this repo and round N < CAP → treat the edits as this round's fixes: item 3's plan-drift check, then commit + push through items 5-6 below (same guard, do not paste the bash a second time), then back to step 2 for round N+1 as item 6 already does (fresh pipeline poll), whose local code review covers them. This round counts against `CAP` like any fix round; the next clean exit's catch-up skips what this one captured (session-memory dedup), so the loop ends. The `review_loop_warn` reminder on any fix-round commit, harvest included, is expected: the next round's local code review is the coverage, not `review-loop`. Something to write in this repo and round N == CAP → do not write: list the would-be harvest edits in the final report for the user, since no round is left to review them. Then report the collected pipeline status alongside the `low` findings not applied, stop. Never edit after the clean exit: that is code no round reviewed, and costs another commit + push.
- Round N < CAP with medium+ findings or applicable `low`s →
  1. Show a summary (count by severity, top 5 messages).
  2. **Validity filter** — `simo-dev:code-review-protocol` rigor per medium+ finding and applicable `low` BEFORE fixing (remote comments come from reviewers without this session's context): (a) technically correct for THIS codebase? (b) fix breaks existing functionality? (c) deliberate design? (d) reviewer had full context? Fails → drop, log `rejected: [severity] file:line — message → reason`. Ambiguous → unresolved.

     Then, before item 3 applies anything, run the branch-point check (`docs/canonical-flow.md` "In-scope improvements") once on every passing `low` from `LOCAL_REVIEW` — targets: its cited `file:line` plus every line its fix will replace or remove, or both sides of a pure insertion. A `low` from `REMOTE_COMMENTS` is never an In-scope improvement and gets no check. `LOCAL_REVIEW` lines are HEAD coordinates while the script reads the working tree, so uncommitted tracked edits skip the script and make every target `error`:

     ```bash
     R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
     git -C "<worktree-abs>" diff --quiet HEAD || { echo "uncommitted tracked edits: every target is error"; exit 0; }
     python3 "$R/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "$(git -C "<worktree-abs>" merge-base HEAD "origin/<base>")" "<file1>:<line1>" "<file2>:<line2>" ...
     ```

     `<base>` = the literal step 1 resolved (`origin/<base>` is fresh from step 6's fetch). Skip the block when no `LOCAL_REVIEW` `low` passed. rc ≠ 0, or a target with no output line → `error` for that `low`.
  3. Auto-apply passing fixes via `Edit`/`Write`. Track each fix. Then, before item 5's commit, run the plan-drift check on every file just fixed:

     ```bash
     R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
     python3 "$R/lib/check_plan_drift.py" --repo "<worktree-abs>" --base "<base>" --changed-file <file1> <file2> ...
     ```

     `<base>` = the same literal this skill's step 1 already resolved (the MR/PR target branch) — never omit it here: the script's own `default_base()`, when called with no `--base`, resolves `origin/HEAD` (falling back to `main`/`master` only if that is unset), which silently diverges from `<base>` whenever the MR targets a non-default branch. With that divergence, a plan merged into `origin/HEAD` but not yet into the MR's real target gets wrongly excluded as "already merged" (false negative — a live plan goes unchecked), and a plan merged into the real target but not yet into `origin/HEAD` gets wrongly flagged as needing a resync (false positive — a stale plan the round already accounted for). `<file1> <file2> ...` = every unique file path this round actually applied a fix to, in the same repo-root-relative form `git -C "<worktree-abs>" diff --name-only` prints (the same form a plan's `**Files:**` bullet declares) — the `[severity] file:line` findings parsed at this step (step 7's own parse regex) are observed to carry paths in that form in this plugin's existing diff-derived findings, just strip the trailing `:line`. Never a `lib/`-relative shorthand or an absolute path: `plans_mentioning()` does an exact string match against the plan's declared paths, so a differently-rooted path silently matches nothing (skip the call entirely when the round applied zero fixes). rc 0 → nothing to do. rc 1 → for each `<plan> mentions <file>` line printed, read that plan around the embedded target for `<file>` and, if the embedded code/text still shows the pre-fix version, resync it with a targeted `Edit` — this must land before item 5's commit, since that commit is unconditional and immediate: a drifted plan would otherwise ship in the same commit as the fix that caused the drift. rc 2 → note the error and continue; a script failure never blocks the round.
  4. Show the applied fixes as `git -C "<worktree-abs>" diff --stat` + one line per fix (full diff only if the user asks), the rejected list, branch + remote, the proposed commit message, and on its own labelled line any `low` whose branch-point check was `error` (applied, not listed as an In-scope improvement).
  5. **No gate** — zero fixes applied this round → no commit, no push: every finding rejected, declined by the user or `deferred (needs decision)` → take the clean-exit branch above; any **ambiguous** one → not clean, surface the unresolved list alongside the collected pipeline status and stop, as the cap-reached branch does. Otherwise commit unconditionally: NEW commit (no amend), conventional message scoped to the ticket. Its body lists the In-scope improvements this round applied — `low`s whose item-2 branch-point check was all `pre-existing` and that do not complete the branch's own change, and dead-code deletions — one line each (file — what — why) — the MR body is not rewritten.
  6. **Pre-push MR-state guard** — re-verify the MR/PR is open (incident 2026-07-17: pushing after a mid-session merge recreates the deleted branch as an orphan — `git push` prints `[new branch]`, easy to miss):

     ```bash
     R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
     G="$R/lib/check_mr_state.py"
     if [ ! -f "$G" ]; then
       echo "STOP: could not verify MR state for '<branch>' — the guard is not at $G, so it never ran. If that path starts at /lib/, R was never bound: re-issue this whole block. Otherwise the plugin dir is incomplete — restart the session to rebind the plugin root. Pushing blind risks recreating a merged branch as an orphan. The fix commit is local."; exit 2
     fi
     MR_STATE=$(python3 "$G" "<branch>" --forge "<forge>" --cwd "<worktree-abs>")
     MR_STATE_RC=$?
     if [ -z "$MR_STATE" ]; then
       echo "STOP: could not verify MR state for '<branch>' — the guard exited rc $MR_STATE_RC but printed nothing, so it never answered. That is the guard itself being broken (a crashing or truncated check_mr_state.py, or a usage error such as a bad --forge or a relative --cwd), not a bad token. A crash or a usage error left a line on stderr above; a truncation leaves nothing at all, and that silence is the tell. Pushing blind risks recreating a merged branch as an orphan. The fix commit is local."; exit 2
     elif [ $MR_STATE_RC -eq 0 ] || [ $MR_STATE_RC -eq 3 ]; then
       [ $MR_STATE_RC -eq 3 ] && echo "WARNING: no MR found for '<branch>' mid-loop — an MR should exist by now."
       git -C "<worktree-abs>" push -u origin "<branch>"
     elif [ $MR_STATE_RC -eq 1 ]; then
       echo "STOP: MR/PR for '<branch>' is now '$MR_STATE' — likely merged/closed mid-session; pushing would recreate '<branch>' as an orphan. The fix commit is local, ready to cherry-pick onto a fresh branch."; exit $MR_STATE_RC
     else
       # No backticks in these messages: inside a double-quoted echo they are command substitution,
       # so the identifiers they were meant to quote get executed and then deleted from the output.
       echo "STOP: could not verify MR state for '<branch>' (glab/gh missing, bad token, network down, a transient forge error, or a bad --cwd such as an unsubstituted <worktree-abs>) — pushing blind risks recreating a merged branch as an orphan. The fix commit is local; re-run once the guard can verify again."; exit $MR_STATE_RC
     fi
     ```

     What the codes mean — the block decides, so never re-issue the push from a separate call. **Two tests run before any rc is read, and both end the loop with `2`**: the guard file is not there (`[ -f "$G" ]`), and the guard printed nothing (`[ -z "$MR_STATE" ]`). An rc can say why a helper failed, never that it answered, and an unanswered guard would push blind on either push branch — rc `0` included, since a script truncated to valid syntax exits `0` with empty stdout (both worth logging in a personal notes file if you keep one — section "A custom exit code that the interpreter also produces, with the same empty stdout"; how a live session's script path vanishes under it is the section "Releasing a plugin kills the hooks of every already-open session", both in the author's own). Past those two tests, `0` (open) and `3` (not found, unexpected mid-loop since an MR should exist by now) are the only push branches; after either, back to step 2 for round N+1. Force-push stays forbidden; merge never auto. `1` (merged/closed) ends the loop. So does everything on the `else`, `2` (could not verify) included — fail closed, and re-run once the guard can verify again. That "re-run" applies to the `else` only: the existence test and the arrival test above mean the guard never ran or never answered, so an unattended caller parks on them rather than retrying — with the one evidence-gated exception `simo-dev:commit` step 5's classifier spells out, and its evidence differs per arm: a `/lib/…` root in the path the existence stop echoes, or, for the arrival stop, an argparse line on **stderr** naming a bad `--forge` or a non-absolute `--cwd`. Those are the caller's own substitution — re-issue the block with the literals in place and run it once more before parking. A rejected `git push` also exits `1`, being the last command of the push branch, but prints git's own error and no `STOP:` line — that is not the guard stopping you: fetch, rebase and retry the round.

  **Thread reply policy (parallel to item 5)** (GitHub only) — for findings from inline comments: reply in-thread (`gh api .../comments/{id}/replies`) "Fixed in <sha>." after the commit, or "Not applied — <rationale>." at filter-reject time. Never "thanks"/"great point" — technical acknowledgment only. GitLab: resolve via `glab` if straightforward, else leave to the user.
- Round N == CAP with medium+ findings → **cap reached**: surface the full unresolved list (especially `critical`) alongside the collected pipeline status, do NOT auto-push, hand off to `simo-dev:code-review-protocol` (receiving side). Before taking this branch, apply step 1.5's escalation: run the validity filter (item 2 above) on this round's `high`/`critical` findings first — this branch otherwise never runs it, so the escalation would hang on a verdict the procedure never produces. A surviving `high`/`critical` here **or in any earlier round** means `CAP` is already 5, so the loop continues and that round takes the fix + commit + push branch above, not this one.

### 7.5. Pre-merge QA verify gate (optional)

Runs after step 7 exits clean, before the merge (human gate, outside this skill — unchanged). Active only in repos with a release skill that can deploy an **arbitrary branch** to QA (verify arbitrary-branch support first); not detected → skip silently, zero behavior change elsewhere.

Not always-on — ask only when the diff touches ranking/scoring/search-query code, importers/generators, or runtime-behavior config, OR a release skill is present but that heuristic is inconclusive. Otherwise skip silently — trivial merges shouldn't slow down.

On yes: deploy the branch to QA (same tracks the diff touches) → wait for / trigger the scheduled eval+perf run → compare against the last valid pre-deploy baseline (same scheduler/environment) → present the comparison, ask explicit go/no-go before the user proceeds to merge.

> Full procedure (capability list, heuristic patterns, multi-track deploy gotcha, QA-vs-PROD scheduler gotcha): `lib/pre-merge-qa-gate.md` — load when the gate triggers.

### 8. Post-merge transition + verification notes + worktree cleanup

Runs only after the user has explicitly merged (the merge is a separate human gate, outside this skill). Then:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
KEY=$(python3 "$R/lib/derive_ticket_key.py" "<branch>" --cwd "<worktree-abs>")
if [ -n "$KEY" ]; then
  PREFIX="${KEY%%-*}"
  NO_DEPLOY=$(python3 "$R/lib/check_deploy_required.py" "$KEY" --repo "<worktree-abs>")
  if [ "$NO_DEPLOY" = "1" ]; then
    TARGET=$(python3 "$R/lib/tracker_config.py" get "$PREFIX" post_merge_state_no_deploy)
    [ -z "$TARGET" ] && TARGET="Done"
  else
    TARGET=$(python3 "$R/lib/tracker_config.py" get "$PREFIX" post_merge_state)
    [ -z "$TARGET" ] && TARGET="Ready for Testing"
  fi
  python3 "$R/lib/tracker_config.py" run "$PREFIX" transition_cmd "$KEY" "$TARGET" --leaf-only --sync-parent --clear-blocked || echo "WARNING: transition_cmd failed for $KEY -> $TARGET (continuing)"
  echo "post-merge: KEY=$KEY PREFIX=$PREFIX TARGET=$TARGET"
fi
```

When the merged plan (located by the same `docs/plans/YYYY-MM-DD-<key-lower>-<name>.md` convention `plan-write` uses, ticket key anchored right after the date) declares `**Deploy required:** no`, the target instead comes from `post_merge_state_no_deploy` (falling back to `"Done"` when unset); any ambiguity in locating or reading that plan (not found, more than one match, field absent/unrecognized) falls back to the path below unchanged. Target state comes from `post_merge_state` for the prefix, defaulting to `"Ready for Testing"` when unset. `DEV_FLOW_NO_TRACKER=1` / `.claude/no-tracker` marker skip; no `transition_cmd` configured for the prefix also skips, no special-case needed; forward-only no-op if the ticket already moved past target. A `transition_cmd` failure — any non-zero exit, including a `--leaf-only` refusal on an umbrella ticket or a `--sync-parent` exit 7 (all the parent's sub-tasks at/past Done, see `simo-dev:task-intake` step 2.5; followed up by step 8.6's parent-close gate) — only prints the warning above and the flow continues.

**Verification notes ticket comment** — write it between the ticket transition and the cleanup (the notes need branch/MR/diff context that cleanup destroys). Consumed at release time by the release skill's per-ticket verify step, which confirms the deployed behavior and transitions the ticket (`Ready for Deploy` after QA deploy, `Done` after prod).

- **Write** when the change alters observable runtime behavior (endpoints/response shapes, search/scoring, jobs/importers, runtime config, bug fixes with reproducible symptom). A passing step 7.5 run this session gets noted in the comment, not skipped.
- **Skip** for docs/typo-only, test-only, CI config, pure refactors, dependency bumps.

Then auto-execute the worktree cleanup (pre-approved exception — runs ONLY after the user-approved merge) and append the dev-flow journal entry (`~/.claude/state/dev-flow-journal.md`, step 8.5 — written only by piping into `lib/journal_append.py`, never `cat >>`/Write onto that path, see `lib/post-merge.md`; skip for `--no-mr`/WIP runs that never merged). After the journal, step 8.6 offers the next ready sibling sub-task as a spawn chip, or the umbrella close once every sub-task is Done — both behind a user gate, driven by the `post-merge:` line printed above (the worktree is gone by then).

> Full procedure (verification-notes comment spec, cleanup script, journal format, step 8.6 next sub-task / parent close): `lib/post-merge.md` — load when the merge is confirmed.

## References

- GitLab keychain one-time setup: `lib/keychain-setup.md`.
- PostToolUse hook to auto-trigger this skill after `git push`: `lib/hook-setup.md`.
- GitLab vs GitHub quick-reference: `lib/quick-reference.md`.
- Pre-merge QA verify gate detail (step 7.5): `lib/pre-merge-qa-gate.md`.
