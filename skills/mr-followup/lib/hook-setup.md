# Auto-trigger after `git push` — no setup needed

The plugin registers `hooks/mr_followup_remind.py` as a PostToolUse `Bash` hook: after a `git push` it reminds you to run `simo-dev:mr-followup`. The published plugin wires it through its own `hooks/hooks.json`; in the source checkout it is wired from `~/.claude/settings.json` instead.

Do not add a hand-written `git push` reminder hook to `~/.claude/settings.json` on top of it — a working one would remind twice on every push. If an earlier version of this page had you add one, remove it: that snippet single-quoted `$CLAUDE_TOOL_INPUT`, so it never fired.
