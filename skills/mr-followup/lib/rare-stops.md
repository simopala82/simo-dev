# Rare stops: round boundary, cap reached

Loaded on demand from `mr-followup` `SKILL.md`. Step and item numbers refer to `SKILL.md`; `<worktree-abs>`, `<branch>`, `<base>` and `CAP` are bound there.

## Round-boundary stop

A user-requested ad-hoc edit, or a `simo-dev:review-loop` / `simo-dev:plan-execute` run, asked for mid-run waits for a round boundary: finish the current round through its commit and push (step 7 items 5-6), or stop before applying anything. Then report via step 7.4 (`answer=round-boundary`) and stop, overriding item 6's loop-back to step 2. The work is committed through `simo-dev:commit` without `--no-mr`, which re-enters this skill.

## Cap reached

Round N == CAP with medium+ findings. Before taking this branch, apply step 1.5's escalation: run the validity filter (step 7 item 2) on this round's `high`/`critical` findings first — this branch otherwise never runs it, so the escalation would hang on a verdict the procedure never produces. A surviving `high`/`critical` here **or in any earlier round** means `CAP` is already 5, so the loop continues and that round takes the fix + commit + push branch, not this one.

Otherwise: report via step 7.4 (`answer=cap`; full unresolved list, especially `critical`, and the collected pipeline status), do NOT auto-push, and hand off to `simo-dev:code-review-protocol` (receiving side).
