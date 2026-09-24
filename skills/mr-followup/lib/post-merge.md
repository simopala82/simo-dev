# Post-merge procedure — verification notes, cleanup, journal

Loaded on demand from `mr-followup` steps 8 / 8.5, once the user has confirmed the merge.

## Verification notes ticket comment

One comment on the ticket, posted directly (no confirmation), in English (repo-artifact convention). Skip silently when there is no ticket key (`DEV_FLOW_NO_TRACKER` / `.claude/no-tracker`). Idempotent: an existing `Verification notes (auto)` comment mentioning this MR → skip (safe on resume).

Content — terse and actionable for a context-free release-time agent (it has no session context, only the ticket):

```
h4. Verification notes (auto)
* MR: <url>
* Change: <one-paragraph summary of the merged change>
* QA check: <concrete steps — endpoints to call, jobs to trigger, expected vs previous behavior>
* PROD check: <prod equivalents — base URL / ArgoCD app names where they differ, else "same as QA">
* Pre-merge QA gate: <"passed <date>" if step 7.5 ran clean this session, else omit this line>
```

Write the body to a temp file, then post it through the shared tracker comment mechanism — NOT a direct CLI call and NOT a positional/heredoc body (a positional/heredoc invocation of the underlying comment-posting CLI hung 15+ minutes with no output when backgrounded, no TTY present — observed 2026-08-25):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/post_tracker_comment.py" "<KEY>" "<tmpfile>"
```

This routes through `tracker_config.py`'s `comment_cmd` field (`lib/tracker_config.py`), same as every other gate comment the dev flow posts — **behavior change**: the comment now posts only when `comment_cmd` is configured for `<KEY>`'s prefix. Previously this section always attempted a direct post; a prefix that never runs `tracker_config.py set <PREFIX> comment_cmd "python3 <path to jira_comment.py>"` (see `lib/tracker_config.py`'s docstring for the `comment_cmd` contract) now gets no verification-notes comment at all. `jira_comment.py` (not shipped in the published plugin) is the CLI+REST-fallback implementation this used to inline here. Consumers: the release skill's per-ticket verify step reads the latest such comment after a QA/prod deploy; when absent it falls back to MR description + ticket body.

Announce in one line, reading the script's own stdout: `posted: <KEY>` → `Verification notes: commented on <KEY>`; `skipped: ...` → `Verification notes: skipped — <the script's reason>`; `failed: ...` → surface that line as-is, do not swallow it.

## Worktree cleanup

Auto-execute, no prompt — the one pre-approved destructive exception (CLAUDE.md carves it out): runs ONLY after a merge the user already approved. Never trigger or confirm the merge here.

```bash
# Removal runs FROM the main checkout. Removing the one you are standing in does succeed,
# but it unlinks your cwd and any later BARE git command then dies with "Unable to read
# current working directory" for the life of the shell — hence -C "$MAIN" throughout, which
# is immune.
# It does NOT require $MAIN to be on any particular branch: the branch being deleted is
# checked out in the worktree being removed, so removing it first frees the `branch -D`.
# Substitute <main-checkout-abs> as a LITERAL path — not a bare git call, not a variable
# (mechanism: CLAUDE.md "Worktree cwd gotcha"). Get it wrong and the `branch -D` below deletes a
# branch in another repo.
# Do NOT derive this from <worktree-abs>: this block removes that directory, so a re-run or a
# resume after a partial cleanup (worktree gone, `branch -D` not yet done) could not resolve it
# and would abort with the branch still dangling. A literal makes the step idempotent.
MAIN="<main-checkout-abs>"
# Catches an unsubstituted placeholder, and equally a path that is no longer a checkout (main
# moved or renamed). With a literal MAIN is never empty, so this asserts resolvability, not
# non-emptiness.
git -C "$MAIN" rev-parse --git-dir >/dev/null 2>&1 \
  || { echo "post-merge: '$MAIN' is not a git checkout — substitute the real absolute path"; exit 1; }
DEFAULT_BRANCH=$(git -C "$MAIN" symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
[ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH=main   # origin/HEAD unset; `git remote set-head origin -a` sets it
# Assert it resolves — the blind `main` fallback is wrong wherever the default is `master`,
# and a non-existent value silently disables the refresh AND breaks the leftover check below.
git -C "$MAIN" rev-parse --verify --quiet "origin/$DEFAULT_BRANCH" >/dev/null \
  || { echo "post-merge: origin/$DEFAULT_BRANCH does not exist — run 'git -C \"$MAIN\" remote set-head origin -a', then redo the refresh"; DEFAULT_BRANCH=""; }

# Guard against a concurrent session (or a spawn_task child) still working in this
# worktree on the same branch — CLAUDE.md "Spawn tasks for cross-session follow-ups"
# documents exactly this: another session can commit AND push on the branch a session
# is still working on. A merge elsewhere does not mean this worktree has nothing of its
# own: uncommitted edits, or local commits made after the merge that never got pushed.
# `status --porcelain` catches the former; `log HEAD --not --remotes` catches the latter
# (scoped to HEAD only — a linked worktree shares refs with $MAIN, so an unscoped
# `--branches --not --remotes` would also flag unrelated local branches).
# Both read straight from <path> (still a valid checkout at this point) — quoted like every
# other literal-substituted path in this file, to survive a path containing spaces.
# Known limitation: `--not --remotes` reads "unpushed" as "unreachable from any
# remote-tracking ref this checkout currently knows about" — if the branch's own
# origin/<name> ref gets pruned locally (e.g. `fetch.prune=true`, or GitLab's
# delete-source-branch-on-merge plus a later `fetch --prune` here) AFTER a squash
# merge, the original commits become unreachable from origin/$DEFAULT_BRANCH too
# (squash mints new SHAs), so this permanently reads a fully-merged branch as
# "unpushed" and the guard keeps skipping it. Fails safe (skip, not delete) — not
# data loss, just a cleanup that then needs a manual `worktree remove`/`branch -D`.
# And this check-then-act is not atomic: a commit landing between this capture and
# the `worktree remove` a few lines below is still unprotected — narrows the race
# from "always" to "a few lines of gap", does not eliminate it.
WT_DIRTY=$(git -C "<path>" status --porcelain 2>/dev/null)
WT_UNPUSHED=$(git -C "<path>" log HEAD --not --remotes --oneline 2>/dev/null)
if [ -n "$WT_DIRTY" ] || [ -n "$WT_UNPUSHED" ]; then
  echo "post-merge: worktree <path> has uncommitted or unpushed work — leaving it in place, not deleting branch <name>."
  [ -n "$WT_DIRTY" ] && printf 'Uncommitted:\n%s\n' "$WT_DIRTY"
  [ -n "$WT_UNPUSHED" ] && printf 'Unpushed commits:\n%s\n' "$WT_UNPUSHED"
  echo "Re-run cleanup once the worktree is clean (commit/push, or discard, then remove manually)."
else
  git -C "$MAIN" worktree remove <path> || git -C "$MAIN" worktree remove --force <path>
  git -C "$MAIN" branch -D <name>
fi
git -C "$MAIN" worktree prune

# Refresh $MAIN only when it is already on the default branch with no tracked changes —
# a concurrent session may be working there. NEVER switch/checkout it to satisfy this.
# Untracked-only dirt must NOT skip: the pull has to run to surface the leftover below.
MAIN_BRANCH=$(git -C "$MAIN" branch --show-current)                    # empty on detached HEAD
MAIN_DIRTY=$(git -C "$MAIN" status --porcelain --untracked-files=no)   # tracked changes only
if [ -n "$MAIN_BRANCH" ] && [ -n "$DEFAULT_BRANCH" ] && [ "$MAIN_BRANCH" = "$DEFAULT_BRANCH" ] && [ -z "$MAIN_DIRTY" ]; then
  git -C "$MAIN" pull --ff-only || true   # on failure see the leftover-originals note below
else
  echo "skipped main refresh — branch='${MAIN_BRANCH:-detached}' (want '${DEFAULT_BRANCH:-unresolved}'), tracked changes: $([ -n "$MAIN_DIRTY" ] && echo yes || echo no)"
fi
```

Pull failed with "untracked working tree files would be overwritten by merge" → leftover carried originals (a `cp` during intake carry instead of a move — task-intake `lib/worktree-carry.md`). For each file the error lists: remove it ONLY if identical to the incoming tracked version (`git -C "$MAIN" show "origin/$DEFAULT_BRANCH:<path>" | diff - "$MAIN/<path>"`), then retry the pull once. Content differs → leave the file, report it, continue cleanup.

Report what was removed in one line (e.g. `Cleaned up: worktree <path>, branch <name>`) — or, when the dirty/unpushed guard fired, report that guard's own skip line verbatim instead (worktree and branch were left in place, not an error). Plus, separately, one line whenever the main-refresh below did not land — that step's own skip line verbatim, or the pull's own error. Neither refresh outcome is a failure of this step, but both leave `$MAIN` behind the remote — and on that skip path no fetch runs at all, so the local `origin/$DEFAULT_BRANCH` ref is stale too and cannot be compared against (matters for the post-merge plugin release, `docs/plugin-release.md` step 1).

## Dev-flow journal (step 8.5)

Append one block to `~/.claude/state/dev-flow-journal.md` (create parent dir if needed). Use values already known this run — no new instrumentation except the two read-only transcript readers, `~/.claude/local-marketplace/simo-dev/lib/session_tokens.py` (token sum) and `~/.claude/local-marketplace/simo-dev/lib/session_meta.py` (model/effort). Unknown values → `n/a`.

```bash
mkdir -p ~/.claude/state
DATE=$(date -u +%Y-%m-%d)
# Each block that runs a plugin script re-binds R: shell state does not survive between
# Bash calls. This file reaches a shell through the Read tool, which substitutes nothing, so the
# placeholder arrives raw and the shell expands it — empty in the Bash tool env as measured, so
# the binding lands on the source tree. A value exported into that env naming a real directory
# would win instead. Either way the pin belongs to rendered SKILL.md bodies, not to this one.
# Without the binding the python3 calls below run against /lib/... and exit rc 2.
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
# repo name, stable from any worktree (--git-common-dir points at the main checkout).
# Substitute the main checkout's absolute path as a LITERAL — not `$MAIN`, which was set in a
# different fenced block and is empty here (shell state does not survive between Bash calls),
# and not a bare call: by this point the task worktree is removed, so a bare call from the
# unlinked cwd dies with "Unable to read current working directory".
REPO=$(basename "$(dirname "$(git -C "<main-checkout-abs>" rev-parse --path-format=absolute --git-common-dir)")")
BRANCH="<the merged branch name>"      # may already be deleted
# skills actually fired this task (PostToolUse Skill logger catches auto-fires).
# Session id is the primary key; repo+branch is the key for sessions whose cwd IS
# the task worktree (spawn_task children, terminal-launched, unattended headless callers).
# Deliberately unbounded in time — see the KNOWN LIMIT in lib/skill_log.py: if
# this session already ran an earlier task, its skills leak in. Sanity-check the
# result against what you actually did this task before writing the block.
SKILLS=$(python3 "$R/lib/skill_log.py" read --session "$CLAUDE_CODE_SESSION_ID" --repo "$REPO" --branch "$BRANCH")
[ -z "$SKILLS" ] && SKILLS="n/a"
# flow path actually taken (from the skill logger):
if printf '%s\n' "$SKILLS" | grep -q 'simo-dev:grill-me'; then MODE=grill-me
else MODE=direct; fi
# output_tokens main+subagents (best-effort; needs $CLAUDE_CODE_SESSION_ID) — use the "total=".
# NO `2>/dev/null`: rc 3 means the script itself crashed and the traceback on stderr is the
# only diagnosis. Whenever the script runs at all, stdout carries one parseable line, so the
# redirect bought nothing and hid everything (2026-08-18: a non-object transcript line crashed
# it, and the journal recorded a `tokens: unknown` that read as "transcript not found").
# rc 0 = total computed | 4 = nothing to count (no session id, or no main transcript) |
# 3 = internal error. An EMPTY $TOKENS is the third case and must be read BEFORE the rc:
# it means python never reached the module, so the rc is not one of ours — observed 2 when it
# cannot open the file (the wrong-$R case this file already warns about above),
# 1 on a syntax error, 127 with no usable python3. That collision is why "nothing to count"
# is 4 and not 2. The guard below quotes the actual rc, so an unlisted one still shows up.
TOKENS=$(python3 "$R/lib/session_tokens.py"); TOKENS_RC=$?
# `if`, not `[ … ] && …`: the short form exits 1 whenever $TOKENS is non-empty, i.e. on the
# normal path, which would read as a failed block if nothing followed it.
if [ -z "$TOKENS" ]; then TOKENS="(script did not run — rc $TOKENS_RC, check \$R)"; fi
# model/effort from the main transcript's last assistant turn (lib/session_meta.py) — the
# transcript records both, no env var does. Same no-`2>/dev/null` and empty-stdout reasoning
# as TOKENS above; any failure leaves an honest `unknown`, never a guess.
META=$(python3 "$R/lib/session_meta.py"); META_RC=$?
if [ -z "$META" ]; then META="(script did not run — rc $META_RC, check \$R)"; fi
MODEL="unknown"; EFFORT="unknown"
if [ "$META_RC" -eq 0 ]; then
  MODEL=$(printf '%s\n' "$META" | sed -n 's/^model=\([^ ]*\) effort=.*/\1/p')
  EFFORT=$(printf '%s\n' "$META" | sed -n 's/^model=[^ ]* effort=\([^ ]*\).*/\1/p')
  MIXED=$(printf '%s\n' "$META" | sed -n 's/.* (mixed: \(.*\))$/ (mixed: \1)/p')
  if [ -z "$MODEL" ]; then
    MODEL="unknown (unparseable: $META)"; EFFORT="$MODEL"
  else
    MODEL="$MODEL$MIXED"
    case "$EFFORT" in ""|unknown) EFFORT="unknown (not recorded on last turn)";; esac
  fi
else
  NOTE=$(printf '%s\n' "$META" | sed -n 's/.*(\(.*\))$/\1/p'); [ -n "$NOTE" ] || NOTE="rc $META_RC"
  MODEL="unknown ($NOTE)"; EFFORT="unknown ($NOTE)"
fi
# Print them: shell state dies with this Bash call, so anything not on stdout is unreadable
# by the time the block below gets written — and that block BRANCHES on $TOKENS_RC, which is
# a decision, not a transcription. Keep this the last command so the block's own rc is
# printf's 0. The other variables above are unprinted for the same reason and always have
# been; only the tokens, model and effort values printed here are read back, by the
# `- tokens:`, `- model:` and `- effort:` lines.
printf 'TOKENS=%s\nTOKENS_RC=%s\nMODEL=%s\nEFFORT=%s\nMETA=%s\n' "$TOKENS" "$TOKENS_RC" "$MODEL" "$EFFORT" "$META"
```

Block format (append verbatim, one blank line before the heading):

```
## $DATE · $REPO · $BRANCH · mode=$MODE
- ticket: <KEY or "none">
- task: <one line: type + rough size>
- model: <the printed MODEL, verbatim — the block already turns every non-zero META_RC into `unknown (<reason>)`; never a bare `unknown`>
- effort: <the printed EFFORT, verbatim — `unknown (not recorded on last turn)` when the last turn carried none; never a bare `unknown`>
- tokens: <$TOKENS_RC 0 → the `total=` from $TOKENS followed by its parenthesised note verbatim, e.g. `813274 (15 subagent transcripts, 578 lines skipped)`; otherwise `unknown (<the parenthesised note from $TOKENS>)`, never a bare `unknown`>
- skills used: <$SKILLS>
- review-loop iterations: <count, or "n/a">
- post-MR auto-fixes: <count of fix-commits this skill applied>
- pipeline passed first try: <yes|no>
- problems/friction: <concrete issues hit this task — or "none">
- notes: <1-2 lines qualitative takeaway>
```

An honest `unknown` beats a fake datapoint. `model` and `effort` are auto-derived from the main transcript's last assistant turn by `session_meta.py` — `unknown (<reason>)` when it found nothing or failed, with a ` (mixed: …)` suffix on `model` when the session switched models; unlike `- tokens:`, the block already made every decision, so those two lines are pure transcription. `tokens` is auto-derived (on a plain non-SDD task it records the main thread only and says `subagents not captured`). When the script runs at all it leaves one parseable line on stdout whatever the rc, and the note in its parentheses is what makes the `unknown` say why: `4` is "nothing to count" (no session id, or no main transcript), `3` is the script itself crashing — a bug to fix, not a missing transcript. An empty `$TOKENS` is the case the rc cannot describe, because python never reached the module: the guard above turns it into its own note rather than letting it read as one of the other two.

The `N lines skipped` clause counts transcript lines that reached no total: non-JSON, JSON that is not an object, and a non-integer `output_tokens`. It is a coarse signal, not an alarm — the subagent glob is deliberately wide and also matches captured tool output, so a healthy session reports hundreds (the run that produced this contract: 578 skipped, 15 transcripts, all correct). A record with a `usage` object and no `output_tokens` key is a normal record and is not counted, so a drop can also happen without the number moving. Read it as "something in this session's files was not transcript-shaped", and compare it against past entries rather than against zero.

After writing the block, prune this task's entries from the skill log. Self-contained
— shell state does not survive between Bash calls, so re-derive the values. Prune
only removes repo+branch matches (task-specific); session-keyed lines are left
behind on purpose, because a session can cover two interleaved tasks. It reports
`pruned N lines` on stderr. `0` is the expected result whenever the session's cwd
was not the task worktree (the usual host-launched case — those lines carry the
harness branch): not a problem, nothing to report in the journal. It is only worth
a look when the task ran terminal-launched, headless, or as a spawn_task child,
where a `0` usually means an unsubstituted `BRANCH` placeholder:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
REPO=$(basename "$(dirname "$(git -C "<main-checkout-abs>" rev-parse --path-format=absolute --git-common-dir)")")   # literal path, see above
BRANCH="<the merged branch name>"
python3 "$R/lib/skill_log.py" prune --repo "$REPO" --branch "$BRANCH"
```
