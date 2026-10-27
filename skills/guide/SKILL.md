---
name: guide
description: Routes to the simo-dev flow conventions — skill chain, publish path, gates, ticket marker, post-merge cleanup, full review-loop trigger list. On-demand reference, not always-on; the eager rules live in CLAUDE.md. Disabling the plugin switches the flow off.
---

# simo-dev flow conventions

Full details: `~/.claude/local-marketplace/simo-dev/docs/canonical-flow.md` (read on demand).

## Canonical flow

`simo-dev:task-intake` → `simo-dev:plan-execute` → `simo-dev:review-loop` → `simo-dev:commit` → `simo-dev:mr-followup`. Interview entry: `simo-dev:grill-me`.

Entry into the flow is mandatory, and the exemptions are listed with it: CLAUDE.md "Mandatory entry" (always-on, unlike this file).

## Domain glossary

At task start, read `CONTEXT.md` if present (use its terms). Update when a term crystallizes — active capture in `simo-dev:grill-me`. Glossary only, no implementation detail.

## Worktrees

- Worktree policy, path convention and the never-a-second-worktree invariant: CLAUDE.md "Worktrees" (always-on; this file is not). `<name>` = branch minus the `<type>/` prefix — `feat/proj-1234-topn` → `.worktrees/proj-1234-topn`.
- Decision table, ordered commands and the adopt/relocate procedure: `simo-dev:task-intake` step 6 (+ its `skills/task-intake/lib/worktree-carry.md`). Mechanism behind the `git -C` discipline: CLAUDE.md "Worktree cwd gotcha".

## Ticket marker

Personal repos with no linked tracker project drop a `.claude/no-tracker` marker (or `DEV_FLOW_NO_TRACKER=1`) to skip the intake step-5 ticket question. An explicit ticket key always wins.

## The publish path (one hard gate: the merge)

`simo-dev:commit` auto-cascades: knowledge harvest → review-loop pre-flight → commit → push → MR body → mermaid preview (informational, not a gate) → MR created immediately, no confirmation, URL printed — verification is delegated to `simo-dev:mr-followup`'s pipeline + review-comment loop, which also commits + pushes each round of auto-fixes unconditionally. A clean `simo-dev:review-loop` run in the ship flow cascades straight into `simo-dev:commit` (standalone review requests and headless runs stop at the report — see its step 8). A pre-push MR-state guard stops any follow-up push whose MR was merged/closed mid-session. WIP opt-out: `--no-mr` / `DEV_COMMIT_NO_MR=1`.

`commit` step 10 always auto-invokes `mr-followup` (the auto-publish arm/check mechanism is retired — publish is unconditional; the WIP opt-out stays `--no-mr`).

## Reviewing others' MRs

`simo-dev:mr-review <URL-or-IID>` (or no arg on a non-default branch). Strictly read-only — output is a local markdown file (`~/Documents/cr/`, override `DEV_REVIEW_MR_OUT_DIR`). Never posts back.

## Gates that always require explicit permission

- **Merge** (the publication gate) and **force-push** — CLAUDE.md owns both; they hold on any repo, in the flow or out of it.
- **Commit+push on a protected branch** (`master`/`main`/`develop` — out-of-flow fallback, the canonical flow always works in a worktree) — the single gate of `commit`'s Protected-Branch Path; nothing skips it.

## Post-merge cleanup

Worktree cleanup after merge is handled by `simo-dev:mr-followup` step 8, automatically — the one pre-approved exception to "destructive ops need approval" (runs ONLY after a user-approved merge). Never switch/checkout the main checkout to do it — removal works from any branch, and another session may be working there.

Once cleanup, the journal and step 8.6 are done, step 8.7 invokes the repo's configured `release_skill` with args `post-merge`; an unconfigured repo is a silent no-op. Configure it in `~/.claude/simo-dev/repo.json` via `lib/repo_config.py`, keyed by repo-id — `docs/canonical-flow.md` "Configuring per-repo settings".

## MR assignee

Always assign GitLab MRs to the current user: `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; glab api user | python3 "$R/lib/json_field.py" - username`, pass `--assignee <username>`. Never hardcode.

## Proactive review-loop auto-run (eager copy: CLAUDE.md — this file is not loaded by itself)

When a trigger fires and review-loop hasn't run since the last batch of edits, **auto-invoke `simo-dev:review-loop` and proceed** — never ask first. Triggers:
- **Linguistic** — "done" / "ok" / "proceed" / "merge" / "push" / "ship it" / "let's commit".
- **Implementation** — tests passing, feature complete, batch of edits done.
- **Structural** — BEFORE `git commit`, `git push`, `git merge`, `glab mr merge`, `gh pr merge`, or `simo-dev:commit`. A PreToolUse hook on `Bash` self-gates to `git commit` (any form, including `git -C <worktree> commit` and chained `add && commit`) and injects a reminder pointing here.

Inside a `simo-dev:mr-followup` run nothing the run does is a trigger: its edits, test runs, commits, pushes, and the user's answers to its own questions. Any other review-loop waits as canonical-flow "In-scope improvements" (Transparency) and `simo-dev:mr-followup` "No Also-fixed ledger work inside a run" describe.

Skip only when clearly useless: trivial doc/typo edit, empty diff, or a review already covers the pending files (`simo-dev:commit` step 1 checks this deterministically — no TTL). A clean run cascades into `simo-dev:commit` (review-loop step 8 exceptions apply); unresolved findings still gate. A clean run lets the next `simo-dev:commit` skip its review pre-flight, until a file changes again.
