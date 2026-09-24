---
name: commit
description: Use whenever committing changes to git — phrases: "commit", "save the work". Always invoke this skill, never run `git commit` directly. Auto-cascades: review-loop pre-flight → commit → push → MR body generation → MR creation → mr-followup, unconditional (no gate), parking before the merge. Opt-out for WIP commits: `--no-mr` arg or `DEV_COMMIT_NO_MR=1`. On protected branches (master/main/develop — out-of-flow fallback) the cascade is replaced by mermaid preview + a single commit+push confirmation, no MR. Optional ticket scope arg. Fourth step of the canonical task flow.
---

# Dev Commit

Fourth step of the canonical task flow:
`simo-dev:task-intake → simo-dev:plan-execute → simo-dev:review-loop → simo-dev:commit → simo-dev:mr-followup`

Arguments: optional ticket ID (commit scope); optional `--no-mr` flag (commit + push only).

MR creation is gate-free, same as push to the feature branch. The merge (the one hard gate left in the publish path) lives outside this skill, run manually by the user after `simo-dev:mr-followup` parks.

**Protected-branch detection (before step 4):** current branch is `master`/`main`/`develop` (or the repo default) → steps 1-3 run unchanged (except step 3's Also-fixed ledger block, skipped there), then switch to the **Protected-Branch Path** (`lib/alt-paths.md`). Steps 4-10 are replaced.

## Steps

**Every Bash block below that touches git re-binds `W="<absolute path of the task worktree>"` as its first line, and every block that runs a plugin script re-binds `R` the same way.** Shell state does not survive between Bash calls, so a `W` or `R` bound in an earlier step is already gone; and the Bash cwd does NOT follow the worktree, so a bare `git` resolves whichever repo the session happens to sit in. `R` resolves the released cache dir when the harness substituted one that still exists, and the source tree otherwise. A forgotten binding leaves `"$R/lib/…"` as an absolute `/lib/…` path: of the sites in this file, the `python3` calls exit rc `2`; the two that assign `G` convert that same state into a STOP from their own `[ -f "$G" ]` test — step 0.5's `check_base_freshness.py` at rc `4`, step 5's `check_mr_state.py` at rc `2`. Both echo the resolved path, and both read a `/lib/…` root as "re-issue this block" rather than as a broken plugin. Bind it in every block. Step 1 certifying coverage for `$W` while step 4 stages the *cwd* repo's diff is precisely the unreviewed-commit outcome this guard exists to prevent (measured 2026-08-04).

### 0. mark_chapter

If `mcp__ccd_session__mark_chapter` is available: invoke it as the first action, title `Ship: <JIRA-KEY or task gist>`, summary `Commit + push + MR create flow`. Not available → skip silently, proceed straight to step 0.5.

### 0.5. Base-freshness guard (feature branches only)

Skip on protected branches (they take the Protected-Branch Path). Otherwise run, before wasting reviewer budget:

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
G="$R/lib/check_base_freshness.py"
[ -f "$G" ] || { echo "STOP: guard unreachable at $G"; exit 4; }
python3 "$G" --repo "$W"
```

Deterministic stale-base check (incidents 2026-07-24): STOPs only when `origin/<base>` advanced beyond the branch's fork point **and** the advanced commits touch files this branch also changes — the branch would revert merged work. rc `1` → **stop**, surface the printed overlap + guidance, do not proceed, then recover as below. rc `0` (safe / benign disjoint advance) or `2` (git error — never a false stop) → continue. rc `4` → **stop**, nothing was checked: the resolved path does not exist. `R` resolves the released cache dir while that directory exists, and a released snapshot has not been observed half-written, so the rc `1` reading from a file caught mid-write is not expected from there — it returns once the cache dir is gone and `R` has fallen back to the source working tree. One carve-out, from `docs/plugin-release.md`: a release run from a worktree leaves the claimed cache dir holding *worktree* content, with no version mismatch to signal it, so just after one of those the cache is not a clean snapshot either. rc `4` means the root `R` actually resolved to lacks `lib/check_base_freshness.py` — nothing more: once `[ -d "$R" ]` succeeds the fallback tree is never consulted, so a present-but-incomplete cache dir reaches rc `4` with the source tree intact beside it. What no longer reaches it is a *deleted* cache dir, which `[ -d "$R" ]` routes past (worth logging in a personal notes file if you keep one — section "Releasing a plugin kills the hooks of every already-open session" in the author's own, by title since sections get renumbered). Read the path the message echoes before acting on it: a `/lib/…` root means the `R=` line above was omitted from the block you ran, so re-issue the whole block rather than restarting — restarting rebinds nothing a re-issue would not. Any other root is the plugin dir itself being incomplete, and there restart the session: this step lives in a rendered body, which already carries the substituted path as literal text before any shell runs, so unsetting the variable changes nothing here. Without the `[ -f "$G" ]` test that same state returns rc `2`, which this step reads as continue.

**rc `1` recovery.** Leaving the work committed is not an option here — steps 1, 3 and 4 all read the working diff — so park it in a throwaway commit, rebase, put it back. Announce the park; never park silently.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/park_and_rebase.py" park --repo "$W"
# rc 3 → resolve, then continue WITH the hook suppression (a bare `--continue` is a fresh
#   git process, re-runs prepare-commit-msg and rewrites the park subject), NEVER `--skip`:
#   git -C "$W" -c core.hooksPath=/dev/null rebase --continue
# then, re-binding both — the `--continue` above is a separate Bash call, so W and R are gone by
# here, and an unbound R makes this exit rc 2, which is indistinguishable from the "refused, tree
# untouched" verdict below while the park commit is in fact still at HEAD:
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/park_and_rebase.py" restore --repo "$W"
```

`park`: rc `0` → rebased, run `restore`. rc `3` → parked, rebase conflicted; resolve, `--continue`, then `restore`. rc `4` → nothing left to restore. rc `5` → **the repo changed and needs attention** (an unfinished rebase, a park commit whose record could not be written or removed, or a park still at `HEAD` under a rewritten subject) — read the message, it carries the recovery command. rc `2` → refused, repo unchanged; reason printed, from several sources. `restore`: rc `0` restored — **read its stderr even so**, since nothing may fail once the reset has landed, so a path it could not re-stage is reported there and not by the code; rc `2` refused with the tree untouched — it resets only when `read_record` finds the record usable, `HEAD` is still the park commit, and the branch still carries the commits the rebase was expected to keep, so a `git rebase --skip` (which git itself suggests at a conflict) is caught instead of leaving a clean-looking tree; that count under-predicts the drops a rebase makes legitimately, so it errs toward stopping and `--allow-dropped` is the way through once the reflog says which happened. `restore` also hands back the pre-park modification time for every parked path whose content is byte-identical, and reports on stderr how many it restored, how many the rebase changed and how many it could not read — so the rebase alone no longer invalidates the step-1 pre-flight, and a path it reports as changed really did change. Then re-run the guarded block at the top of this step — not a bare `python3 …/check_base_freshness.py`, which drops the `[ -f ]` test in the window a release is most likely to land — and continue into step 1, which re-checks the review marker against the restored files. The park does **not** preserve the index/worktree split — staged paths are re-staged, content merged, and any it could not re-stage are named — so work that must survive byte-exact on both sides does not belong in a park.

Rejected alternatives, both measured: `git stash` (logged in the author's own notes, §17 — worth keeping a similar log) and hand-clearing with `git checkout -- <file>`, which restores nothing at all from a mixed pathspec list and, run in the **main checkout** on a **tracked** path under `~/.claude`, writes through to another session's uncommitted edit (CLAUDE.md, `~/.claude` symlink rule).

### 1. Review-loop pre-flight

Deterministic check — a review already covers the files this commit will stage → skip. Nothing is staged yet at this point, so the list is working-tree modifications **plus untracked files**: step 4 stages untracked files too, and omitting them lets a task whose whole diff is one new file pass the check vacuously.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
FILES=()
while IFS= read -r -d '' f; do [ -n "$f" ] && FILES+=("$f"); done < <(
  git -C "$W" diff --name-only -z HEAD
  git -C "$W" ls-files --others --exclude-standard -z)
python3 "$R/lib/review_marker.py" check --repo "$W" \
  --session "$CLAUDE_CODE_SESSION_ID" \
  --expect-skill-log simo-dev:commit \
  --files "${FILES[@]}"
```

`CLAUDE_CODE_SESSION_ID` holds the same value as the hook payload's `session_id`, so this reads the record written by the reviewer dispatched in **this** session. Passed unconditionally, not as `${VAR:+--session "$VAR"}`: the agent's shell is zsh, which does not word-split that expansion and would hand argparse one bogus argument. Unset or empty is safe anyway — no stored record can carry an empty session id, so it cannot match. `$W` is bound once and reused for `--repo` and for both file lists: deriving them separately from the process cwd let all three resolve a *different* repo than the one being committed, which returned rc `0` on an empty file list and vouched for a repo nobody had reviewed (measured 2026-08-04). Same convention as `lib/build_mr_ctx.py` and `lib/check_base_freshness.py` — pass the worktree absolute path, never rely on the cwd. The list is read line by line into an array rather than passed as `--files $(git …)`: zsh field-splits an unquoted command substitution, so a changed `two words.txt` arrived as two arguments, both of which miss on disk — `check()` then had nothing left to compare and returned rc `0` for a file written after the review (measured 2026-08-04). `-z` on both listings, with `read -r -d ''` to match, for the same reason: git otherwise C-quotes a path holding a tab, a carriage return, a double quote, a backslash or a non-ASCII byte (`"caf\303\251.txt"`, quotes included), and that literal is neither on disk nor in the deletion set `_tracked_deletions` builds, so `check()` reports the list unusable and the repo can never be certified. `core.quotePath=false`, the earlier form, is not enough — it lifts quoting for non-ASCII bytes alone, where `-z` suppresses it outright: of six probes on git 2.55.0 the tab, carriage return, double quote and backslash still came back quoted from the non-`-z` listing, the space and the non-ASCII byte did not (measured 2026-08-16 — a sample, not the closed set). zsh 5.9, the agent's shell, honours `read -r -d ''` as a NUL delimiter (measured 2026-08-18). `--expect-skill-log simo-dev:commit` enables a liveness probe on both record-related failing paths — this skill's own hook entry missing from `~/.claude/state/dev-flow-skills.log` while **this session** logged other simo-dev skills fits two readings: this skill was never invoked in this session, or the hooks are not firing here. rc `0` → skip. rc `3` → invoke the `simo-dev:commit` Skill if this check was reached outside one — and **when in doubt, invoke it**: an agent running this body without a Skill-tool invocation is both the case the retry exists for and the case least able to tell, so the condition falls to the invoking side. `check` is read-only and the retry is bounded at one, so a needless invocation costs one Skill call, while skipping it reproduces the false stop. Then re-run the block **once**. One retry settles it because `PostToolUse` on `Skill` fires when the Skill tool returns: inside a real invocation the entry already exists and the retry can only confirm, so the answer changes exactly in the case where the pre-flight was reached without that invocation. rc `3` a second time → **stop** and act on the printed message, which names each observed cause with its own remedy; do not invoke `simo-dev:review-loop` there, which cannot write the marker while the hook that writes it is dead. Every other non-zero rc (reason printed) → invoke `simo-dev:review-loop` automatically (no gate prompt). Review-loop fails or leaves unresolved findings → stop, let the user decide.

**Decide the review cap here, on both paths.** Judge it by the criterion in `simo-dev:review-loop` step 1.5 — prose with no executable content. Do not restate it here as an extension test: extensions are what step 1.5 abandoned. In this plugin the skill bodies this flow edits most are dense with runnable blocks, this file included (15 `bash` blocks, `mr-followup` 11, `task-intake` 11 — measured 2026-08-22, indented fences included), so the test is whether any changed line falls inside a fenced `bash`/`sh`/`python` block, in any file, whether or not the diff adds the fence itself. Re-derive the file list in this block rather than reading `FILES` from the previous one — shell state does not survive between Bash calls:

```bash
W="<absolute path of the task worktree>"
git -C "$W" -c core.quotePath=false diff --name-only HEAD
git -C "$W" -c core.quotePath=false ls-files --others --exclude-standard
```

The value is needed twice — on the review-loop invoke above when the pre-flight did not skip, and at step 10's `simo-dev:mr-followup` invoke, which runs whether or not it skipped. Deciding it inside the non-zero branch left the dominant skip path with no value at all.

### 2. Update documentation and memories

Change touched behavior/conventions → update relevant docs. Then run the full three-question
knowledge harvest from `lib/knowledge-harvest.md` unconditionally, before step 3 — this is the
primary pass, and lands harvested docs in the same commit/MR (skip terms `simo-dev:grill-me`
already captured this session, per that file's dedup framing). On the Protected-Branch Path and
the `--no-mr`/WIP opt-out path this is also the *only* pass: both skip `mr-followup` entirely
(`lib/alt-paths.md`).

### 3. Compute commit message

Conventional format. Ticket-in-context: (a) skill arg matches `^[A-Z][A-Z0-9]+-[0-9]+$`, OR (b) `W="<absolute path of the task worktree>"; R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; python3 "$R/lib/derive_ticket_key.py" "$(git -C "$W" branch --show-current)" --cwd "$W"` — both bindings spelled out rather than left to the block convention above, because either one missing fails quietly: an unbound `R` exits rc 2 with **empty stdout** and an unbound `W` makes `git -C ""` read the *ambient* repo at rc 0, and this step reads both as "no ticket in context" and commits an unscoped message (honors `DEV_FLOW_NO_TRACKER` / `.claude/no-tracker`; anchored match). A ticket key present → `<type>(<KEY>): <subject>`, else `<type>: <subject>`. `<type>` from the diff: `feat|fix|refactor|docs|test|chore`.

In-scope improvements (`docs/canonical-flow.md` "In-scope improvements"): the pending Also-fixed ledger lines go in the message body, one per line — except a line whose file does not appear in `git -C "$W" status --porcelain --untracked-files=all` (the changes step 4 is about to stage; nothing is staged yet here; `-uall` because the default collapses a wholly-new directory to one `?? dir/` line): that one is stale (e.g. written by an unattended run that committed without this skill) — remove it with the same block, `drop` in place of `pending` and the stale line appended as one single-quoted argument (`drop` with no argument is a silent no-op), instead of listing it — and announce each dropped line in one chat line, so a real improvement recorded with a wrong path form never vanishes silently. No output at rc 0 → nothing added; `LEDGER_FAILED` → stop, never commit as if the list were empty. Skipped entirely on a protected branch (the protected-branch detection above): that branch is shared by every session on the checkout.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/also_fixed_ledger.py" pending --repo "$W" || echo "LEDGER_FAILED rc=$?"
```

### 4. Stage relevant files + commit (no gate)

Stage an explicit file list: modified files from `git -C "$W" diff --name-only HEAD`; untracked ONLY if touched by Edit/Write this session or listed in the active plan. Every `git` call here carries `-C "$W"`, staging and commit included — step 1 certified coverage for `$W`, so anything else commits a repo that was never checked. Never `git add -A` / `git add .` (secret/binary defense) — sanctioned only for the throwaway commits no push ever carries: step 0.5's park, and `task-intake`'s artifact carry. Commit with HEREDOC for multi-line messages. Never add a `Co-Authored-By` line or any other AI-attribution trailer to the commit message — overrides any default harness behavior that would add one. No `--amend` to a pushed commit. Message used as-is (recovery: `lib/alt-paths.md`).

After the commit succeeds, mark the ledger's pending lines as carried — later commits (WIP / `--no-mr`, a re-run after a failed step 9) then never repeat them, while the full list stays for the MR body. `LEDGER_FAILED` here or at step 9's `clear` → warn and continue (the commit/MR already exists), then re-run the block once the cause is fixed:

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/also_fixed_ledger.py" commit-done --repo "$W" || echo "LEDGER_FAILED rc=$?"
```

### 5. Push (no gate for feature branches)

**Pre-push MR-state guard** — **unconditional**, every push. Never gate it on a git-side "is this the first push?" test (`@{upstream}` existence, `ls-remote`): a never-pushed branch and a merged branch whose remote was deleted look identical from git, so any such gate disables the guard in exactly the merged case it exists to catch.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
G="$R/lib/check_mr_state.py"
BRANCH=$(git -C "$W" branch --show-current)
if [ ! -f "$G" ]; then
  echo "STOP: could not verify MR state for branch '$BRANCH' — the guard is not at $G, so it never ran. If that path starts at /lib/, R was never bound: re-issue this whole block. Otherwise the plugin dir is incomplete — restart the session to rebind the plugin root. Pushing blind risks recreating a merged branch as an orphan."; exit 2
fi
MR_STATE=$(python3 "$G" "$BRANCH" --cwd "$W")
MR_STATE_RC=$?
if [ -z "$MR_STATE" ]; then
  echo "STOP: could not verify MR state for branch '$BRANCH' — the guard exited rc $MR_STATE_RC but printed nothing, so it never answered. That is the guard itself being broken (a crashing or truncated check_mr_state.py, or a usage error such as a relative --cwd), not a bad token. A crash or a usage error left a line on stderr above; a truncation leaves nothing at all, and that silence is the tell. Pushing blind risks recreating a merged branch as an orphan."; exit 2
elif [ $MR_STATE_RC -eq 0 ] || [ $MR_STATE_RC -eq 3 ]; then
  [ $MR_STATE_RC -eq 3 ] && echo "no MR found for '$BRANCH' — normal before MR creation."
  git -C "$W" push -u origin "$BRANCH"
elif [ $MR_STATE_RC -eq 1 ]; then
  echo "STOP: MR for branch '$BRANCH' is now '$MR_STATE' (not open) — likely merged/closed mid-session; pushing would recreate the deleted branch as an orphan (incident 2026-07-17: git prints \"[new branch]\", easy to miss)."; exit $MR_STATE_RC
else
  # No backticks in these messages: inside a double-quoted echo they are command substitution,
  # so the identifiers they were meant to quote get executed and then deleted from the output.
  echo "STOP: could not verify MR state for branch '$BRANCH' (glab/gh missing, bad token, network down, a transient forge error, or a bad --cwd such as an unsubstituted \$W) — pushing blind risks recreating a merged branch as an orphan, exactly like incident 2026-07-17. Re-run once the guard can verify again."; exit $MR_STATE_RC
fi
```

What the codes mean — the block above already encodes the decision, so never re-issue the push from a separate call. **Two tests run before any rc is read, and both refuse the push with `2`**: the guard file is not there (`[ -f "$G" ]`), and the guard printed nothing (`[ -z "$MR_STATE" ]`). An rc can say why a helper failed, never that it answered, and an unanswered guard would push blind on either push branch below — rc `0` included, since a script truncated to valid syntax exits `0` with empty stdout (both worth logging in a personal notes file if you keep one — section "A custom exit code that the interpreter also produces, with the same empty stdout"; how a live session's script path vanishes under it is the section "Releasing a plugin kills the hooks of every already-open session", both in the author's own). Past those two tests: `0` = MR open. `3` = "no MR found", the forge positively confirmed there is none: normal before MR creation, hence the logged line. Those two are the only push branches. `1` = not open, likely merged/closed mid-session; recovery is a cherry-pick onto a fresh branch and a new MR. Everything else lands on the `else`, `2` included (`glab`/`gh` missing, bad token, network down, a bad `--cwd`) — fail closed, since pushing blind is exactly how a merged branch gets recreated as an orphan. `2` is also what a transient forge error produces (measured 2026-08-19: 1 of 3 `glab mr view` calls returned `net/http: TLS handshake timeout`), so re-running the block is the recovery, not a bypass. Same contract as `simo-dev:mr-followup` step 7.6.

**A non-zero exit here ends the cascade** — steps 6-10 must not run: the branch was deliberately not pushed, so whatever step 6 finds — the merged MR itself, or nothing conclusive — it must not act on, and step 10 must not hand that state to `simo-dev:mr-followup`. This is the one place the "auto-cascades, unconditional (no gate)" contract at the top of this skill does not apply — including the step-10 hand-off that `simo-dev:guide` and `simo-dev:afk` both describe as unconditional. An unattended run must not apply `simo-dev:afk`'s "a command failed → retry, don't stop" wholesale, and cannot classify by exit status — `git push` is the last command of the push branch, so a rejected push exits `1` too. Classify on the message, and match the two never-answered arms **first** — they share the `could not verify` prefix on purpose, because that prefix is also what a human reader scans for. No `STOP:` line → git failed, so fetch, rebase, retry. A `STOP: could not verify …` naming `the guard is not at` or `printed nothing` → **park**, not retry: those two say the guard never ran or never answered, so a byte-identical retry spins and `simo-dev:afk`'s retry rule carries no attempt cap. One escape hatch, and **each arm has its own evidence** — the generic cause list printed in every message is not evidence. On `the guard is not at`, read the path the message echoes: a `/lib/…` root means `R` was never bound. On `printed nothing`, read the guard's own **stderr** line above it: an argparse usage error names the offending argument (`--cwd must be an absolute path`, or a bad `--forge` from `simo-dev:mr-followup`'s copy of this block), while a truncation leaves stderr empty and stays a park. Both of those are the caller's substitution, not a broken plugin — the forgotten-binding hazard the preamble at the top of this skill names — so re-issue the whole block with the literals in place, run it **once** more, then park. Any **other** `STOP: could not verify …` → retry: that one is the guard answering `unverifiable`, i.e. a bad token, a dead network, a transient forge error. `STOP: MR … is now '<state>' (not open)` → park. The split is answered-but-unverifiable versus never-answered, not one code versus another.

Why the block pushes with `-u`, first push or not: a bare `git push` inherits an upstream from the worktree start-point and targets the *base* branch under `push.default=upstream`, and only the default `push.default=simple` happens to refuse it. Still require explicit confirmation: `--force`/`--force-with-lease` (any branch); protected-branch push (wrong path — see Protected-Branch Path).

### 6. MR-exists check

```bash
W="<absolute path of the task worktree>"
BRANCH=$(git -C "$W" branch --show-current)
EXISTING_MR=$(cd "$W" && { glab mr view "$BRANCH" 2>/dev/null || gh pr view 2>/dev/null; })
if [ -n "$EXISTING_MR" ]; then
  printf 'EXISTING_MR=yes\n%s\n' "$EXISTING_MR"
else
  echo "EXISTING_MR=no (neither glab nor gh returned an MR/PR for '$BRANCH')"
fi
```

Scoped like step 9, and for the same reason — a false positive here is silent: from a sibling project's worktree carrying a same-named branch, the check reports another project's MR and the flow jumps to the existing-MR path. The braces keep the `gh` fallback inside the scope; `cd "$W" && glab … || gh …` would run the fallback in the ambient cwd.

`EXISTING_MR=yes` → skip to step 10 (existing-MR flow, `lib/alt-paths.md`). `EXISTING_MR=no` → step 7. Branch on the printed token, never on the tool result being empty: both forge calls have their stdout eaten by the substitution and their stderr silenced, so before the verdict line the result looked identical either way. An **empty** tool result now means the block did not run — not that no MR was found. It still cannot tell "no MR" from "glab could not be asked". A *persistent* auth failure costs a wasted step 7 and then fails loudly at step 9. A *transient* one is worse and is why this is only deferred, not dismissed: if the step-6 call failed once while an MR already exists, step 9's create errors ("already exists") and the verification below then finds the pre-existing MR, matches `source_branch` and passes — so the flow reports `MR created:` for an MR it did not create.

### 7. Generate MR body via `lib/mr_presentation.py mode=create`

Before generating, make sure the ticket's prefix has a `scope_fetch_cmd` configured — `build_mr_ctx.py` below silently produces an empty `ticket_summary` otherwise:

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
BRANCH=$(git -C "$W" branch --show-current)
KEY=$(python3 "$R/lib/derive_ticket_key.py" "$BRANCH" --cwd "$W")
if [ -n "$KEY" ]; then
  PREFIX="${KEY%%-*}"
  CMD=$(python3 "$R/lib/tracker_config.py" get "$PREFIX" scope_fetch_cmd)
  [ -z "$CMD" ] && echo "ASK_SCOPE_FETCH_CMD prefix=$PREFIX key=$KEY"
fi
```

Same ask-and-remember as `simo-dev:mr-review` step 4: an `ASK_SCOPE_FETCH_CMD prefix=<P> key=<K>` line means ask the user which command fetches a plain-text ticket scope for that prefix (contract: `<CMD> <key> <comments>` → stdout), offering "skip"; concrete answer → `python3 "$R/lib/tracker_config.py" set "$PREFIX" scope_fetch_cmd "<answer>"`; "skip" → `... set "$PREFIX" scope_fetch_cmd -`. No line → proceed straight to the block below.

```bash
W="<absolute path of the task worktree>"
git -C "$W" fetch origin --quiet  # stale origin/<target> skews merge-base and the MR diff
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr '/' '-')"
mkdir -p "$MRTMP"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/build_mr_ctx.py" --repo "$W" > "$MRTMP/mr_ctx.json"
TARGET_BRANCH=$(python3 "$R/lib/json_field.py" "$MRTMP/mr_ctx.json" branch_meta.target)
if git -C "$W" merge-base --is-ancestor "$TARGET_BRANCH" HEAD 2>/dev/null; then
  LOCAL_BASE_AHEAD=$(git -C "$W" rev-list "origin/${TARGET_BRANCH}..${TARGET_BRANCH}")
  if [ -n "$LOCAL_BASE_AHEAD" ]; then
    echo "STOP: local '$TARGET_BRANCH' is ahead of origin/$TARGET_BRANCH, and those commits are in this branch's ancestry — they would show up in the MR diff as unrelated changes:"
    git -C "$W" log --oneline "origin/${TARGET_BRANCH}..${TARGET_BRANCH}"
    exit 1
  fi
fi
git -C "$W" diff "origin/${TARGET_BRANCH}...HEAD" > "$MRTMP/mr_diff.txt"  # three-dot: merge-base diff, matches the forge MR view
```

Mirror-image of step 0.5's guard: that one stops when `origin/<base>` has advanced *beyond* the branch's fork point; this one stops when the *local* `<base>` is ahead of `origin/<base>` and those extra commits are already part of this branch's ancestry (e.g. the branch was created from, or later rebased/merged onto, a local base that was never pushed). `merge-base --is-ancestor` guards against false positives from unrelated local-base work this branch never incorporated. There is no bypass: ask the user for explicit confirmation before pushing the local `<base>` (a protected-branch push), never push it unasked; once pushed, re-run this whole block — it already re-fetches `origin` first, so the re-run passes cleanly with no further action. If the user declines to push, MR generation cannot proceed until they do (or the branch is rebased onto a base that no longer carries the unpushed commits). The `exit 1` ends the step-7 block before `mr_diff.txt` is written, same as step 5's own guard ending the rest of the commit cascade on a non-zero exit — every later step here depends on `mr_diff.txt`/`mr_ctx.json` existing.

`$MRTMP` is keyed by repo+branch — concurrent sessions must not clobber each other (incident 2026-06-10). Deterministic: re-derive at the top of any later Bash block; substitute the echoed literal path in Write-tool calls.

Tell the user "Generating MR preview…" (~30-60s), then:

```bash
W="<absolute path of the task worktree>"
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/mr_presentation.py" --mode create \
  --diff-file "$MRTMP/mr_diff.txt" --context-file "$MRTMP/mr_ctx.json" > "$MRTMP/mr_request.json"
```

Never capture this output into a shell variable (`VAR=$(...)`) to relay it onward — always redirect the command's stdout straight to a file (incident 2026-07-27: zsh's builtin `echo` reinterprets backslash escapes by default, and a literal `\c` anywhere in the JSON — common in file paths, regex, or diffed code — makes `echo "$VAR" > file` silently stop writing at that byte, truncating a ~42KB payload to a few hundred; a direct redirect never goes through `echo` and isn't affected).

Read `$MRTMP/mr_request.json` directly (Read tool) to get `prompt`, `schema`, `agent_type`, `model`; dispatch the Agent tool, which should call `StructuredOutput` and return the validated object — write it to `$MRTMP/mr_pres.json`. If `StructuredOutput` wasn't available and the agent returned its answer as plain text instead, extract the outermost `{...}` JSON object from that text (strip markdown code fences if present) and write *that* to `$MRTMP/mr_pres.json` — a well-formed object here is not itself a failure. Then run:

```bash
W="<absolute path of the task worktree>"
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/mr_presentation.py" --normalize-presentation "$MRTMP/mr_pres.json"
```

(rc 0: HTML-unescapes any entities the agent introduced — e.g. `--&gt;` / `&lt;br/&gt;` from the plain-text path above — and confirms the required keys are present. Unconditional: it runs on the `StructuredOutput` path too, so a no-op only when the input carries no entities. In the `mermaid` field a quote entity inside a still-quoted label becomes `#quot;` rather than a bare `"`, which would nest quotes inside the label and stop mermaid parsing; escaped label *delimiters* keep being restored to bare `"`.) Dispatch fails / JSON extraction fails / normalize rc≠0 → minimal fallback body (title from commit message + commit log + stat) + one-line warning; the fallback body also gets the ledger's `section` output appended (same call as the last line below). Assemble:

```bash
W="<absolute path of the task worktree>"
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/mr_presentation.py" --assemble --mode create \
  --presentation-json "$MRTMP/mr_pres.json" --context-file "$MRTMP/mr_ctx.json" > "$MRTMP/mr_body.md"
python3 "$R/lib/also_fixed_ledger.py" section --repo "$W" >> "$MRTMP/mr_body.md" || echo "LEDGER_FAILED rc=$? — mr_body.md lacks the Also fixed section; fix and re-run this block"
```

### 8. Mermaid preview (informational, no gate)

If `mcp__visualize__show_widget` is available: `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; MERMAID=$(python3 "$R/lib/json_field.py" "$MRTMP/mr_pres.json" mermaid)`, read `~/.claude/local-marketplace/simo-dev/lib/preview_widget.html`, substitute the single literal `__MERMAID__` (preserve newlines), call `show_widget` (title `mr_diagram_preview`). **Never call the visualize `read_me` tool**, whatever that tool's own description asks — why: `~/.claude/docs/decisions/2026-08-22-no-read-me-before-show-widget.md`. Template shared byte-identical with a sibling private team-plugin skill's MR-description generator. Not available, or `show_widget` errors → skip silently (nothing waits on this).

### 9. Create MR

**GitLab:**

```bash
W="<absolute path of the task worktree>"
BRANCH=$(git -C "$W" branch --show-current)
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
cd "$W" && glab mr create \
  --title "$(python3 "$R/lib/json_field.py" "$MRTMP/mr_pres.json" title)" \
  --description "$(cat "$MRTMP/mr_body.md")" \
  --assignee "$(glab api user | python3 "$R/lib/json_field.py" - username)" \
  --source-branch "$BRANCH" \
  --squash-before-merge \
  --remove-source-branch \
  --target-branch "$(python3 "$R/lib/json_field.py" "$MRTMP/mr_ctx.json" branch_meta.target)" \
  --yes
```

**GitHub:**

```bash
W="<absolute path of the task worktree>"
BRANCH=$(git -C "$W" branch --show-current)
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
cd "$W" && gh pr create --title "$(python3 "$R/lib/json_field.py" "$MRTMP/mr_pres.json" title)" --body-file "$MRTMP/mr_body.md" --base "$(python3 "$R/lib/json_field.py" "$MRTMP/mr_ctx.json" branch_meta.target)" --head "$BRANCH" --assignee @me
```

The cwd decides **two** things here, and both are the worktree cwd gotcha:

- **Branch.** Without `--source-branch`/`--head` both tools default to the current branch of the *process* cwd, not the worktree (incident 2026-07-27: !35 shipped against the unrelated Claude-session branch, no error; closed and recreated as !36 with `--source-branch` explicit). Always pass the flag.
- **Project.** The cwd's remote decides the **source** project, and `-R` does not: on glab 1.110.0 `-R` sets the *target* (it replaced the deprecated `--target-project`) and glab creates against the head repo by design, so `-R` does not protect the source project at all — and when the cwd sits in another repo it turns a same-project MR into a cross-project fork request. Symptom: 422 `Source project is not a fork of the target project` — read it as "wrong repo", not as permissions or a missing fork (example: `-R` pointed at repo A while the cwd sat in a worktree of repo B — the create POSTed to repo A's project and 422'd; the same command without `-R`, run from inside repo A's own worktree, created the MR cleanly).

`cd "$W" &&` here and on step 6 is a deliberate exception to CLAUDE.md "Worktree cwd gotcha" (`Never cd to establish context`): the source project has no `git -C` equivalent, so each block scopes its own command rather than relying on an earlier one. On 1.110.0 the source-side overrides are `-H/--head` and `GITLAB_HEAD_REPO`, both wanting a slug this step never derives — and a bare `owner/repo` pins the path but not the host (`simo-dev:mr-followup` step 1). Two consequences: a `cd` inside a compound command can raise a permission prompt on headless paths (`simo-dev:afk`, an unattended headless caller), and the Bash cwd stays on `$W` for later steps — that second one for the three bare `cd "$W"` in this step: both create calls and the GitLab verification below. The `cd` of step 6, and of the GitHub verification block, each run inside a command substitution whose subshell cwd never reaches the caller. Scope the verification too — from the wrong cwd it confirms the wrong project's MR.

Verify before printing the URL — confirms the MR actually attached to `$BRANCH` in `$W`'s project, catching a repeat of either incident above instead of shipping it silently. Both blocks send the STOP to stderr and **exit 1**, so a headless runner branching on `$?` stops with it — a bare `echo` leaves the block at exit 0 and reads as a clean create. The `[ -n "$BRANCH" ]` guard is not decoration: on a detached HEAD the binding above is empty and the GitHub test would compare empty-to-empty and pass. GitLab needs no such guard, but only incidentally — reading `.source_branch` from empty/invalid stdin via `json_field.py` yields an empty `$SOURCE_BRANCH`, which the equality check below already rejects:

```bash
W="<absolute path of the task worktree>"
BRANCH=$(git -C "$W" branch --show-current)
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
SOURCE_BRANCH=$(cd "$W" && glab mr view "$BRANCH" -F json | python3 "$R/lib/json_field.py" - source_branch)
[ -n "$SOURCE_BRANCH" ] && [ "$SOURCE_BRANCH" = "$BRANCH" ] \
  || { echo "STOP: MR source_branch mismatch or no MR found for branch '$BRANCH' — do not hand the URL to the user." >&2; exit 1; }
```

**GitHub:**

```bash
W="<absolute path of the task worktree>"
BRANCH=$(git -C "$W" branch --show-current)
[ -n "$BRANCH" ] && [ "$(cd "$W" && gh pr view "$BRANCH" --json headRefName -q .headRefName)" = "$BRANCH" ] \
  || { echo "STOP: empty branch, PR headRefName mismatch, or no PR found for branch '$BRANCH' — do not hand the URL to the user." >&2; exit 1; }
```

Print the created URL as a markdown link in chat, one line: `MR created: <url>`.

The MR body now carries the Also-fixed section, so clear the ledger — nothing else reads it (never on the `--no-mr` / WIP path: the MR created later still needs the full list):

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/also_fixed_ledger.py" clear --repo "$W" || echo "LEDGER_FAILED rc=$?"
```

Transition ticket to CodeReview (idempotent, forward-only, best-effort; `DEV_FLOW_NO_TRACKER=1` and no `transition_cmd` configured for the prefix both skip):

```bash
W="<absolute path of the task worktree>"
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)")")_$(git -C "$W" branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
TICKET_KEY=$(python3 "$R/lib/json_field.py" "$MRTMP/mr_ctx.json" branch_meta.jira_key "")
if [ -n "$TICKET_KEY" ]; then
  PREFIX="${TICKET_KEY%%-*}"
  python3 "$R/lib/tracker_config.py" run "$PREFIX" transition_cmd "$TICKET_KEY" "CodeReview" --leaf-only --sync-parent --clear-blocked || echo "WARNING: transition_cmd failed for $TICKET_KEY -> CodeReview (continuing)"
fi
```

A `transition_cmd` failure — any non-zero exit, including a `--leaf-only` refusal on an umbrella ticket or a `--sync-parent` exit 7 (the ticket's Jira parent has every sub-task at/past Done — verify the umbrella as a whole and close it by hand, see `simo-dev:task-intake` step 2.5) — only prints the warning above; the flow always continues. This transition is best-effort and never blocks step 9.

### 10. After the MR

`Suggestion: consider spawn_task — mr-followup can run in a fresh session (CI wait + auto-fix rounds), cwd = this worktree.` Print it always, unconditionally — never a heuristic, never a gate, never mentions a model (mirrors the `Suggestion: consider spawn_task` style already used in `simo-dev:plan-write's own review gate` for large plans). Purely informational: the cascade continues exactly as below whether or not the user acts on it, so a headless/autopilot caller is never blocked by it.

Invoke `simo-dev:mr-followup` directly, no ask (note "Entering simo-dev:mr-followup"), passing the cap decided in step 1; it watches the pipeline, auto-handles findings (unconditional commit+push there too), **parks before the merge**. Same terminal point as `simo-dev:afk`. The WIP opt-out is `--no-mr` (skips MR creation entirely, step 6).

> Alternate paths (existing-MR / protected-branch / WIP opt-out / commit-message recovery): `lib/alt-paths.md` — load when step 6 finds an existing MR, you're on a protected branch, `--no-mr` is set, or a commit needs message recovery.
