# Plan critique at the step 7.5 gate

Loaded on demand from `task-intake` `SKILL.md` step 7.5 when this gate critiques a plan: a plan from step 7's writer (first entry and every re-entry after `edit` or `apply and re-present`), or a fast-path plan with a `risk:` line (`lib/fast-path.md`: at most one round). A bare plan skips the critique (its escape hatch stays in `SKILL.md` step 7.5). "This section" means `SKILL.md` step 7.5; "the gate below" and "the summary below" are that step's gate and Plan summary.

This session never reads the plan: `plan-reviewer` reads it from disk, and its fixes go to the writer (`SKILL.md` step 7's agent id). A fast-path plan has no writer: this session wrote it, and applies the fixes itself with targeted `Edit`s.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
PLAN_CRITIQUE_MODEL=$(python3 "$R/lib/model_strategy.py" get plan_critique)
if [ "$PLAN_CRITIQUE_MODEL" = "none" ]; then
  PLAN_CRITIQUE_MODEL=""
elif [ -z "$PLAN_CRITIQUE_MODEL" ]; then
  PLAN_CRITIQUE_MODEL="sonnet"
fi
```

Empty (only reached via the explicit `none` sentinel now, or a bare plan, per `SKILL.md` step 7.5) → no dispatch. Non-empty → dispatch an `Agent` with `subagent_type: simo-dev:plan-reviewer`, `model: $PLAN_CRITIQUE_MODEL`, `run_in_background: false` (same reasoning as `code-review-protocol` item 2: the gate must see the findings before the user is asked to proceed). The prompt carries paths, never content: the plan's absolute path and, when step 7 wrote one, the brief's (`<brief-dir>/brief.md`, the gathered context), both to read in full from disk; a fast-path plan has no brief, so its prompt carries the decided brief block inline instead. Parse the response's findings: one `[severity] location — message → fix: …` or `[severity] location — message → decide: …` line each (`plan-reviewer`'s output contract; the tail is the last ` → fix:` / ` → decide:` on the line).

**Auto-apply objective findings, before the gate is shown.** Classify each parsed finding by its tail, never by severity:

- **Objective** — a `→ fix:` finding, at any severity. Applied right here, before the gate below: `SKILL.md` step 7's **Resume** carrying every `fix:` line of the round (`SendMessage` to the writer; its fallback when unavailable), and read its report (a fast-path plan: one targeted `Edit` per `fix:` here instead). A finding it lists under `rejected:` is not applied; it reaches the gate as `⚠ Writer rejected`. Never a writer rewrite for these (a rewrite belongs to judgment findings, where the human sees and approves the result).
- **Judgment** — a `→ decide:` finding, at any severity. A line missing the tail counts as a decide finding with no recommendation (fail safe). Judgment findings wait for the user at the gate. A decide finding **has a recommended option** unless its tail reads `(no recommendation)`.

Round 1 is the initial dispatch just above. Once the writer reports a round's objective fixes applied, re-dispatch `plan-reviewer` — same model, same call shape, same prompt: it re-reads the plan from disk, so it sees the fixed file. Repeat classify → autofix → re-review until the plan header's `Critique rounds` cap is reached (`simo-dev:plan-write` "Header": risk-scaled 1–3, as `plan_summary.py` or the writer's report prints it; missing or unreadable → 2). The cap counts every `plan-reviewer` dispatch on this plan, gate re-entries after `apply and re-present` included. On the last allowed round, still apply its `fix:` findings, but do not re-dispatch: only its `decide:` findings go to the gate.

Carry judgment findings forward cumulatively across rounds — each re-dispatch is a stateless call with no memory of earlier rounds, so a judgment finding from round 1 that round 2's dispatch doesn't re-surface (new objective findings crowding it out, or plain non-determinism) must still reach the gate. Keep a **gate findings set**, started empty at every entry to the top of this section that dispatches a critique (a re-entry after `edit`, or after `apply and re-present` while rounds remain, so applied findings are never re-applied); a re-entry after the cap, which dispatches nothing, starts it from the findings the last pass left unapplied (e.g. `(no recommendation)` ones), never empty. The set holds every judgment finding seen across all rounds, deduped by the **(location, message) pair**, the message taken without its tail — never by location alone, since a location is a single string (a task or a file reference) and `plan-reviewer`'s independent check categories can legitimately raise two distinct findings against the same one. On a genuine (location, message) match across rounds, keep the latest occurrence's tail. Exact-string match only: a differently-worded restatement of the same finding across rounds is not deduped by this rule and can surface as a separate entry — accepted, to keep the check simple. This set — not any single round's raw output — is what the `⚠ Plan critique` list, the apply options and the `Recommended:` line in `SKILL.md` step 7.5 reference.

The summary below comes from `plan_summary.py`, run after the last round, so it reflects the post-fix plan.
