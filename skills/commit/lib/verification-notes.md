# Verification notes ticket comment

Loaded on demand by `commit` step 9 (after MR creation), `commit`'s existing-MR path (`lib/alt-paths.md`), `mr-followup` step 7.4 (before every report-ending stop) and `mr-followup` post-merge (`skills/mr-followup/lib/post-merge.md`). Paths below are relative to the plugin root.

One comment on the ticket, posted directly (no confirmation), in English (repo-artifact convention). The tracker's `comment_cmd` can only post, never edit: when the notes change, a **new** comment carrying the full notes supersedes the earlier one.

## When to write

- **Write** when the change alters observable runtime behavior: endpoints/response shapes, search/scoring, jobs/importers, runtime config, bug fixes with a reproducible symptom.
- **Skip** for docs/typo-only, test-only, CI config, pure refactors, dependency bumps.
- **Skip silently** with no ticket key (`DEV_FLOW_NO_TRACKER` / `.claude/no-tracker`), or an empty MR URL — never call the state script with `--mr ""`.

## Format

Terse and actionable for a context-free release-time agent (it has no session context, only the ticket):

```
h4. Verification notes (auto)
* MR: <url>
* Change: <one-paragraph summary of the MR's change>
* QA check: <concrete steps — endpoints to call, jobs to trigger, expected vs previous behavior>
* PROD check: <prod equivalents — base URL / ArgoCD app names where they differ, else "same as QA">
* Pre-merge QA gate: <"passed <date>" — only in a post-merge comment after a passing mr-followup step 7.5, else omit this line>
```

A superseding comment carries the full notes, never a delta: the consumer reads only the latest one.

## Inputs

Every call site uses the same sources, so the per-MR record matches across sessions:

`<mrtmp>` below is the `mrtmp:` literal of step 7's `lib/mr_create.py prepare`.

- **MR URL** — the forge's canonical web URL, always from the same field: `cd "<worktree-abs>" && glab mr view "<branch>" -F json | python3 "$R/lib/json_field.py" - web_url` / `cd "<worktree-abs>" && gh pr view "<branch>" --json url -q .url`. `mr-followup` may reuse its step-1 `<mr-url>` when it came from that same field. Never the `MR created:` line.
- **Key** — commit step 9: `branch_meta.jira_key` from `<mrtmp>/mr_ctx.json`. Everywhere else: `python3 "$R/lib/derive_ticket_key.py" "<branch>" --cwd "<worktree-abs>"`.
- **Change content** — commit step 9: `<mrtmp>/mr_diff.txt`. Existing-MR path and `mr-followup` step 7.4: a fresh diff taken at that moment through the size gate the rest of the plugin uses, `python3 "$R/lib/diff_capture.py" "<worktree-abs>" -- "origin/<base>...HEAD"` (committed work only: the record is stored against HEAD) — never step 6's earlier capture, which predates a round's fix commit (a round-boundary stop reports after pushing it). `<base>` on the existing-MR path, where no `mr_ctx.json` exists: the MR's own target, from the same view call as the URL (`... -F json | python3 "$R/lib/json_field.py" - target_branch` / `gh pr view "<branch>" --json baseRefName -q .baseRefName`); in `mr-followup`, its step-1 `<base>`. Post-merge: `cd "<worktree-abs>" && glab mr diff "<branch>"` / `gh pr diff "<branch>"` (after a merge the local three-dot diff is empty).
- **HEAD** — the MR's head on the forge, from the same view call as the URL: `... -F json | python3 "$R/lib/json_field.py" - sha` / `gh pr view "<branch>" --json headRefOid -q .headRefOid` (a push from the forge UI or another machine moves it while the local HEAD stays behind). When it differs from `git -C "<worktree-abs>" rev-parse HEAD`, take the Change content from `glab mr diff` / `gh pr diff` as post-merge does, so content and HEAD name the same commit.

## Gate before building anything

Every procedure below starts here; nothing is built when it says skip:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
KEY="<KEY>"
[ -n "$KEY" ] && [ -n "$(python3 "$R/lib/tracker_config.py" get "${KEY%%-*}" comment_cmd)" ] && echo "notes: go" || echo "notes: skip"
```

`notes: skip` (no key, or no `comment_cmd` for the prefix) → stop silently. The When-to-write Skip rule also stops here, before any state call.

## Post

Body in a temp file, posted through the shared tracker comment mechanism — never a direct CLI call, never a positional/heredoc body (a positional/heredoc invocation of the underlying comment-posting CLI hung 15+ minutes with no output when backgrounded, no TTY present — observed 2026-08-25). Record only when the **last** output line is `posted:` (a `comment_cmd` may print its own output first, e.g. the comment URL); add `--qa-gate` when the body carries the `Pre-merge QA gate` line:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
OUT=$(python3 "$R/lib/post_tracker_comment.py" "<KEY>" "<tmpfile>"); echo "$OUT"
case "$(printf '%s\n' "$OUT" | tail -n1)" in posted:*) python3 "$R/lib/verification_notes_state.py" record --repo "<worktree-abs>" --mr "<mr-url>" --head "<HEAD>" --body "<tmpfile>" ;; esac
```

`post_tracker_comment.py` routes through `tracker_config.py`'s `comment_cmd` field (`lib/tracker_config.py`, whose docstring has the contract); `jira_comment.py` (not shipped in the published plugin) is the CLI+REST-fallback implementation configured there for JIRA prefixes.

**State call failures.** Any `verification_notes_state.py` call below that exits non-zero, or prints nothing where a `yes`/`no` is expected, is not an answer: announce `Verification notes: state unreadable — <its stderr ERROR line>` and post nothing (an unreadable state file would otherwise read as "no record" and post on every stop).

## First post

Commit step 9, the existing-MR path, and post-merge for an MR with no record:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/verification_notes_state.py" has --repo "<worktree-abs>" --mr "<mr-url>"
```

`no` → build the notes, then **Post**. `yes` → nothing (announce `Verification notes: unchanged`).

## Supersede check

`mr-followup` step 7.4 and post-merge. `has` first: `no` → **First post**. Otherwise:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/verification_notes_state.py" stale --repo "<worktree-abs>" --mr "<mr-url>" --head "<HEAD>"   # post-merge after a passing 7.5: add --qa-gate
```

- `no` → nothing: this HEAD was already checked (resume-safe).
- `yes` → rebuild the notes from the current Change content and compare with the recorded body — never with session memory, a fresh session has none:
  ```bash
  R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
  python3 "$R/lib/verification_notes_state.py" show --repo "<worktree-abs>" --mr "<mr-url>"
  ```
  Change or QA/PROD steps materially differ, or the QA-gate line is new → **Post**. Otherwise mark this HEAD checked without posting, so a resume does not re-judge it:
  ```bash
  R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
  python3 "$R/lib/verification_notes_state.py" record --repo "<worktree-abs>" --mr "<mr-url>" --head "<HEAD>"
  ```

Whoever pushed the commits — an `mr-followup` fix round, or a follow-up `simo-dev:commit` through the existing-MR path — the check sees the new HEAD.

## Announce

One line, from the script's own stdout:

- `posted: <KEY>` → `Verification notes: commented on <KEY>`
- `skipped: ...` → `Verification notes: skipped — <the script's reason>`
- `failed: ...` → surface that line as-is, do not swallow it
- no status line at all (the script crashed: a traceback on stderr, `$OUT` empty or only `comment_cmd`'s own output) → report it as failed, quoting stderr; nothing is recorded
- nothing to post → `Verification notes: unchanged`; at post-merge → `Verification notes: already posted at MR open`

## Call sites

- `commit` step 9 — **First post**, right after `MR created:`.
- `commit` existing-MR path — **First post** (an MR opened before this flow, or from another worktree); later changes are `mr-followup`'s.
- `mr-followup` step 7.4 — **Supersede check** before every report-ending stop.
- `mr-followup` post-merge — **First post** when `has` → `no` (MRs opened before this flow); else always the **Supersede check** — with `--qa-gate` only after a passing step 7.5 this session — so a push made outside the flow before the merge (a forge-UI suggestion, a manual push after the clean exit) still gets its notes; an unchanged HEAD answers `no` at once.

## Consumer

The release skill's per-ticket verify step reads the latest such comment after a QA/prod deploy; absent → MR description + ticket body. Notes now exist from MR open, so a closed or abandoned MR, or a second MR on the same ticket, can leave a comment for undeployed work: the consumer must confirm the MR named in the comment is merged before trusting it.
