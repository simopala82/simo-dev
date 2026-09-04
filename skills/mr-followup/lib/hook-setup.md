# Auto-trigger after `git push` — one-time hook setup

Add a PostToolUse hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "if echo '$CLAUDE_TOOL_INPUT' | grep -q 'git push'; then echo 'REMINDER: Run simo-dev:mr-followup to check pipeline status and MR/PR review comments'; fi"
      }]
    }]
  }
}
```
