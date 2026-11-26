---
name: spawn-task
description: Use when the user wants to spin off a follow-up into its own background session via `mcp__ccd_session__start_session`. Triggers include "spawn task", "spawn this", "spin this off", "background task this". Formats title/prompt/background and starts it directly (proactive follow-ups land as a proposal card the user starts or ignores).
---

# Spawn Task

Wraps `mcp__ccd_session__start_session` so the user can ask casually ("spawn a task for X") and get a well-formed session (or proposal card) without dictating fields.

## When to invoke

- User explicitly asks to spawn / spin off / background a follow-up.
- Proactively, no confirmation needed, when you identify: a bug or gap in a skill currently in use; an out-of-scope bug or improvement in the project being worked on; a fact, convention, or gotcha worth remembering for future sessions (destination: the repo's CLAUDE.md, a reference file, or memory) — spawn directly (a follow-up for another repo asks first, see the `cwd` bullet below). None of these cases fires for an In-scope improvement (files already in the task's diff/plan): do it in-task, per `docs/canonical-flow.md` "In-scope improvements".
- Never spawn a documentation fix this session can reach directly — make the edit here instead, on its own branch (or in the current task when the file is already in its diff), with the usual gates.

## How

1. **Infer the fields from context — do not ask back unless truly ambiguous:**
   - `initiation`: `user_asked` when the user asked for it in words (starts now — also for "spawn this" / "spin this off", where an `offer` card would only repeat the request); `own_initiative` for a proactive follow-up (a proposal card, nothing runs until the user starts it).
   - `context`: `fresh`.
   - `title`: imperative, <60 chars, starts with verb (`Fix`, `Add`, `Document`, `Investigate`).
   - `prompt`: self-contained. Spawned session has NO memory of this conversation — include file paths, line numbers, repro steps. No "as discussed" / "the bug we found". Frame it as ownership: the session owns area X and its result, with the done-criteria and the constraints, not a list of ad hoc edits to make. While the simo-dev flow is active and the direction, scope and files are already decided, add the decided-brief block (`docs/canonical-flow.md` "Decided brief": `direction: decided`, then `decisions:` with one `- <decision>` line per decision taken, then a `risk:` line when something is risky; the keys `direction: decided`, `decisions:` and `risk:` stay verbatim in English whatever the prompt's language — task-intake matches them literally — while the decision texts follow the prompt's language) and a `simo-dev:task-intake` invocation line — the flow is opt-in, so the block alone stays inert. Anything still open, or the flow is off (the invocation line would switch it on in the child) → no block. Write it in the language of the **current session/user**, never a fixed language (keep paths, identifiers, code as-is) — the spawned session mirrors the prompt's language, so writing in the wrong one produces a spawned session in the wrong language. Open the prompt with a marker line identifying it as a spawned task, in the same language as the rest of the prompt — reference form: `[SPAWNED TASK — you are the session created specifically for what follows. This is not a live report; do not call start_session again for this same content.]` — so the spawned session's own first turn recognizes it is the assignment, not a live report to act on. While the simo-dev flow is active, also add the `parent task:` line (`docs/canonical-flow.md` "Parent task"), so the follow-up's task links back to this one; never for a hand-off of this same task.
   - `background`: required with `fresh`, ≥80 chars — the overall goal and the constraints so far, for a reader who has not seen this conversation. No paths, no code.
   - `cwd`: omit it when the follow-up targets the current project. Another repo → set only with `user_asked`, which for a proactive follow-up means: `own_initiative` cannot set `cwd`, and the session would start in this project (sharing this checkout, or a worktree of the wrong repo): say it in one line to the user instead, and on their yes start it with `user_asked`, `cwd` = that repo root, `use_worktree` `true`. When set, **always a real repo root, never a subdirectory** — never a `.claude/plugins/cache/` or `.claude/plugins/marketplaces/` path (those are copies, not the source; 2026-07-28: worktrees found under `myrepo/.claude/.claude/worktrees/` and inside two `.claude/plugins/cache/` dirs). Unsure which repo root → ask the user rather than guessing a path.
   - `use_worktree`: `true` for any follow-up that ends in a branch/MR of its own — omitted from a plain checkout, the new session shares this checkout's folder.

2. **Confirm the tool is available, then spawn directly.** Before calling `mcp__ccd_session__start_session`, confirm it is callable this session: when it is not already — listed only as a deferred tool, or not listed at all — run `ToolSearch` `select:mcp__ccd_session__start_session` once first, and never skip that load because the name is not listed. No schema back, or no `ToolSearch` tool at all (not available) → tell the user explicitly (e.g. "this session doesn't have the start-session capability available; start it yourself, or use a session where it's enabled") and stop, rather than calling the tool and surfacing whatever raw error results. Available → do not gate on confirmation from the user — for `own_initiative` the proposal card itself is the confirmation surface. Only exception: a proactive follow-up for another repo, which asks first (the `cwd` bullet above).

3. **After the call**, one-line ack with the title. Then decide whether it stays linked: keep it a child (nested here, its turn ends notify this session) only when it depends on this task — a subtask, a result this session still needs, or work on this session's branch. Anything else is independent: once the tool result says it started, call `mcp__ccd_session_mgmt__detach_session` with its id, so it becomes a normal top-level session and stops notifying this one (not yet callable → `ToolSearch` `select:mcp__ccd_session_mgmt__detach_session` once; no schema, or the call is refused, e.g. a child sharing this session's worktree → leave it linked, no report). An `own_initiative` card has no id until the user starts it: detach it at its first note, with no report. Continue the original task.

   A linked child's notes are not a cue to report: answer only when it is blocked on a question, gave up or was stopped, went off-brief or touched this session's branch (correct it via `mcp__ccd_session_mgmt__send_message`), unblocks a step this session still owes, finished its whole task (work done, or MR ready for merge — then verify its result before reporting), or anything else needs this session to act; ignore mid-flow progress.

## Common shapes

- **This plugin's own bug** → no `cwd` when it lives in the current project, prompt references the skill file (e.g. `.claude/skills/<name>/SKILL.md`, or names the skill and the symptom) with the broken behavior.
- **Project follow-up** → no `cwd` for the current project (another repo → the `cwd` bullet above), prompt includes file paths + line numbers from the current investigation.
- **Cross-repo follow-up under an umbrella** → the current work has an umbrella ticket (`simo-dev:task-intake` step 2.5) and the follow-up lands in a different project/repo: the prompt says to open a **Sub-task under `<UMBRELLA-KEY>`**, not a new standalone ticket.
- **Memory / convention to remember** → no `cwd` when the target file is in the current project (else the `cwd` bullet above), prompt says "add to `.claude/CLAUDE.md` / `.claude/reference/<domain>.md`" with the exact fact.

## When the follow-up touches the current branch

If the spawned follow-up would touch the branch/worktree this session is still working on, say so explicitly in the `prompt` (e.g. "do not commit on `<branch>`, open a separate branch") — or spawn only after this session's branch is merged. If the target content exists only on an unmerged branch already checked out in a worktree this session may still use, the prompt must also say to create a **separate worktree** off that branch's tip, never inside the existing worktree — git only refuses a second checkout of the *same* branch name, not a new one cut from its tip:

```bash
git worktree add <repo>/.worktrees/<child-name> -b <child-branch> <source-branch>
```

## Correcting a spawned task after the card is started

`mcp__ccd_session__dismiss_task` cannot withdraw a proposal the user already started — it responds that the task was already started and cannot be withdrawn, nothing changed. A replacement proposal doesn't fix this either — it opens a **separate** session instead of correcting the one already running. To correct a brief that went stale after the start, use `mcp__ccd_session_mgmt__send_message` on that session's id (from `mcp__ccd_session_mgmt__list_sessions`) — it arrives as a user turn in the spawned session. When either tool is not yet callable (listed only as deferred, or not listed at all), run one `ToolSearch` `select:mcp__ccd_session_mgmt__list_sessions,mcp__ccd_session_mgmt__send_message` first, never skipped because the names are unlisted. No schema back for one or both, or no `ToolSearch` → tell the user the running session's brief is stale and give them the correction text to send it by hand.

## Anti-patterns

- Asking the user to draft the prompt — that defeats the purpose.
- Vague prompts ("look into the thing we discussed") — the spawned session won't know.
- Confirmation prompts before calling the tool — spawn directly (except the cross-repo proactive case in the `cwd` bullet).
- Using it for trivial inline fixes — if it takes 30 seconds in the current session, just do it.
