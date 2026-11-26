# Merge entry (argument `merge`)

Loaded on demand from `mr-followup` `SKILL.md` when the skill is invoked with argument `merge`. "Step 1", "step 7.4" and "steps 8-8.7" refer to `SKILL.md`; steps 8-8.7 run from `lib/post-merge.md`.

A fresh session started from a step 7.6 card. Values come from the brief in the session's first prompt, as literals: `<worktree-abs>` and the cleanup's `<path>` = `worktree`, `<main-checkout-abs>` = `main checkout`, `<branch>` and the cleanup's `<name>` = `branch`, `<base>`, `<mr-url>` = `mr`, `<TASK>` = `task id` (absent → empty), `<parked>` = `parked session`. No brief in the first prompt → stop and say so. Skip the ledger entry check and steps 1.5-7.6: the parked session already ran them. The merge gate below is this skill's own question, so the user's answer to it is no review-loop trigger.

First, before any other call (any turn end of a linked child wakes the parked session, which then rewrites its whole idle cache): `mcp__ccd_session_mgmt__detach_session` with `"self"` (not yet callable → `ToolSearch` `select:mcp__ccd_session_mgmt__detach_session` once). Detached → no line. Refused or not available (e.g. this session shares the parked session's tree) → one line `detach: <reason>, the parked session will wake at each turn end`, then go on: the merge works the same.

0. `<TASK>` set → check it is still open:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/merge_brief.py" task-state --task "<TASK>"
   ```
   `closed` → post-merge already ran (the parked session merged): say so and stop.
1. Step 1 with `--view-only` appended to the `detect` call (forge, MR view, state check; no CodeReview transition, no stacked-parent check). The MR resolved there must be the brief's `mr`; another one → stop and ask.
2. MR merged → say so, go to step 4's re-check (no merge command). Closed → stop. Open → read the head SHA, the pipeline status and the merge options (GitHub: `cd "<worktree-abs>" && gh pr view "<branch>" --json headRefOid,statusCheckRollup` and `cd "<worktree-abs>" && gh repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed`):

   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   V=$(cd "<worktree-abs>" && glab mr view "<branch>" -F json) || exit 1
   for f in sha head_pipeline.status squash force_remove_source_branch; do
     printf '%s=%s\n' "$f" "$(printf '%s' "$V" | python3 "$R/lib/json_field.py" - "$f")"   # false prints empty
   done
   ```

   Head ≠ the brief's `head` → stop: commits landed after the clean exit and no round reviewed them; run `simo-dev:mr-followup` without arguments instead. Draft → say it must be marked ready first (step 7.4 item 7's command), stop.
3. **Merge gate** — ask, default N, showing the MR link, its title, the pipeline status and the exact command. GitLab, through the merge API (never `glab mr merge`: it decides auto-merge from the MR's `pipeline` field, which can be null while a pipeline runs, and then merges at once whatever the flags). The exact command opens with step 2's `R=...` line, in the same call, then: `[ -f "$R/lib/json_field.py" ] || { echo "R unbound, nothing sent"; exit 1; }; V=$(cd "<worktree-abs>" && glab api -X PUT "projects/:id/merge_requests/<mr-iid>/merge" -f sha=<head>) || { printf '%s\n' "$V"; exit 1; }; for f in state merge_error; do printf '%s=%s\n' "$f" "$(printf '%s' "$V" | python3 "$R/lib/json_field.py" - "$f")"; done`, adding `-F squash=true` when `squash=true` and `-F should_remove_source_branch=true` when `force_remove_source_branch=true` (the MR's own settings). With no `auto_merge` field it is an immediate merge. `state=merged` → merged; a 4xx refusal (sha mismatch, not mergeable) changes nothing, but any non-zero exit (a 5xx or a timeout can come after the merge) still goes through step 4's re-check; any other `state` (e.g. a 200 left `opened` with `merge_error` set) → NOT merged: report it at step 4 and stop, never as a queued merge. GitHub: `cd "<worktree-abs>" && gh pr merge <pr-num> --match-head-commit <head>` plus the method the repo allows (`--merge`, `--squash` or `--rebase`; more than one allowed → ask which in the same gate). Never an auto-merge: a pipeline still running → say so in the gate (merging now skips its result). Another method or flag only when the user names it. N → stop; the user can ask again later.
4. On `y` run the command, then re-check (still open → re-check once after a few seconds: a merge in progress or a merge queue reads open): `python3 "$R/lib/check_mr_state.py" "<branch>" --forge <glab|gh> --cwd "<worktree-abs>"` (`R` bound as above). State `merged` → steps 8-8.7 with the brief's literals, as `lib/post-merge.md` describes, plus its **Merge entry differences**. Any other state, a refusal or an error → report it verbatim and stop; never run post-merge on an MR that is not merged, never retry with other flags unasked. Still open after a queued merge → say: once the MR shows merged, invoke `simo-dev:mr-followup merge` again in this session to run post-merge.
