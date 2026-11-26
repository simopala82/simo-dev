# Review-loop steps 5-6: validity filter and fixes

Loaded on demand: by the step 4.5 fixer subagent, whose prompt names this file, or by `review-loop` itself when the `Agent` tool is unavailable (step 4.5 then runs these steps in the main session). Step numbers refer to `review-loop` `SKILL.md`; `<worktree-abs>`, `<BASE_SHA>` and `<BASE>` are the literals step 2 printed.

## 5. Validity filter (code-review-protocol rigor)

For each medium+ finding and each collected `low`, before fixing: (1) technically correct for THIS codebase (file/line exists, problem real, fix matches the language semantics)? (2) would the fix break existing functionality (check callers/tests)? (3) is the current code deliberate (comments, commits, sibling code)? (4) does the reviewer have full context (it saw a diff, not the repo)?

- **Pass** → step 6. **Fail** → drop, log `rejected: [severity] file:line — message → reason`. **Ambiguous** → drop, surface as unresolved for the user.

No "you're absolutely right" / "great point" — just decide.

Then, before step 6 applies anything, run the branch-point check once on every passing `low`'s targets — its cited `file:line` plus every line its fix will replace or remove, or both sides of a pure insertion (`docs/canonical-flow.md` "In-scope improvements"):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/branch_point_check.py" --repo "<worktree-abs>" --base-sha "<BASE_SHA>" "<file1>:<line1>" "<file2>:<line2>" ...
```

Skip the call when no `low` passed. Keep the verdicts for step 6. rc ≠ 0, or a target with no output line → `error` for that `low`.

## 6. Auto-apply fixes

Apply passing fixes via Edit/Write; log each as `[severity] file:line — fix description`. A fix that turns out to need behavior changes not visible at filter time → abort that fix, log `escalated-during-fix: [severity] file:line — reason`, move to unresolved.

Right after applying each In-scope improvement (an applied `low` whose step-5 targets were all `pre-existing` and that does not complete the branch's own change, a dead-code deletion with its `rg -uu` evidence), append it to the Also-fixed ledger (`docs/canonical-flow.md` "In-scope improvements") — one single-quoted `file — what — why` argument per improvement, `file` relative to the repo root exactly as `git status --porcelain` prints it, a `'` inside a line written as `’`:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/also_fixed_ledger.py" add --repo "<worktree-abs>" '<file> — <what> — <why>' \
  || echo "LEDGER_FAILED rc=$? — the improvement is not recorded; fix the --repo literal and re-run"
```

A later iteration that reverts one runs the same block with `drop` in place of `add` — only for a line actually added to the ledger. Skipped on a protected branch (`simo-dev:commit`'s protected-branch detection): that path never reads or clears the ledger.

After applying every passing fix for the round, run the plan-drift check on the files just written:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/check_plan_drift.py" --repo "<worktree-abs>" --base "<BASE>" --changed-file <file1> <file2> ...
```

`<BASE>` = the `REVIEW BASE:` literal step 2 printed: a stacked parent's plan is not in `origin/<default>`, so the default base would flag it as live.

`<file1> <file2> ...` = every unique file path from this round's **applied-fix** log entries only (never the `escalated-during-fix:` entries this same step can also log — that file was never written, so it has nothing to check), in the same repo-root-relative form `git -C "<worktree-abs>" diff --name-only` prints (the same form a plan's `**Files:**` bullet declares) — the fix log's own `[severity] file:line` entries are observed to carry paths in that form in this plugin's existing diff-derived findings, just strip the trailing `:line`. Never a `lib/`-relative shorthand or an absolute path: `plans_mentioning()` does an exact string match against the plan's declared paths, so a differently-rooted path silently matches nothing (skip the call entirely when the round applied zero fixes — nothing changed to check). rc 0 → nothing to do. rc 1 → for each `<plan> mentions <file>` line printed, read that plan around the embedded target for `<file>` and, if the embedded code/text still shows the pre-fix version, resync it with a targeted `Edit` before continuing to step 7 — not a full plan-write regeneration, and never a reason to abort the round: a false positive (the plan cites the file but the reviewed passage is unaffected) costs a few seconds of reading, nothing to fix. rc 2 → note the error in the fix log and continue; a script failure never blocks the round.
