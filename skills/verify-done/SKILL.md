---
name: verify-done
description: Use before claiming any work complete, fixed, or passing — before commits, MRs, task completion, or expressing satisfaction. Requires running the verification command fresh and reading its output before the claim.
---

# Verify Before Claiming Done

**The rule:** no completion claim without fresh verification evidence produced in the current message. Applies to exact phrases, paraphrases, and any wording that implies success ("Great!", "that fixes it", moving on to the next task). Confidence is not evidence; a previous run is not evidence.

## The gate

Before any status claim or expression of satisfaction:

1. **Identify** the command that proves the claim.
2. **Run** it — fresh and complete, not a partial check. Never leave the Bash cwd inside a repo subdirectory. For a project in a subdirectory, use the tool's own directory flag (`npm --prefix <dir>`, `pnpm -C <dir>`, `yarn --cwd <dir>` on v1, `go -C <dir>`, `uv --directory <dir>`, `cargo test --manifest-path <dir>/Cargo.toml`, `dotnet test <dir>`). Without one, run it in a subshell: `( cd "<worktree-abs>/<dir>" && <command> )`. Never a bare `cd`: the Bash cwd persists into later calls. Once it sat inside a subdirectory, the next skill load attached that subdirectory's `CLAUDE.md` and path-scoped rules to the main session for the rest of that session (observed once).
3. **Read** the full output: exit code, failure count.
4. **Confirm** it supports the claim. If it doesn't, state the actual status with the evidence.
5. Only then make the claim, citing the evidence.

## Claim → required evidence

| Claim | Requires |
|---|---|
| Tests pass | Test command output: 0 failures (not "should pass") |
| Linter clean | Linter output: 0 errors (linter ≠ compiler) |
| Build succeeds | Build command: exit 0 |
| Bug fixed | Original symptom re-tested: passes |
| Regression test works | Red-green verified: fails without the fix, passes with it |
| Agent/subagent completed | VCS diff inspected, not the agent's "success" report — except `simo-dev:plan-execute`'s `subagent` executor and `simo-dev:review-loop`'s fixer, whose evidence is review-loop step 0's fresh verification plus the reviewer reading the diff: the orchestrator does not re-read it |
| Requirements met | Line-by-line checklist against the plan/spec, not just green tests |

## When it applies

Before: any success claim, committing, MR/PR creation, marking a task complete, moving to the next task, delegating to an agent.

**Done when:** the verification command ran in this message and its output confirms the claim.
