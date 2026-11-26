# `subagent` — one clean-context executor

Loaded on demand from `plan-execute` `SKILL.md` step 1 when the delegation gate prints `Execution: subagent`. It replaces `SKILL.md` step 2 (direct execution); step 3's Report still follows. `$MODEL` = the preflight's `model:` value, empty when it printed `inherit caller's tier`.

This session keeps only the orchestration: the dispatch, the user's answers, the ledger. It never reads the plan's target files or the diff to check the executor's work: `simo-dev:review-loop` step 0 re-runs the verification fresh, and its reviewer reads the diff.

1. Dispatch one `Agent` call with `subagent_type: simo-dev:plan-executor` (its 1 h prompt-cache TTL keeps a build or test run longer than 5 minutes from rewriting its whole context; not listed → the default `subagent_type`), `run_in_background: false`, `model: $MODEL` when non-empty (omit `model` when empty). The prompt carries, each with the literals already substituted (the executor never sees this skill):
   - an ownership line: the executor owns the outcome of the whole plan, every task in order, within the plan's declared Files;
   - the plan's absolute path, to read in full from disk (never paste the plan into the prompt: the executor reads it itself), the worktree's absolute path with the standing `git -C "<worktree-abs>"` discipline, and the recorded spec path if any, with the plan-fidelity note from `SKILL.md` step 1 verbatim;
   - the direct-execution rules of `SKILL.md` step 2: follow each task's steps exactly, run every verification (`simo-dev:verify-done`), `simo-dev:tdd` for feature/fix work and `simo-dev:debug` on an unexpected red, capture surprising discoveries inline (CONTEXT.md / ADR / reference file) and name each in the report;
   - `SKILL.md` step 2's base-freshness checkpoint block, run after each task: rc `1` or `4` → stop at once and report `STOP-BASE`, never rebase or park, copying into the report the rc, the `CHECK BASE:` value and every `note:` / `STOP:` stderr line verbatim;
   - unless `<ledger-base>` is empty or the branch is protected, the ledger paragraph's branch-point check block (`SKILL.md` step 2) and its rules, worded as in `lib/agent-dispatch.md` item 1 (the `canonical-flow.md` path, the check before each improvement edit, one tag per improvement), but the improvements go in the report's `improvements:` lines; with no check block only `exempt` and `completes-branch` apply;
   - the line "Write throwaway files only under a fresh `mktemp -d /tmp/claude_<slug>.XXXXXX` of your own; never write into or overwrite files in the Scratchpad directory your environment lists — it belongs to the caller's session." verbatim;
   - invoke no flow skill: not `simo-dev:plan-execute` (the plan header's sub-skill line is satisfied by this dispatch), not `simo-dev:review-loop`, `simo-dev:commit` or `simo-dev:mr-followup`, whatever a trigger rule says about green tests; only `simo-dev:tdd`, `simo-dev:debug` and `simo-dev:verify-done`;
   - never `run_in_background` (raise the Bash `timeout`, up to 600000 ms, for a slow build or test run), so the rule holds on the default-type fallback too;
   - never commit, never push, never dispatch an `Agent`, never ask the user: a blocker, a plan gap, an unclear instruction or a verification that fails repeatedly ends the run with `BLOCKED` (`SKILL.md` "Hard stops", never guessed past), with the work done so far left in the worktree;
   - the report format below, as the final message, at most about 40 lines:
   ```
   tasks: <N ✓ | N ⚠ | N – (not started)>, one entry per task, in plan order
   files: <paths as `git status --porcelain` prints them>
   verified: <command> → rc <n>   (one line per command run, the last run of each)
   deviations: <each departure from the plan, with why — or none>
   improvements: Task <N> — <file> — <what> — <why> [<tag>]   (one line each — or none)
   improvements-dropped: <file> — <what> — <why>   (an improvement from an earlier report this run reverted, worded as that report had it — or none)
   notes: <discoveries recorded, and where — or none>
   status: DONE | BLOCKED: <what is missing, what was tried> | STOP-BASE rc=<1|4>: <CHECK BASE value, the overlap and the note:/STOP: lines printed>
   ```
2. Mark each `✓` task's todo completed. Append to the ledger exactly as `lib/agent-dispatch.md` item 3 says, reading the tags from the `improvements:` lines (`exempt` checked against the title of the `### Task N:` that line names; the `Task <N> — ` prefix is dropped from the ledger line). Each `improvements-dropped:` line that was actually ledgered → the ledger `add` block with `drop` in place of `add`.
3. On the `status:` line:
   - `DONE` → `SKILL.md` step 3.
   - `BLOCKED` → ask the user, as `SKILL.md` "Hard stops". With the answer, resume the same executor with `SendMessage` (its agent id; when not callable, `ToolSearch` `select:SendMessage` once first): it keeps what it already read. After a rebase or any other change this session made to the worktree, the resume message tells it to re-read every file before editing it. `SendMessage` returns at once with a `resumed … in the background` acknowledgement, which is not the report: wait for the task notification, and edit nothing in the worktree until it arrives. `SendMessage` unavailable → a fresh dispatch with the same prompt plus the tasks already `✓` (not to redo) and the user's answer. Then this item again on the new report.
   - `STOP-BASE` → `SKILL.md` step 2's base-freshness checkpoint handling for the reported rc, with the reported `CHECK BASE:` and `note:` / `STOP:` lines (ask, never `git stash`); once resolved, resume as for `BLOCKED`.
   - A return that is a background-launch acknowledgement, not a report → the executor is still running: wait for its task notification, and edit nothing in the worktree until it arrives.
   - A report with no `status:` line → treat as `BLOCKED: no report`, ask the user.
4. The user changes the plan → back to `SKILL.md` step 1.
