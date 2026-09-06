---
name: model-strategy
description: View or change which model each simo-dev dispatch category (code_review, plan_critique, explore_research) uses. Invoke anytime — first use or later changes. Standalone utility, exempt from task-intake's mandatory entry (same class as onboard-repo/spawn-task — low blast radius, reversible config file). Triggers "model strategy", "model config".
---

# Model Strategy Config

Wraps `lib/model_strategy.py` so viewing/changing which model backs each dispatch category does not require the raw CLI.

## Steps

1. **Show current state:**
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   for c in code_review plan_critique explore_research; do
     v=$(python3 "$R/lib/model_strategy.py" get "$c")
     printf '%s: %s\n' "$c" "${v:-(default)}"
   done
   ```
   Explain what "(default)" means per category: for `code_review`, default = inherit the caller's tier; for `explore_research`, default = `sonnet`; for `plan_critique`, default = `fable` (automatic plan-critique dispatch happens) — set it to `none` (lowercase, case-sensitive) to fully disable dispatch instead.

2. **Ask what to change** — present the three category names plus a "just show me, no change" option. On a category pick, ask for the new model value as free text: the script enforces no whitelist of model names, so accept whatever the user types, but mention `sonnet`/`opus`/`fable`/`haiku` as the names in common use — not a closed list, don't phrase it as one. For `plan_critique` specifically, also mention `none` as the way to fully disable automatic plan-critique dispatch — it has no special meaning for the other two categories.

3. **Confirm and write:**
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/model_strategy.py" set "<category>" "<value>"
   ```

4. **Confirm the change** — re-run the loop from step 1 and show the user the new state.

## Reverting to default

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/model_strategy.py" set "<category>" ""
```

This is how a user clears a category back to its built-in default — `get_value` already treats an empty string the same as unconfigured, so there is no separate "unset"/"delete" verb. What "default" means varies per category: inherit-the-caller's-tier for `code_review`, `sonnet` for `explore_research`, `fable` for `plan_critique`.

For `plan_critique` specifically, setting it to the literal string `none` (`model_strategy.py set plan_critique none`) fully disables automatic plan-critique dispatch — not a new CLI verb (the CLI still only has `get`/`set`), just a value that `plan_critique`'s one call site (`simo-dev:plan-write`'s own review gate) interprets specially. `code_review` and `explore_research` have no such disable concept; `none` is not special for them.
