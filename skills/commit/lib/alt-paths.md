## Existing-MR Path (step 6 detected an MR)

When `EXISTING_MR` is non-empty, the cascade is shorter:
- Steps 7-9 skipped (no body generation, no create call).
- Step 10 fires (auto-enters `simo-dev:mr-followup`).
- A Draft request (`--draft`, `--no-draft` or a plan's `**MR draft:**` header) does not change the existing MR's draft state: it stays as it is, and toggling it is the user's call on the forge. When `--draft` or `--no-draft` was passed explicitly, tell the user it was not applied, naming the commands that toggle it (`glab mr update <branch> --draft` / `gh pr ready <branch> --undo`, or `--ready` / `gh pr ready <branch>`), each prefixed with `cd "<worktree-abs>" &&`.
- The push already happened (step 5) and its commit message was the Also-fixed record, so clear the ledger before step 10 (`LEDGER_FAILED` → warn, continue, re-run it once fixed):

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/also_fixed_ledger.py" clear --repo "$W" || echo "LEDGER_FAILED rc=$?"
```

- Before step 10, the **First post** procedure of `lib/verification-notes.md`, with this path's inputs from its **Inputs** section (no `mr_ctx.json` here: steps 7-9 were skipped). It posts only when nothing is recorded for this MR (an MR opened before this flow, or from another worktree); later changes are covered by `simo-dev:mr-followup`'s supersede check.

This is the "follow-up commit to an open MR" flow.

## Protected-Branch Path (working directly on master/main/develop)

Triggered by the detection above — an out-of-flow commit on a protected branch (the canonical flow always works in a worktree) has no MR, so the gate moves to the commit itself.

1. Stage per step 4 rules (explicit file list, no `git add -A`).
2. Show a mermaid preview instead of the full diff: get the scratch dir from the fence below (it prints `mrtmp:` and creates the dir; this path skips step 7; rc 2 → re-issue it with the literals, a `STOP:` → report it and stop before staging the preview), run `git diff --staged > "<mrtmp>/staged_diff.txt"` as its OWN Bash call, never chained with `git add`, then generate the presentation via `lib/mr_presentation.py --mode create` on that diff.

   ```bash
   W="<absolute path of the task worktree>"
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
   python3 "$R/lib/mr_create.py" mrtmp --repo "$W"
   ```

   Before rendering, when `mcp__visualize__show_widget` is not yet callable (listed only as deferred, or not listed at all), run `ToolSearch` `select:mcp__visualize__show_widget` once, never skipped because the name is unlisted; no schema, or no `ToolSearch`, counts as unavailable. Then render the widget per step 8 (informational). If `show_widget` is unavailable or errors → show `git diff --staged --stat` only (stat, never the full diff; full diff only if the user asks). List untracked-but-staged files explicitly.
3. Show the computed commit message (step 3 rules), then ONE gate covering BOTH commit and push — never ask a second confirmation at push time:

   > "Commit + push to `<branch>`? [y / edit-message / abort]"

   - `y` → commit + `git push`, no further prompts.
   - `edit-message` → take the user's replacement message, re-present this gate.
   - `abort` → stop; nothing committed (staged files stay staged).
4. Steps 6-10 never run: the branch is its own target, no MR exists.
5. The Also-fixed ledger is not used here: step 3's block and step 4's `commit-done` are both skipped; the commit message lists In-scope improvements from the session's own context, as before.

Nothing skips this gate — pushing to a protected branch always asks (CLAUDE.md rule). `--no-mr` is a no-op here (there is no MR step to skip).

## WIP Opt-Out

If `--no-mr` arg OR `DEV_COMMIT_NO_MR=1` env var:
- Steps 0.5-5 execute (step 0.7 is the only harvest pass on this path).
- Steps 6-10 SKIPPED.
- Tell the user: "Commit pushed. MR creation skipped (WIP opt-out). To create MR later, re-run simo-dev:commit without --no-mr." When this run had `--draft` or `--no-draft`, say to pass it again on that re-run: the WIP run drops it.

## Commit Message Recovery

The user does not see the commit message before step 4 records it, and push (step 5) + MR creation (step 9) both now happen immediately with no gate — there is no interactive window left to catch a bad message before it's public.

- Want to fix it anyway → `git commit --amend -m "<new>"` then force-push (always asks separately per CLAUDE.md — never silent). Only worth it before anyone has reviewed or pulled the branch.
- Otherwise → add a fixup commit and accept the original message stays in history.
