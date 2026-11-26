# Auto-trigger after `git push` — no setup needed

The plugin registers `hooks/mr_followup_remind.py` as a PostToolUse `Bash` hook: after a `git push` it reminds you to run `simo-dev:mr-followup` — only while the simo-dev flow is active (`lib/flow_state.py`); outside it the push passes silently. After a `glab mr merge` / `gh pr merge`, or a `glab api` call on the GitLab merge endpoint, it also reminds to run step 8.5 when the session holds a task with no outcome, flow flag or not. The published plugin wires it through its own `hooks/hooks.json`; in the source checkout it is wired from `~/.claude/settings.json` instead.

Do not add a hand-written `git push` reminder hook to `~/.claude/settings.json` on top of it — a working one would remind twice on every push. If an earlier version of this page had you add one, remove it: that snippet single-quoted `$CLAUDE_TOOL_INPUT`, so it never fired.
