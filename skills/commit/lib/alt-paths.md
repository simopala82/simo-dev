## Existing-MR Path (step 6 detected an MR)

When `EXISTING_MR` is non-empty, the cascade is shorter:
- Steps 7-9 skipped (no body generation, no create call).
- Step 10 fires (auto-enters `simo-dev:mr-followup`).

This is the "follow-up commit to an open MR" flow.

## Protected-Branch Path (working directly on master/main/develop)

Triggered by the detection above — an out-of-flow commit on a protected branch (the canonical flow always works in a worktree) has no MR, so the gate moves to the commit itself.

1. Stage per step 4 rules (explicit file list, no `git add -A`).
2. Show a mermaid preview instead of the full diff: derive `$MRTMP` with the step-7 line (`mkdir -p` included — this path skips step 7), run `git diff --staged > "$MRTMP/staged_diff.txt"` as its OWN Bash call, never chained with `git add`, then generate the presentation via `lib/mr_presentation.py --mode create` on that diff and render the widget per step 8 (informational). If `show_widget` is unavailable or errors → show `git diff --staged --stat` only (stat, never the full diff; full diff only if the user asks). List untracked-but-staged files explicitly.
3. Show the computed commit message (step 3 rules), then ONE gate covering BOTH commit and push — never ask a second confirmation at push time:

   > "Commit + push to `<branch>`? [y / edit-message / abort]"

   - `y` → commit + `git push`, no further prompts.
   - `edit-message` → take the user's replacement message, re-present this gate.
   - `abort` → stop; nothing committed (staged files stay staged).
4. Steps 6-10 never run: the branch is its own target, no MR exists.

Nothing skips this gate — pushing to a protected branch always asks (CLAUDE.md rule). `--no-mr` is a no-op here (there is no MR step to skip).

## WIP Opt-Out

If `--no-mr` arg OR `DEV_COMMIT_NO_MR=1` env var:
- Steps 1-5 execute.
- Steps 6-10 SKIPPED.
- Tell the user: "Commit pushed. MR creation skipped (WIP opt-out). To create MR later, re-run simo-dev:commit without --no-mr."

## Commit Message Recovery

The user does not see the commit message before step 4 records it, and push (step 5) + MR creation (step 9) both now happen immediately with no gate — there is no interactive window left to catch a bad message before it's public.

- Want to fix it anyway → `git commit --amend -m "<new>"` then force-push (always asks separately per CLAUDE.md — never silent). Only worth it before anyone has reviewed or pulled the branch.
- Otherwise → add a fixup commit and accept the original message stays in history.
