---
name: commit
description: Use for every commit while the simo-dev flow is active — phrases: "commit", "save the work" — never a direct `git commit` there; outside the flow, only when the user asks for it by name. Auto-cascades: knowledge harvest → review-loop pre-flight → commit → push → MR body generation → MR creation → mr-followup, unconditional (no gate), parking before the merge. Opt-out for WIP commits: `--no-mr` arg or `DEV_COMMIT_NO_MR=1`. Optional `--target <branch>` overrides the MR target and the review-loop base (default: an auto-detected stacked parent branch, else the repo default branch). Optional `--draft` / `--no-draft` opens the MR/PR as Draft or forces it not to (default: Draft only when a plan the branch adds declares `**MR draft:** yes`). On protected branches (master/main/develop — out-of-flow fallback) the cascade is replaced by mermaid preview + a single commit+push confirmation, no MR. Optional ticket scope arg. Fourth step of the canonical task flow.
---

# Dev Commit

Fourth step of the canonical task flow:
`simo-dev:task-intake → simo-dev:plan-execute → simo-dev:review-loop → simo-dev:commit → simo-dev:mr-followup`

Arguments: optional ticket ID (commit scope); optional `--no-mr` flag (commit + push only); optional `--target <branch>` (MR target branch and base for steps 0.5/7/9, passed on to review-loop at step 1 and recorded for mr-followup at step 7; omitted → a stacked parent branch is auto-detected, else the repo default branch); optional `--draft` (create the MR/PR as Draft — also set by a plan's `**MR draft:** yes` header, see `simo-dev:plan-write`) or `--no-draft` (force a non-Draft MR and skip the plan check — the way out when step 7's WARNING names another task's plan); both omitted → unchanged, both passed on to review-loop at step 1.

MR creation is gate-free, same as push to the feature branch. The merge (the one hard gate left in the publish path) lives outside this skill, run manually by the user after `simo-dev:mr-followup` parks.

**Protected-branch detection (before step 4):** current branch is `master`/`main`/`develop` (or the repo default) → steps 0.7-3 run unchanged (except step 3's Also-fixed ledger block, skipped there), then switch to the **Protected-Branch Path** (`lib/alt-paths.md`). Steps 4-10 are replaced.

## Steps

**Every Bash block below that touches git re-binds `W="<absolute path of the task worktree>"` as its first line, and every block that runs a plugin script re-binds `R` the same way.** Shell state does not survive between Bash calls, so a `W` or `R` bound in an earlier step is already gone; and the Bash cwd does NOT follow the worktree, so a bare `git` resolves whichever repo the session happens to sit in. `R` resolves the released cache dir when the harness substituted one that still exists, and otherwise the source tree, or — on an install that has none, such as the public export — the highest simo-dev version under `~/.claude/plugins/cache/`. A forgotten binding leaves `"$R/lib/…"` as an absolute `/lib/…` path: of the sites in this file, the `python3` calls exit rc `2`; the two that assign `G` convert that same state into a STOP from their own `[ -f "$G" ]` test — step 0.5's `check_base_freshness.py` at rc `4`, step 5's `check_mr_state.py` at rc `2`. Both echo the resolved path, and both read a `/lib/…` root as "re-issue this block" rather than as a broken plugin. Bind it in every block. Step 1 certifying coverage for `$W` while step 4 stages the *cwd* repo's diff is precisely the unreviewed-commit outcome this guard exists to prevent (measured 2026-08-04).

### 0. mark_chapter

When `mcp__ccd_session__mark_chapter` is not yet callable (listed only as deferred, or not listed at all), run `ToolSearch` `select:mcp__ccd_session__mark_chapter` once first, never skipped because the name is unlisted. Callable: invoke it as the first action (after the load, if one ran), title `Ship: <JIRA-KEY or task gist>`, summary `Commit + push + MR create flow`. No schema, or no `ToolSearch` (not available) → skip silently, proceed straight to step 0.5.

### 0.5. Base-freshness guard (feature branches only)

Skip on protected branches (they take the Protected-Branch Path). Otherwise run, before wasting reviewer budget:

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
G="$R/lib/check_base_freshness.py"
[ -f "$G" ] || { echo "STOP: guard unreachable at $G"; exit 4; }
git -C "$W" fetch origin --prune --quiet
TARGET_ARG="<the --target skill arg, or empty>"
BASE=$(python3 "$R/lib/build_mr_ctx.py" --repo "$W" --target "$TARGET_ARG" --print-target) || { echo "STOP: could not resolve the base branch (stderr above)." >&2; exit 5; }
python3 "$G" --repo "$W" --base "$BASE"
```

`<base>` below is the same target step 7 will use (`--target` arg → stacked parent → repo default), so a stacked branch is checked against, and on rc `1` rebased onto, its parent rather than the default branch. An empty `BASE` (no `origin/HEAD`) makes the guard fall back to `origin/main|master`. rc `5` is the `BASE=` line's own STOP, not the guard's — never "recover as below"; it follows step 7's `STOP: target:` handling (**MR target**). A `note:` printed by the `BASE=` line names a remote branch that shares commits with this one but diverged (a parent that advanced after the fork, or a branch stacked on this one): relay it, and ask here — the one place it is asked — whether this branch is stacked on the named branch; a yes sets `TARGET_ARG` to it for the rest of the run, step 7 included, and re-runs this whole block with it set — the guard above ran against the default branch, so act on the re-run's rc, not the first one's. If the guard then returns rc `1`, ask that question BEFORE the park: parking onto the default branch rewrites the parent's commits into this branch. Under `simo-dev:afk`, a `note:` followed by guard rc `1` stops (no park) and is recorded in the afk summary; a `note:` with guard rc `0` is recorded and the run continues.

Deterministic stale-base check (incidents 2026-07-24): STOPs only when `origin/<base>` advanced beyond the branch's fork point **and** the advanced commits touch files this branch also changes — the branch would revert merged work. rc `1` → **stop**, surface the printed overlap + guidance, do not proceed, then recover as below. rc `0` (safe / benign disjoint advance) or `2` (git error — never a false stop) → continue. rc `4` → **stop**, nothing was checked: the resolved path does not exist. `R` resolves the released cache dir while that directory exists, and a released snapshot has not been observed half-written, so the rc `1` reading from a file caught mid-write is not expected from there — it returns once the cache dir is gone and `R` has fallen back to the source working tree (an install with no source tree falls back to another released cache dir instead). One carve-out, from `docs/plugin-release.md`: a release run from a worktree leaves the claimed cache dir holding *worktree* content, with no version mismatch to signal it, so just after one of those the cache is not a clean snapshot either. rc `4` means the root `R` actually resolved to lacks `lib/check_base_freshness.py` — nothing more: once `[ -d "$R" ]` succeeds the fallback tree is never consulted, so a present-but-incomplete cache dir reaches rc `4` with the source tree intact beside it. What no longer reaches it is a *deleted* cache dir, which `[ -d "$R" ]` routes past (worth logging in a personal notes file if you keep one — section "Releasing a plugin kills the hooks of every already-open session" in the author's own, by title since sections get renumbered). Read the path the message echoes before acting on it: a `/lib/…` root means the `R=` line above was omitted from the block you ran, so re-issue the whole block rather than restarting — restarting rebinds nothing a re-issue would not. Any other root is the plugin dir itself being incomplete, and there restart the session: this step lives in a rendered body, which already carries the substituted path as literal text before any shell runs, so unsetting the variable changes nothing here. Without the `[ -f "$G" ]` test that same state returns rc `2`, which this step reads as continue.

**rc `1` recovery** (park the work, rebase, put it back; announce the park, never park silently): read `lib/failure-paths.md` "Step 0.5 rc 1: park, rebase, restore" and follow it, `park`/`restore` exit codes included.

### 0.7. Update documentation and memories

Runs before step 1, so the pre-flight's mtime-based check covers whatever it writes. Change
touched behavior/conventions → update relevant docs. Then run the full three-question knowledge
harvest from `lib/knowledge-harvest.md` unconditionally — this is the primary pass, and lands
harvested docs in the same commit/MR (skip terms `simo-dev:grill-me` already captured this
session, per that file's dedup framing). Accepted cost: when this step writes something, step 1
runs a second whole-diff `review-loop` (and its tracker summary comment) even if one just ran.
On the Protected-Branch Path and the `--no-mr`/WIP opt-out path this is also the *only* pass:
both skip `mr-followup` entirely (`lib/alt-paths.md`), so lessons from a step-1 `review-loop`
run are not harvested there (accepted gap; the feature-branch path catches them in
`mr-followup`'s catch-up).

### 1. Review-loop pre-flight

Deterministic check — a review already covers the files this commit will stage → skip. Nothing is staged yet at this point, so the list is working-tree modifications **plus untracked files** (step 0.7's harvest writes included): step 4 stages untracked files too, and omitting them lets a task whose whole diff is one new file pass the check vacuously.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
T=$(mktemp /tmp/claude_commit_files.XXXXXX) || exit 1
python3 "$R/lib/changed_files.py" --repo "$W" > "$T"; rc=$?
FILES=()
while IFS= read -r -d '' f; do [ -n "$f" ] && FILES+=("$f"); done < "$T"
rm -f "$T"
if [ "${rc:-9}" -ne 0 ]; then echo "LISTING_FAILED rc=${rc:-9}"; exit 2; else
python3 "$R/lib/review_marker.py" check --repo "$W" \
  --session "$CLAUDE_CODE_SESSION_ID" \
  --expect-skill-log simo-dev:commit \
  --files "${FILES[@]}"
fi
```

`CLAUDE_CODE_SESSION_ID` holds the same value as the hook payload's `session_id`, so this reads the record written by the reviewer dispatched in **this** session. Passed unconditionally, not as `${VAR:+--session "$VAR"}`: the agent's shell is zsh, which does not word-split that expansion and would hand argparse one bogus argument. Unset or empty is safe anyway — no stored record can carry an empty session id, so it cannot match. `$W` is bound once and reused for `--repo` and for the listing call: deriving them separately from the process cwd let them resolve a *different* repo than the one being committed, which returned rc `0` on an empty file list and vouched for a repo nobody had reviewed (measured 2026-08-04). Same convention as `lib/build_mr_ctx.py` and `lib/check_base_freshness.py` — pass the worktree absolute path, never rely on the cwd. Both listings in this step come from `lib/changed_files.py`, the helper `hooks/review_loop_warn.py` uses too, and it only reads: porcelain `git diff` refreshes a stat-dirty entry and writes `.git/index` back through `index.lock` whatever `GIT_OPTIONAL_LOCKS` says (git 2.55.0, measured 2026-10-06), and in a shared checkout that lock collides with another session's `git add`/`commit`. The listing goes through a temp file, not `< <(...)`: a process substitution drops the helper's exit status, and an empty `FILES` makes `check()` return rc `0` — the review skipped on a listing that never ran. A failed listing prints `LISTING_FAILED` and exits rc `2` without calling `check()`, so the "every other non-zero rc" clause below covers it (`2`, not `3`: `3` triggers the skill-log retry). The list is read line by line into an array rather than passed as `--files $(git …)`: zsh field-splits an unquoted command substitution, so a changed `two words.txt` arrived as two arguments, both of which miss on disk — `check()` then had nothing left to compare and returned rc `0` for a file written after the review (measured 2026-08-04). The helper emits NUL-terminated paths (`-z` on both of its git listings), with `read -r -d ''` to match, for the same reason: git otherwise C-quotes a path holding a tab, a carriage return, a double quote, a backslash or a non-ASCII byte (`"caf\303\251.txt"`, quotes included), and that literal is neither on disk nor in the deletion set `_tracked_deletions` builds, so `check()` reports the list unusable and the repo can never be certified. `core.quotePath=false`, the earlier form, is not enough — it lifts quoting for non-ASCII bytes alone, where `-z` suppresses it outright: of six probes on git 2.55.0 the tab, carriage return, double quote and backslash still came back quoted from the non-`-z` listing, the space and the non-ASCII byte did not (measured 2026-08-16 — a sample, not the closed set). zsh 5.9, the agent's shell, honours `read -r -d ''` as a NUL delimiter (measured 2026-08-18). `--expect-skill-log simo-dev:commit` enables a liveness probe on both record-related failing paths — this skill's own hook entry missing from `~/.claude/state/dev-flow-skills.log` while **this session** logged other simo-dev skills fits two readings: this skill was never invoked in this session, or the hooks are not firing here. rc `0` → skip. rc `3` → invoke the `simo-dev:commit` Skill if this check was reached outside one — and **when in doubt, invoke it**: an agent running this body without a Skill-tool invocation is both the case the retry exists for and the case least able to tell, so the condition falls to the invoking side. `check` is read-only and the retry is bounded at one, so a needless invocation costs one Skill call, while skipping it reproduces the false stop. Then re-run the block **once**. One retry settles it because `PostToolUse` on `Skill` fires when the Skill tool returns: inside a real invocation the entry already exists and the retry can only confirm, so the answer changes exactly in the case where the pre-flight was reached without that invocation. rc `3` a second time → **stop** and act on the printed message, which names each observed cause with its own remedy; do not invoke `simo-dev:review-loop` there, which cannot write the marker while the hook that writes it is dead. Every other non-zero rc (reason printed) → invoke `simo-dev:review-loop` automatically (no gate prompt), passing `--target <TARGET_ARG>` when step 0.5 settled a non-empty `TARGET_ARG`, so review and MR share one base, and `--draft` / `--no-draft` when this skill received one, so review-loop's "(1) skip → `simo-dev:commit`" re-invocation keeps it. Review-loop fails or leaves unresolved findings → stop, let the user decide.

**Decide the review cap here, on both paths.** Judge it by the criterion in `simo-dev:review-loop` step 1.5 — prose with no executable content. Do not restate it here as an extension test: extensions are what step 1.5 abandoned. In this plugin the skill bodies this flow edits most are dense with runnable blocks, this file included (10 `bash` blocks, `mr-followup` 18, `task-intake` 20 — measured 2026-11-25, indented fences included), so the test is whether any changed line falls inside a fenced `bash`/`sh`/`python` block, in any file, whether or not the diff adds the fence itself. Re-derive the file list in this block rather than reading `FILES` from the previous one — shell state does not survive between Bash calls:

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
T=$(mktemp /tmp/claude_commit_files.XXXXXX) || exit 1
python3 "$R/lib/changed_files.py" --repo "$W" > "$T" || echo "LISTING_FAILED rc=$? — the list below is NOT empty-by-design"
tr '\0' '\n' < "$T"; rm -f "$T"
```

The value is needed twice — on the review-loop invoke above when the pre-flight did not skip, and at step 10's `simo-dev:mr-followup` invoke, which runs whether or not it skipped. Deciding it inside the non-zero branch left the dominant skip path with no value at all.

### 3. Compute commit message

Conventional format. Ticket-in-context: (a) skill arg matches `^[A-Z][A-Z0-9]+-[0-9]+$`, OR (b) `W="<absolute path of the task worktree>"; R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"; python3 "$R/lib/derive_ticket_key.py" "$(git -C "$W" branch --show-current)" --cwd "$W"` — both bindings spelled out rather than left to the block convention above, because either one missing fails quietly: an unbound `R` exits rc 2 with **empty stdout** and an unbound `W` makes `git -C ""` read the *ambient* repo at rc 0, and this step reads both as "no ticket in context" and commits an unscoped message (honors `DEV_FLOW_NO_TRACKER` / `.claude/no-tracker`; anchored match). A ticket key present → `<type>(<KEY>): <subject>`, else `<type>: <subject>`. `<type>` from the diff: `feat|fix|refactor|docs|test|chore`.

In-scope improvements (`docs/canonical-flow.md` "In-scope improvements"): the pending Also-fixed ledger lines go in the message body, one per line — except a line whose file does not appear in `git -C "$W" status --porcelain --untracked-files=all` (the changes step 4 is about to stage; nothing is staged yet here; `-uall` because the default collapses a wholly-new directory to one `?? dir/` line): that one is stale (e.g. written by an unattended run that committed without this skill) — remove it with the same block, `drop` in place of `pending` and the stale line appended as one single-quoted argument (`drop` with no argument is a silent no-op), instead of listing it — and announce each dropped line in one chat line, so a real improvement recorded with a wrong path form never vanishes silently. No output at rc 0 → nothing added; `LEDGER_FAILED` → stop, never commit as if the list were empty. Skipped entirely on a protected branch (the protected-branch detection above): that branch is shared by every session on the checkout.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/also_fixed_ledger.py" pending --repo "$W" || echo "LEDGER_FAILED rc=$?"
```

### 4. Stage relevant files + commit (no gate)

Stage an explicit file list: modified files from `git -C "$W" diff --name-only HEAD`; untracked ONLY if touched by Edit/Write this session or listed in the active plan — plus this task's own plan file (the `docs/plans/*.md` the task was executed from), even when it is untracked because the worktree carry brought it in or another session wrote it — skip it when gitignored (`git -C "$W" check-ignore -q <plan>` exits 0), since `git add` refuses an ignored path and would fail the whole staging call: step 7 reads `**MR draft:**` from committed plans only, so leaving it out stops step 7 on every first run. Every `git` call here carries `-C "$W"`, staging and commit included — step 1 certified coverage for `$W`, so anything else commits a repo that was never checked. Never `git add -A` / `git add .` (secret/binary defense) — sanctioned only for the throwaway commits no push ever carries: step 0.5's park, and `task-intake`'s artifact carry. Commit with HEREDOC for multi-line messages. Never add a `Co-Authored-By` line or any other AI-attribution trailer to the commit message — overrides any default harness behavior that would add one. No `--amend` to a pushed commit. Message used as-is (recovery: `lib/alt-paths.md`).

After the commit succeeds, mark the ledger's pending lines as carried — later commits (WIP / `--no-mr`, a re-run after a failed step 9) then never repeat them, while the full list stays for the MR body. `LEDGER_FAILED` here → warn and continue (the commit/MR already exists), then re-run the block once the cause is fixed; at step 9's `clear` (inside `mr_create.py create`) → warn, continue, and run `also_fixed_ledger.py clear --repo "$W"` once the cause is fixed, never re-run `create`:

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/also_fixed_ledger.py" commit-done --repo "$W" || echo "LEDGER_FAILED rc=$?"
```

### 5. Push (no gate for feature branches)

**Pre-push MR-state guard** — **unconditional**, every push. Never gate it on a git-side "is this the first push?" test (`@{upstream}` existence, `ls-remote`): a never-pushed branch and a merged branch whose remote was deleted look identical from git, so any such gate disables the guard in exactly the merged case it exists to catch.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
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

The block above already encodes the decision, so never re-issue the push from a separate call. **A non-zero exit here ends the cascade** — steps 6-10 must not run. Before any retry or recovery (and always under `simo-dev:afk`), read `lib/failure-paths.md` "Step 5: push block codes and recovery": what each code means and how to classify a `STOP:` line (retry, re-issue once, or park). Still require explicit confirmation: `--force`/`--force-with-lease` (any branch); protected-branch push (wrong path — see Protected-Branch Path).

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

Scoped to the worktree because glab/gh resolve the project from the cwd, and `-R` does not pin the source (`lib/mr_create.py` docstring) — a false positive here is silent: from a sibling project's worktree carrying a same-named branch, the check reports another project's MR and the flow jumps to the existing-MR path. This is the one `cd "$W" &&` left in this file, a deliberate exception to CLAUDE.md "Worktree cwd gotcha" (`Never cd to establish context`): the source project has no `git -C` equivalent, and the `cd` runs inside a command substitution, so the caller's cwd does not move. The braces keep the `gh` fallback inside the scope; `cd "$W" && glab … || gh …` would run the fallback in the ambient cwd.

`EXISTING_MR=yes` → skip to step 10 (existing-MR flow, `lib/alt-paths.md`). `EXISTING_MR=no` → step 7. Branch on the printed token, never on the tool result being empty: both forge calls have their stdout eaten by the substitution and their stderr silenced, so before the verdict line the result looked identical either way. An **empty** tool result now means the block did not run — not that no MR was found. It still cannot tell "no MR" from "glab could not be asked". A *persistent* auth failure costs a wasted step 7 and then fails loudly at step 9. A *transient* one is worse and is why this is only deferred, not dismissed: if the step-6 call failed once while an MR already exists, step 9's create errors ("already exists"). Step 9 routes that failed create back to this step rather than on to its verification, which would find the pre-existing MR, match `source_branch`, pass, and report `MR created:` for an MR it did not create.

### 7. Generate MR body via `lib/mr_create.py`

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
TARGET_ARG="<the --target skill arg, or empty>"
DRAFT_ARG="<1 if the --draft skill arg was given, 0 if --no-draft was given, else empty>"
python3 "$R/lib/mr_create.py" prepare --repo "$W" --target "$TARGET_ARG" --draft "$DRAFT_ARG"
```

Run the block above. Its stdout is `key: value` lines. rc 0 → continue; rc 3 → ask (below); rc 1 → stop (below); rc 2 → `R` unbound or the `DRAFT_ARG` placeholder left in: re-issue it with the literals (afk: once, then park). A leftover `W` placeholder surfaces as rc 1 `STOP: --repo must be …` instead.

`ASK: scope_fetch_cmd prefix=<P> key=<K>` (rc 3, nothing written) → same ask-and-remember as `simo-dev:mr-review` step 4: ask which command fetches a plain-text ticket scope for that prefix (contract: `<CMD> <key> <comments>` → stdout), offering "skip"; concrete answer → `python3 "$R/lib/tracker_config.py" set <P> scope_fetch_cmd "<answer>"` ("skip" → value `-`); then re-run the prepare block. Under `simo-dev:afk` the answer is "skip": run `python3 "$R/lib/tracker_config.py" set <P> scope_fetch_cmd -`, re-run the prepare block, record it in the afk summary.

`mrtmp:` is the scratch-dir literal: substitute it for `<mrtmp>` in Write-tool calls and in step 8.

**MR target.** Relay `target:` / `target_source:` as `MR target: <target> (<target_source>)` and any `note:` line. A `note:` here was already asked about at step 0.5 — never ask twice (afk: record it in the afk summary, continue with the default target). The order is `--target`, else stacked parent, else repo default; steps 0.5 and 9 use the same one. `STOP: target: …` → ask the user which branch and re-run the block with `TARGET_ARG` set (afk: stop, record it, never guess) — except a `STOP: target:` quoting `<the --target skill arg`: the placeholder was left in, so re-issue the block with the literal, do not ask.

`draft:` → relay as `MR draft: <0|1>` (`--draft`/`--no-draft` win, else a committed plan's `**MR draft:**` header).

Any other `STOP:` ends the cascade: `STOP: local base ahead` → read `lib/failure-paths.md` "Step 7: local base ahead of origin" (no bypass, never push the local base unasked); every other one → "Step 7: other prepare stops".

Tell the user "Generating MR preview…" (~30-60s). `request:` present. Never Read `mr_request.json` in this session: it carries the whole diff, which would stay in context to the end of the task. Dispatch the Agent tool with `subagent_type` = the `agent_type:` value, `model` = the `model:` value, and this short prompt, the `request:` path substituted as a literal: "Read `<request path>` with the Read tool, the whole file. Its `prompt` field is your task: follow it exactly. Its `schema` field is the JSON schema of your answer: call `StructuredOutput` with an object that validates against it; without that tool, reply with that JSON object only." Append this line verbatim at dispatch (never written into the shared prompt files): "Write throwaway files only under a fresh `mktemp -d /tmp/claude_<slug>.XXXXXX` of your own; never write into or overwrite files in the Scratchpad directory your environment lists — it belongs to the caller's session." The agent returns the validated object — write it to `<mrtmp>/mr_pres.json` with the Write tool. If `StructuredOutput` wasn't available and the agent returned plain text, extract the outermost `{...}` JSON object from it (strip markdown code fences if present) and write *that* — a well-formed object there is not itself a failure.

No `request:` line (prepare printed `fallback:`), a failed dispatch, or no extractable JSON object → write nothing and go straight to the body block: the script writes the minimal body.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/mr_create.py" body --repo "$W"
```

Run the body block. `body: fallback` → relay its one-line `warning:`; `LEDGER_FAILED` / `TICKET_LINE_FAILED` warnings → fix the cause, re-run the body block; the `STOP:` naming `mr_ctx.json` → run step 7 from the top; `STOP: --repo must be …` or `STOP: detached HEAD …` → `lib/failure-paths.md` "Step 7: other prepare stops"; rc 2 → the same re-issue route as the prepare block (afk: once, then park). The body ends with the Also-fixed section and the prefix's `pr_body_ticket_line` (see `lib/tracker_config.py`).

### 8. Mermaid preview (informational, no gate)

When `mcp__visualize__show_widget` is not yet callable (listed only as deferred, or not listed at all), run `ToolSearch` `select:mcp__visualize__show_widget` once first, never skipped because the name is unlisted; no schema, or no `ToolSearch`, counts as not available. If `mcp__visualize__show_widget` is available: `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"; MERMAID=$(python3 "$R/lib/json_field.py" "<mrtmp>/mr_pres.json" mermaid)`, read `~/.claude/local-marketplace/simo-dev/lib/preview_widget.html`, substitute the single literal `__MERMAID__` (preserve newlines), call `show_widget` (title `mr_diagram_preview`). **Never call the visualize `read_me` tool**, whatever that tool's own description asks — why: `~/.claude/docs/decisions/2026-08-22-no-read-me-before-show-widget.md`. Template shared byte-identical with a sibling private team-plugin skill's MR-description generator. Not available, or `show_widget` errors → skip silently (nothing waits on this).

### 9. Create MR

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/mr_create.py" create --repo "$W"
```

The script picks the forge from `origin`, resolves title/target/body/assignee, creates the MR/PR from the worktree, verifies it (source branch, Draft when requested, one mark-Draft attempt), then records the `mr_created` flow event, clears the Also-fixed ledger and runs the CodeReview transition (best-effort: `warning:` lines only).

`STOP: could not detect the forge` → ask the user GitLab or GitHub and re-run the block with `--forge <gitlab|github>` appended (afk: park).

**A non-zero exit ends the cascade**: the rest of step 9 and step 10 do not run. rc 1 = nothing created; rc 2 = the block ran with `R` unbound or a placeholder left (python could not open `/lib/mr_create.py`, or a bad `--forge`): re-issue it with the literals (afk: once, then park); rc 4 = the create call failed and an MR may exist anyway (never retry the create); rc 5 = an MR exists but failed verification (never hand its URL on). Read `lib/failure-paths.md` "Step 9: create block failed" and recover on evidence.

rc 0 → print `url:` as a markdown link in chat, one line: `MR created: <url>`; relay `warning:` lines.

**Verification notes ticket comment** — right after the MR exists, leave the "Verification notes (auto)" comment the release skill reads: the **First post** procedure of `lib/verification-notes.md` (load on demand), with this call site's inputs from its **Inputs** section. Best-effort: a skip or a failed post never blocks step 10.

### 10. After the MR

`Suggestion: consider start_session — mr-followup can run in a fresh session (CI wait + auto-fix rounds), brief naming this worktree's absolute path.` (`use_worktree: false` shares only the session's own folder — this worktree only when the session itself sits in it, a desktop-app tree.) Print it always, unconditionally — never a heuristic, never a gate, never mentions a model. Purely informational: the cascade continues exactly as below whether or not the user acts on it, so a headless caller is never blocked by it.

Invoke `simo-dev:mr-followup` directly, no ask (note "Entering simo-dev:mr-followup"), passing the cap decided in step 1; it watches the pipeline, auto-handles findings (unconditional commit+push there too), **parks before the merge**. Same terminal point as `simo-dev:afk`. The WIP opt-out is `--no-mr` (skips MR creation entirely, step 6).

> Alternate paths (existing-MR / protected-branch / WIP opt-out / commit-message recovery): `lib/alt-paths.md` — load when step 6 finds an existing MR, you're on a protected branch, `--no-mr` is set, or a commit needs message recovery.
