---
name: spawn-task
description: Use when the user wants to spin off a follow-up into its own background session via `mcp__ccd_session__spawn_task`. Triggers include "spawn task", "spawn this", "spin this off", "background task this". Formats title/prompt/tldr/cwd and spawns directly (no confirmation — user dismisses the chip).
---

# Spawn Task

Wraps `mcp__ccd_session__spawn_task` so the user can ask casually ("spawn a task for X") and get a well-formed chip without dictating fields.

## When to invoke

- User explicitly asks to spawn / spin off / background a follow-up.
- Proactively, no confirmation needed, when you identify: a bug or gap in a skill currently in use; an out-of-scope bug or improvement in the project being worked on; a fact, convention, or gotcha worth remembering for future sessions (destination: the repo's CLAUDE.md, a reference file, or memory) — spawn directly.
- Never spawn a documentation fix this session can reach directly — make the edit here instead, on its own branch, with the usual gates.

## How

1. **Infer the four fields from context — do not ask back unless truly ambiguous:**
   - `title`: imperative, <60 chars, starts with verb (`Fix`, `Add`, `Document`, `Investigate`).
   - `prompt`: self-contained. Spawned session has NO memory of this conversation — include file paths, line numbers, repro steps, what to change. No "as discussed" / "the bug we found". Write it in the language of the **current session/user**, never a fixed language (keep paths, identifiers, code as-is) — the spawned session mirrors the prompt's language, so writing in the wrong one produces a spawned session in the wrong language. Open the prompt with a marker line identifying it as a spawned task, in the same language as the rest of the prompt — reference form: `[SPAWNED TASK — you are the session created specifically for what follows. This is not a live report; do not call spawn_task again for this same content.]` — so the spawned session's own first turn recognizes it is the assignment, not a live report to act on.
   - `tldr`: 1–2 plain sentences for the chip tooltip. No paths, no code.
   - `cwd`: **always a real repo root, never a subdirectory** — the harness derives a spawned worktree from the cwd, so a subdirectory lands it outside any sane location (2026-07-28: worktrees found under `myrepo/.claude/.claude/worktrees/` and inside two `.claude/plugins/cache/` dirs). Resolve based on the target:
     - Default to the current repo's root (`git rev-parse --show-toplevel` from the invoking session's cwd) — never a `.claude/plugins/cache/` or `.claude/plugins/marketplaces/` path (those are copies, not the source). If the follow-up clearly targets a different repo than the one currently open, ask the user which repo root to use rather than guessing a path.
     - Project bug → that project's root (current `cwd` if same project, and only if it IS the root)
     - General → omit `cwd` (defaults to current project)

2. **Confirm the tool is available, then spawn directly.** Before calling `mcp__ccd_session__spawn_task`, confirm it is actually present this session (in the tool list, or a prior successful `ToolSearch` for it). Not available → tell the user explicitly (e.g. "this session doesn't have the spawn-task capability available; spawn it yourself, or use a session where it's enabled") and stop, rather than calling the tool and surfacing whatever raw error results. Available → do not gate on confirmation from the user — the chip itself is the confirmation surface.

3. **After spawning**, one-line ack with the title. Continue the original task.

## Common shapes

- **This plugin's own bug** → `cwd: <current repo's root>`, prompt references the skill file (e.g. `.claude/skills/<name>/SKILL.md`, or names the skill and the symptom) with the broken behavior.
- **Project follow-up** → `cwd: <project root>`, prompt includes file paths + line numbers from the current investigation.
- **Cross-repo follow-up under an umbrella** → the current work has an umbrella ticket (`simo-dev:task-intake` step 2.5) and the follow-up lands in a different project/repo: the prompt says to open a **Sub-task under `<UMBRELLA-KEY>`**, not a new standalone ticket.
- **Memory / convention to remember** → `cwd: <current repo's root>`, prompt says "add to `.claude/CLAUDE.md` / `.claude/reference/<domain>.md`" with the exact fact.

## When the follow-up touches the current branch

If the spawned follow-up would touch the branch/worktree this session is still working on, say so explicitly in the `prompt` (e.g. "do not commit on `<branch>`, open a separate branch") — or spawn only after this session's branch is merged. If the target content exists only on an unmerged branch already checked out in a worktree this session may still use, the prompt must also say to create a **separate worktree** off that branch's tip, never inside the existing worktree — git only refuses a second checkout of the *same* branch name, not a new one cut from its tip:

```bash
git worktree add <repo>/.worktrees/<child-name> -b <child-branch> <source-branch>
```

## Correcting a spawned task after the chip is clicked

`mcp__ccd_session__dismiss_task` cannot withdraw a chip the user already clicked — it responds that the task was already started and cannot be withdrawn, nothing changed. A replacement chip doesn't fix this either — it opens a **separate** session instead of correcting the one already running. To correct a brief that went stale after the click, use `mcp__ccd_session_mgmt__send_message` on that session's id (from `mcp__ccd_session_mgmt__list_sessions`) — it arrives as a user turn in the spawned session.

## Anti-patterns

- Asking the user to draft the prompt — that defeats the purpose.
- Vague prompts ("look into the thing we discussed") — the spawned session won't know.
- Confirmation prompts before calling the tool — spawn directly.
- Using it for trivial inline fixes — if it takes 30 seconds in the current session, just do it.
