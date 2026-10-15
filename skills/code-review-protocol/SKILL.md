---
name: code-review-protocol
description: Use when requesting a code review (dispatching the reviewer agent) or processing review feedback before implementing fixes — covers both sides of the review exchange.
---

# Code Review Protocol

## Requesting a review

Never skip review because the change "is simple". Dispatch the reviewer with precisely crafted context — never your session history — so it evaluates the work product, not your thought process.

1. Compute the range:
   ```bash
   REPO="<absolute path of the repo under review>"   # the Bash cwd does NOT follow the worktree
   BASE_SHA=$(git -C "$REPO" rev-parse origin/develop)   # or the task's base commit
   HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   CODE_REVIEW_MODEL=$(python3 "$R/lib/model_strategy.py" get code_review)
   ```
   Same path goes in the `REPO:` line of item 2. Resolving the range from the cwd while the marker names another repo hands the reviewer a range from repo A and files the record against repo B.

   **Range diff empty but the working tree is dirty — most commonly `HEAD_SHA == BASE_SHA` (no commits yet on this branch).** Not rare: `simo-dev:review-loop` is invoked by `simo-dev:plan-execute` right after implementation, before `simo-dev:commit`, so the branch can legitimately carry zero commits while the working tree has real changes. A no-op/empty commit ahead of `BASE_SHA` triggers the same thing without SHA equality, so gate on the diff itself, not the SHA comparison: `git -C "$REPO" diff --quiet "$BASE_SHA...$HEAD_SHA"` (exit 0 = empty) together with a non-empty `git -C "$REPO" status --porcelain --untracked-files=all` means the range is empty by construction while real changes exist — detect this right here, before dispatching, and fall back to the working tree instead of the range:
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/diff_capture.py" "$REPO" --include-untracked -- HEAD
   DIFF_CAPTURE_RC=$?
   [ $DIFF_CAPTURE_RC -eq 0 ] || { echo "STOP: diff capture failed (rc $DIFF_CAPTURE_RC) — do not dispatch the reviewer on a partial/empty diff."; exit 1; }
   ```
   When this fallback fires, say so in the dispatch prompt (item 2) — e.g. `Diff: working tree vs HEAD — no commits yet on this branch` — so the reviewer's `file:line` references match what `git diff HEAD` actually shows, not a range. The two SHAs computed above are then only useful for the `REPO:`/review-record marker, not for reconstructing the diff. The capture above is gated by size (`lib/diff_capture.py` — full diff under ~50k chars, `git diff --stat` + per-file slices over it, same threshold everywhere in this plugin) and handles tracked and untracked changes in one call. Skip this fallback capture entirely when the caller (e.g. `simo-dev:review-loop`) already supplies a computed, gated diff in its own dispatch prompt — see that caller's own instructions; this fallback exists only for callers reaching this protocol with no diff already in hand (e.g. a direct, standalone `code-review-protocol` invocation).
2. Dispatch an Agent with `subagent_type: simo-dev:code-reviewer`, **`run_in_background: false`** — background is the tool's default, and a background reviewer leaves the main thread free to edit files that the record will then certify as reviewed — and `model: $CODE_REVIEW_MODEL` whenever that variable is non-empty (omit the `model` parameter entirely when it is empty, so the reviewer inherits the caller's tier — today's behavior, unchanged). Pass: a brief description of what was built, the plan/requirements (file path or task text) — including the design spec path (`docs/specs/*.md` or similar) whenever the plan cites one, so the reviewer checks the diff against the spec's full requirements and not only the plan's per-task `Files:`/`Interfaces:` lines (a plan snippet broader than its own task line is not automatically drift — `simo-dev:plan-write` mirrors an approved spec faithfully; incident 2026-09-11: spec-mandated content dropped by `plan-execute` dispatches went undetected through a full review-loop pass and surfaced only in `mr-followup`, once that later review happened to read the spec directly) — and both SHAs. **The prompt's first line must be `REPO: <absolute path of the repo under review>`** — `hooks/review_record.py` fires on `SubagentStop` and reads that line back from line 1 of the **first** user message of the agent's transcript to attribute the review record, and without it the review does not count as coverage and `simo-dev:commit` will ask for another one. It must be line 1 — the hook reads **only** line 1, so the same text further down, or mid-sentence, is ignored on purpose; the same rule makes a `SendMessage` resume unable to supply or re-point a marker, since it appends a *later* user message. That is a security boundary, not a formatting rule: everything below line 1 may carry text the flow does not control (a pasted diff, a colleague's ticket body), and a `REPO:` line there once manufactured coverage. Never put untrusted content on line 1. The agent's output contract (`[severity] file:line — message` + verdict) is defined in the agent itself. **Re-reviewing?** When `SendMessage` is not already callable — listed only as a deferred tool, or not listed at all — run `ToolSearch` `select:SendMessage` once first; never skip that load because the name is not listed. Resuming an earlier reviewer with `SendMessage` records coverage the same way, and needs no marker of its own — but only when both hold: `SendMessage` is callable after that load (no schema back, or no `ToolSearch` tool at all, fails this), and that reviewer was dispatched through this protocol, since the marker it reuses is the one in its first message. A reviewer from `mr-followup` step 6 or `mr-review` never had one and a resume does not add it. Either condition failing means a fresh dispatch instead, per this item, with `review-loop`'s iteration ≥ 2 memory block appended below the `REPO:` line. A resume also returns a `resumed … in the background` acknowledgement: the findings arrive later, in that agent's task notification, and the tree must not change meanwhile.
3. Act on findings: `critical`/`high` immediately, `medium` before proceeding, `low` on a diff file acted on per `docs/canonical-flow.md` "In-scope improvements" (otherwise noted) — filtered through the validity filter below first.

## Receiving feedback (validity filter)

Technical evaluation, not emotional performance. For EVERY finding — from the reviewer agent, an external MR reviewer, or the user:

1. **READ** the complete feedback without reacting.
2. **UNDERSTAND** — restate the requirement in your own words, or ask.
3. **VERIFY** the claim against the codebase (does the code actually do what the finding says?).
4. **EVALUATE** — technically sound for THIS codebase? Breaks existing functionality? Is there a reason the current code is the way it is? Does the reviewer have full context?
5. **RESPOND** — technical acknowledgment or reasoned push-back.
6. **IMPLEMENT** — one item at a time, testing each; then verify no regressions.

Rules:

- **Unclear item → stop everything.** If ANY item is unclear, ask before implementing anything — items may be related; partial understanding produces wrong fixes.
- **No performative agreement.** Never "You're absolutely right!" / "Great point!" / thanks of any kind. State the fix ("Fixed — <what changed>") or push back. If you catch yourself writing thanks: delete it, state the fix.
- **YAGNI check.** Reviewer says "implement X properly" → grep for actual usage first. Unused → propose removal instead of building it out.
- **Enumeration claims are findings too.** A comment or docstring using "only" / "always" / "never" / "the cases are" is itself a claim about a closed set — check for a third case before accepting it as accurate, don't take it as background truth.
- **Minimum diff on fixes.** A finding's fix must trace to the finding itself — no refactoring adjacent code, no abstraction/configurability for cases that can't happen, unless the finding explicitly names it. In-scope improvements (dead-code deletion on evidence included) are allowed per `docs/canonical-flow.md` "In-scope improvements".
- **Implementation order:** clarify → blocking (breaks/security) → simple (typos, imports) → complex (refactoring, logic).
- **Push back** with technical reasoning (working tests, platform constraints, prior architectural decisions) when the finding is wrong — never blind-comply, never argue defensively. Wrong push-back → correct factually ("Verified — you're correct, fixing") without apology theater.
- Can't verify a claim → say so and ask for direction instead of proceeding.
- GitHub inline review comments get replies in-thread (`gh api .../comments/{id}/replies`), not top-level PR comments.
