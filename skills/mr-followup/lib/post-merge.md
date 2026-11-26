# Post-merge procedure — ticket transition, verification notes, cleanup, journal, next sub-task / parent close, release hook

Loaded on demand from `mr-followup` steps 8 / 8.5 / 8.6 / 8.7, once the user has confirmed the merge. Run the sections in order.

## Merge entry differences

In a fresh session started from the step 7.6 card (`mr-followup` "Merge entry"), every step below runs as written, with the brief's literals and these differences:

- Verification notes: "step 7.5 passed this session" reads as the brief's `qa gate: passed <date>`.
- Worktree cleanup: every path comes from the brief, so it runs the same from the main checkout, from this session's own worktree, or from the task tree. In the desktop-app branch the tree belongs to the **parked** session (`<parked>`). That echo's "Archiving this session" line is replaced in the report by: archive this session and the parked one (once detached, this one is not archived with it; the app removes the task tree with the parked one). This session in a desktop-app worktree of its own (the card's `use_worktree` `true`): add its branch (`git -C "<this session's cwd>" branch --show-current`, read before the archive) to the `branch -D` line, since archiving removes that tree but not its branch.
- Journal (8.5): `TASK` = the brief's `task id` when the cleanup printed none. Before the `event --type outcome` line, end the parked session's hold so its later use does not land in this task: `python3 "$R/lib/flow_telemetry.py" close --session "<parked>" --task "$TASK" || true` (skip when `<parked>` is `unknown`). `--task` makes it a no-op (rc 1, `not closed`) when the parked session started other work while the gate waited. The `handoff` this writes is dated at the merge, so the parked session's share of the task's cost runs to the merge: its 7.5/7.6 work is counted, and so is anything else it did while the card waited.

## Ticket transition (step 8)

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
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

**Verification notes ticket comment** — posted at MR open (`simo-dev:commit` step 9) and kept current by step 7.4's supersede check; post-merge posts only in the cases the section below lists (no notes recorded for this MR, or a passing step 7.5 this session). Run it between the ticket transition and the cleanup (the notes need branch/MR/diff context, and the per-MR record lives in the worktree's git dir, which cleanup removes). Consumed at release time by the release skill's per-ticket verify step, which confirms the deployed behavior and transitions the ticket (`Ready for Deploy` after QA deploy, `Done` after prod). Spec: `skills/commit/lib/verification-notes.md`.

Then auto-execute the worktree cleanup (pre-approved exception — runs ONLY after the user-approved merge) and run the journal step (step 8.5, below): it records the task's outcome as a flow event, attributes its tokens, regenerates `~/.claude/state/dev-flow-journal.md` and the HTML dashboard from the flow events and clears the session's task ID (skip for `--no-mr`/WIP runs that never merged). After the journal, step 8.6 offers the next ready sibling sub-task as a new session (gated y/N), or the umbrella close once every sub-task is Done — both behind a user gate, driven by the `post-merge:` line printed above (the worktree is gone by then, unless it is a desktop-app worktree or the main checkout, left in place). Last, step 8.7 invokes the repo's configured release skill, if any, with args `post-merge` — no ticket needed; an unconfigured repo prints nothing.

## Verification notes ticket comment

The notes were posted at MR open (`simo-dev:commit` step 9) and kept current by step 7.4's supersede check. Post-merge follows `skills/commit/lib/verification-notes.md` (path relative to the plugin root — load it on demand; its **Gate before building anything** runs first, and its **Inputs** give post-merge's sources: HEAD from the MR view, the diff from `glab mr diff` / `gh pr diff`):

1. Nothing recorded for this MR (an MR opened before this flow, or from another worktree) → **First post**:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/verification_notes_state.py" has --repo "<worktree-abs>" --mr "<mr-url>"
   ```
2. `has` → `yes` → always the **Supersede check**, so a push made outside the flow before the merge (a forge-UI suggestion, a manual push after the clean exit) still gets its notes; add `--qa-gate` only when step 7.5 passed this session, and then the rebuilt notes carry the `Pre-merge QA gate: passed <date>` line:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/verification_notes_state.py" stale --repo "<worktree-abs>" --mr "<mr-url>" --head "<MR head from the view call>"   # + --qa-gate after a passing 7.5
   ```

`stale` → `no`, or nothing changed after the rebuild → post nothing and announce `Verification notes: already posted at MR open`. Other outcomes are announced as the spec's **Announce** section says.

## Worktree cleanup

Auto-execute, no prompt — the one pre-approved destructive exception (CLAUDE.md carves it out): runs ONLY after a merge the user already approved. Never trigger or confirm the merge here.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
# Step 8.5 needs the task ID once the worktree marker is gone: print it while it exists.
echo "task-id: $(python3 "$R/lib/flow_telemetry.py" current --session "$CLAUDE_CODE_SESSION_ID" --cwd "<path>")"
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

# Guard against a concurrent session (or a start_session child) still working in this
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
WT_GITDIR=$(git -C "<path>" rev-parse --path-format=absolute --git-dir 2>/dev/null)
# A worktree the desktop app created for this session (harness path + its marker file in the
# per-worktree git dir) is never removed here: this session runs in it, and every session it
# proposes inherits that cwd, so removing it makes them fail with "Working directory no longer
# exists". The app owns that tree's removal, on archive.
case "<path>" in */.claude/worktrees/*) WT_HARNESS=1 ;; *) WT_HARNESS= ;; esac
# A task resumed in place on the main checkout (task-intake lib/resume-path.md) has no worktree
# to remove, and its branch is checked out there: never switch it here, another session may be
# working in it.
if [ "<path>" -ef "$MAIN" ]; then
  echo "post-merge: <path> is the main checkout — leaving it and branch <name> in place."
  python3 "$R/lib/flow_state.py" off --repo "<path>"   # a marker exists only if intake had no session id
  [ -n "$WT_DIRTY" ] && printf 'Uncommitted:\n%s\n' "$WT_DIRTY"
  [ -n "$WT_UNPUSHED" ] && printf 'Unpushed commits:\n%s\n' "$WT_UNPUSHED"
  echo "When the checkout is free: git -C \"$MAIN\" switch ${DEFAULT_BRANCH:-<default-branch>} && git -C \"$MAIN\" pull --ff-only && git -C \"$MAIN\" branch -D <name>"
elif [ -n "$WT_HARNESS" ] && [ -n "$WT_GITDIR" ] && [ -e "$WT_GITDIR/claude-desktop-worktree" ]; then
  echo "post-merge: <path> is a desktop-app worktree — leaving it and branch <name> in place."
  python3 "$R/lib/flow_state.py" off --repo "<path>"   # the tree outlives the task: end its flow
  [ -n "$WT_DIRTY" ] && printf 'Uncommitted:\n%s\n' "$WT_DIRTY"
  [ -n "$WT_UNPUSHED" ] && printf 'Unpushed commits:\n%s\n' "$WT_UNPUSHED"
  echo "Archiving this session should make the app remove it (kept when it holds uncommitted changes); the app does not delete branches. Once the tree is gone: git -C \"$MAIN\" branch -D <name>"
elif [ -n "$WT_DIRTY" ] || [ -n "$WT_UNPUSHED" ]; then
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

Flow marker (`lib/flow_state.py`): removing the worktree removes its git dir and the marker with it. The desktop-app branch clears it explicitly, since that tree stays; so does the main-checkout branch (a task resumed in place), which holds a marker only when intake had no session id. The dirty/unpushed guard keeps it on purpose: the work there is unfinished, and a later manual `worktree remove` clears it.

Report what was removed in one line (e.g. `Cleaned up: worktree <path>, branch <name>`) — or, when the main-checkout skip, the desktop-app skip or the dirty/unpushed guard fired, report that branch's own lines verbatim instead (worktree and branch were left in place, not an error). Plus, separately, one line whenever the main-refresh below did not land — that step's own skip line verbatim, or the pull's own error. Neither refresh outcome is a failure of this step, but both leave `$MAIN` behind the remote — and on that skip path no fetch runs at all, so the local `origin/$DEFAULT_BRANCH` ref is stale too and cannot be compared against (matters for the post-merge plugin release, `docs/plugin-release.md` step 1).

## Dev-flow journal (step 8.5)

The journal is a view of the task's flow events (`lib/flow_telemetry.py`, events in `~/.claude/state/dev-flow/<task-id>.jsonl`). This step adds the task's `outcome` event, which also attributes the task's tokens from its sessions' transcripts (main thread plus `subagents/agent-*.jsonl`, four categories, deduped by `message.id`, split per step into `by_step`: the step model of `docs/canonical-flow.md` "Dev-flow journal & knowledge harvest"), rewrites `~/.claude/state/dev-flow-journal.md` whole from every task file, and clears the session's task ID: the flow stays on, and a later `task-intake` mints a new ID. No command names the journal path, so no `.claude` write prompt fires (the script opens the file itself).

The three `outcome` summary fields are written by you, short and factual — never prompt text, code or a diff:
- `task` — type and rough size (e.g. `feat, 8 tasks across 22 files`);
- `friction` — concrete problems hit in this task, or `none`;
- `notes` — a 1-2 line takeaway, with no token totals: the `attribution` event written right after holds them, and a number read earlier (e.g. from a dashboard run mid-task) is lower than the final one.

Substitute every `<…>` as a literal (shell state does not survive between Bash calls). The three summaries go inside **single** quotes — backticks and `$` inside double quotes would run as shell — with a `'` inside one written as `’`:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
TASK="<the task-id: line the cleanup block printed, empty if none>"
[ -n "$TASK" ] || TASK=$(python3 "$R/lib/flow_telemetry.py" current --session "$CLAUDE_CODE_SESSION_ID" --cwd "<main-checkout-abs>")
echo "TASK=$TASK"
if [ -z "$TASK" ]; then
  echo "journal: no task id for this session or worktree — nothing recorded"
else
  python3 "$R/lib/flow_telemetry.py" event --type outcome --task "$TASK" --field result=merged --field mr='<mr-url>' --field task='<type + rough size>' --field friction='<concrete issues, or none>' --field notes='<1-2 line takeaway>' \
    && python3 "$R/lib/flow_telemetry.py" render \
    && { python3 "$R/lib/flow_telemetry.py" close --session "$CLAUDE_CODE_SESSION_ID" || echo "journal: no session flag to clear (the task was seen through the marker) — fine"; }
  python3 "$R/lib/flow_dashboard.py" || echo "dashboard: not regenerated (rc $?) — run lib/flow_dashboard.py by hand"
fi
```

The last line regenerates the HTML dashboard (`lib/flow_dashboard.py`, `docs/canonical-flow.md` "Dev-flow journal & knowledge harvest") from every task file, this task's new `outcome` and `attribution` included. It sits outside the `&&` chain on purpose: a failed regeneration never keeps the task ID from being cleared, and a chain that stopped early still leaves a fresh page. Like the journal, the script opens its output file itself, so no `.claude` write prompt fires.

Once the outcome is recorded, end the step's report with one line: `Task closed. New work is cheaper in a new session: this one rewrites its whole context after an hour idle.`

The chain stops at the first failure, so the task ID is cleared only once the outcome is recorded. An `event` refused with rc 2 names the field (too long, a newline in a one-line field, a field the type does not take): fix it and re-run the block with `TASK` set to the printed `TASK=` value. When `event` succeeded and a later command failed, re-run only the commands from the failed one on (with the same `TASK`): re-running the `event` adds a second `outcome`. The `outcome` event attributes the task itself, so no close can skip it; a warning `attribution failed` there leaves the outcome recorded and names the command to re-run (`attribute --task`, then `render`, since the chain has already rendered the journal without it). It prints `attribution: total=<n> cost=$<c> (<note>)`, `<c>` the estimated list-price cost, each record at its own model's price (`lib/transcript_usage.py` `PRICES`; the note counts the models not priced exactly: an unknown one, priced as the fallback, or fast mode, at standard rates); a `sessions without transcript` note means a session of this task left no transcript here (another machine, or pruned), so the total is a floor. Model and effort are no longer recorded: the last turn's value was a guess about the whole task.

After the journal step, prune this task's entries from the TSV skill log (it stays: it is the hook-liveness probe `lib/review_marker.py` reads). Self-contained
— shell state does not survive between Bash calls, so re-derive the values. Prune
only removes repo+branch matches (task-specific); session-keyed lines are left
behind on purpose, because a session can cover two interleaved tasks. It reports
`pruned N lines` on stderr. `0` is the expected result whenever the session's cwd
was not the task worktree (the usual host-launched case — those lines carry the
harness branch): not a problem, nothing to report in the journal. It is only worth
a look when the task ran terminal-launched, headless, or as a start_session child,
where a `0` usually means an unsubstituted `BRANCH` placeholder:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
REPO=$(basename "$(dirname "$(git -C "<main-checkout-abs>" rev-parse --path-format=absolute --git-common-dir)")")   # literal path, see above
BRANCH="<the merged branch name>"
python3 "$R/lib/skill_log.py" prune --repo "$REPO" --branch "$BRANCH"
```

## Next sub-task / parent close (step 8.6)

Runs after the journal. Skip entirely when step 8 printed no `post-merge:` line (no `KEY`). The worktree is gone by now (a desktop-app worktree is left in place, see Worktree cleanup), so reuse `<KEY>`, `<PREFIX>` and `<TARGET>` as literals from that line:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/tracker_config.py" run "<PREFIX>" next_subtask_cmd "<KEY>" --resolved-at "<TARGET>" || echo "WARNING: next_subtask_cmd failed for <KEY> (continuing)"
```

`--resolved-at "<TARGET>"`: the sub-task just merged counts as a resolved blocker at its post-merge state, so a sibling waiting only on it is already ready. Read the one JSON line (contract: `lib/tracker_config.py` docstring, `next_subtask_cmd`) and pick the first matching case. One gate per run, never a loop. Whatever case matches, a `[jira-next] ... not in STATE_ORDER or unreadable` stderr note is reported in one line with its keys: the parent cannot reach the close gate until an unknown state is calibrated in the binding (an unreadable key needs a re-check instead).

1. **Nothing to propose** — no JSON line (empty stdout, or only the WARNING above), `"parent": null`, empty `ready` and `pending` with `parent_complete` false, or `parent_complete` true with the parent already at/past Done (or Closed) → say nothing more.
2. **Parent close** — `parent_complete` true and `parent_status` not already at/past Done (or Closed) → y/N gate, default N: name the parent, say every sub-task is Done and ask whether the feature was verified as a whole and the parent should be closed (a per-sub-task check does not confirm the parts work together). On `y`, `<PARENT>` = the JSON's `parent`:

   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/tracker_config.py" run "<PREFIX>" transition_cmd "<PARENT>" "Done" --clear-blocked || echo "WARNING: transition_cmd failed for <PARENT> -> Done (continuing)"
   ```

   No `--leaf-only` (the key is the umbrella) and no `--sync-parent`. On N, nothing else happens: the umbrella stays where the sync left it.
3. **Next sub-task** — non-empty `ready` → resolve the repo for the first entry: the directories next to the main checkout (`dirname <main-checkout-abs>`) whose name appears as a whole word, case-insensitive, in the entry's `summary` or `components`, and that are a git checkout root (`git -C <dir> rev-parse --show-toplevel` resolves to the same directory as `<dir>` — compare directories, not strings: it resolves symlinks); exactly one match → that path, otherwise unresolved. Then a y/N gate, default N, showing the parent, the entry's key and summary, the resolved repo path (or "unresolved"), plus one line with the other `ready` keys and one line summarising `pending` as `key (reason)`. On `y`:
   - repo unresolved → ask the user for the repo root first;
   - `mcp__ccd_session__start_session` not yet callable → run `ToolSearch` `select:mcp__ccd_session__start_session` once before the fork below, never skipped because the name is unlisted; no schema, or no `ToolSearch`, counts as not available;
   - `mcp__ccd_session__start_session` available → `initiation` `user_asked` (the `y` above); `context` `fresh`; `use_worktree` `true`; `source_branch` that repo's base branch `<base-branch>`: its short name with `origin/` stripped (`git -C <repo-root> symbolic-ref --quiet --short refs/remotes/origin/HEAD`), passed only when that resolves and exists as a local branch (`git -C <repo-root> rev-parse --verify --quiet "refs/heads/<base-branch>"`; `start_session` takes an existing local branch only), else omit it; `title` `Intake <key>`; `background` one paragraph (≥80 chars: parent, sibling just merged, what this sub-task covers); `cwd` the resolved repo root when it is not this session's project (a real repo root, never a subdirectory), omitted otherwise; `prompt` = the spawned-task marker line (session language, same rule as `simo-dev:task-intake`'s `lib/handoff-spawn.md`, first sub-bullet of its `prompt` bullet), then an instruction to run `simo-dev:task-intake <key>`, the parent key, the just-merged sibling key and MR URL as context, and the line `parent task: <TASK>` (`docs/canonical-flow.md` "Parent task"), `<TASK>` being the `TASK=` value step 8.5 printed — `close` already cleared the session's task, so `current` prints nothing here; empty → no line;
   - not available → print `simo-dev:task-intake <key>` for the user to start by hand, and the same `parent task:` line on its own line after it, to paste with the command.
4. **Only pending** — empty `ready`, not complete → one line listing `pending` as `key (reason)`.

Example (hypothetical keys): after `PROJ-12` merges at `Ready for Testing`, a JSON with `"ready": [{"key": "PROJ-13", ...}]` and `"pending": [{"key": "PROJ-14", "reason": "blocked-by:PROJ-13"}]` offers one session start for `PROJ-13` and mentions `PROJ-14 (blocked-by:PROJ-13)`.

## Release hook (step 8.7)

Runs last, once step 8.6 has fully resolved — after the user answered its y/N gate when one was shown, so two gates are never open at once. Any reply to that gate starts this step, even one about something else: run it first, then handle the rest of the reply. Unlike 8.6 it does not need a ticket: it runs with no `KEY`, no `post-merge:` line, and under `DEV_FLOW_NO_TRACKER=1` / `.claude/no-tracker`. Like every step in this file it runs only after the user-confirmed merge. Under `simo-dev:afk` it is never reached: afk disarms and prints its park recap before asking for `merge`, and these steps run only after the user's own `merge` reply.

Look the repo's release skill up from the main checkout (`<main-checkout-abs>` as a literal, the same one the cleanup used — the task worktree is normally gone):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/repo_config.py" get --repo "<main-checkout-abs>" release_skill
```

- Empty stdout, rc 0 → the repo has no release hook: say nothing, not even an announce line.
- Non-empty → invoke the Skill tool with `skill` = that value and `args` exactly `post-merge`. That skill owns its own gates and output from there.
- Non-zero rc → report its stderr line and continue.
- The Skill call itself fails (skill not installed or enabled, not model-invocable) → one line naming the skill and the error, and continue. The merge already succeeded; this is not an `mr-followup` failure.

Configure it once per repo, from any checkout or by repo-id (`docs/canonical-flow.md` "Configuring per-repo settings"):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/repo_config.py" set git.example.com/team/app release_skill my-plugin:my-release
```
