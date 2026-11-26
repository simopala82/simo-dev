# Commit failure paths

Loaded on demand from `commit` `SKILL.md` when one of its guarded blocks stops or fails: step 0.5's base-freshness rc `1`, step 5's push block, step 7's `lib/mr_create.py prepare` stops (local-base-ahead guard included), step 9's `lib/mr_create.py create`. Step numbers refer to `SKILL.md`; `W`, `R` and `G` are bound there.

## Step 0.5 rc 1: park, rebase, restore

**rc `1` recovery.** Leaving the work committed is not an option here — steps 0.7, 1, 3 and 4 all read the working diff — so park it in a throwaway commit, rebase, put it back. Announce the park; never park silently.

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
git -C "$W" fetch origin --prune --quiet
TARGET_ARG="<the --target skill arg, or empty>"
BASE=$(python3 "$R/lib/build_mr_ctx.py" --repo "$W" --target "$TARGET_ARG" --print-target) || { echo "STOP: could not resolve the base branch (stderr above)." >&2; exit 5; }
python3 "$R/lib/park_and_rebase.py" park --repo "$W" --base "$BASE"
# rc 3 → resolve, then continue WITH the hook suppression (a bare `--continue` is a fresh
#   git process, re-runs prepare-commit-msg and rewrites the park subject), NEVER `--skip`:
#   git -C "$W" -c core.hooksPath=/dev/null rebase --continue
# then, re-binding both — the `--continue` above is a separate Bash call, so W and R are gone by
# here, and an unbound R makes this exit rc 2, which is indistinguishable from the "refused, tree
# untouched" verdict below while the park commit is in fact still at HEAD:
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/park_and_rebase.py" restore --repo "$W"
```

`park`: rc `0` → rebased, run `restore`. rc `3` → parked, rebase conflicted; resolve, `--continue`, then `restore`. rc `4` → nothing left to restore. rc `5` → **the repo changed and needs attention** (an unfinished rebase, a park commit whose record could not be written or removed, or a park still at `HEAD` under a rewritten subject) — read the message, it carries the recovery command. rc `2` → refused, repo unchanged; reason printed, from several sources. `restore`: rc `0` restored — **read its stderr even so**, since nothing may fail once the reset has landed, so a path it could not re-stage is reported there and not by the code; rc `2` refused with the tree untouched — it resets only when `read_record` finds the record usable, `HEAD` is still the park commit, and the branch still carries the commits the rebase was expected to keep, so a `git rebase --skip` (which git itself suggests at a conflict) is caught instead of leaving a clean-looking tree; that count under-predicts the drops a rebase makes legitimately, so it errs toward stopping and `--allow-dropped` is the way through once the reflog says which happened. `restore` also hands back the pre-park modification time for every parked path whose content is byte-identical, and reports on stderr how many it restored, how many the rebase changed and how many it could not read — so the rebase alone no longer invalidates the step-1 pre-flight, and a path it reports as changed really did change. Then re-run the guarded block at the top of `SKILL.md` step 0.5 — not a bare `python3 …/check_base_freshness.py`, which drops the `[ -f ]` test in the window a release is most likely to land — and continue into step 0.7, then step 1, which re-checks the review marker against the restored files. The park does **not** preserve the index/worktree split — staged paths are re-staged, content merged, and any it could not re-stage are named — so work that must survive byte-exact on both sides does not belong in a park.

Rejected alternatives, both measured: `git stash` (logged in the author's own notes, §17 — worth keeping a similar log) and hand-clearing with `git checkout -- <file>`, which restores nothing at all from a mixed pathspec list and, run in the **main checkout** on a **tracked** path under `~/.claude`, writes through to another session's uncommitted edit (CLAUDE.md, `~/.claude` symlink rule).

## Step 5: push block codes and recovery

What the codes mean — the push block (`SKILL.md` step 5) already encodes the decision, so never re-issue the push from a separate call. **Two tests run before any rc is read, and both refuse the push with `2`**: the guard file is not there (`[ -f "$G" ]`), and the guard printed nothing (`[ -z "$MR_STATE" ]`). An rc can say why a helper failed, never that it answered, and an unanswered guard would push blind on either push branch of that block — rc `0` included, since a script truncated to valid syntax exits `0` with empty stdout (both worth logging in a personal notes file if you keep one — section "A custom exit code that the interpreter also produces, with the same empty stdout"; how a live session's script path vanishes under it is the section "Releasing a plugin kills the hooks of every already-open session", both in the author's own). Past those two tests: `0` = MR open. `3` = "no MR found", the forge positively confirmed there is none: normal before MR creation, hence the logged line. Those two are the only push branches. `1` = not open, likely merged/closed mid-session; recovery is a cherry-pick onto a fresh branch and a new MR. Everything else lands on the `else`, `2` included (`glab`/`gh` missing, bad token, network down, a bad `--cwd`) — fail closed, since pushing blind is exactly how a merged branch gets recreated as an orphan. `2` is also what a transient forge error produces (measured 2026-08-19: 1 of 3 `glab mr view` calls returned `net/http: TLS handshake timeout`), so re-running the block is the recovery, not a bypass. `simo-dev:mr-followup` step 7 item 6 (`lib/mr_followup.py push`) runs the same guard with the same classifier keys (`could not verify`, `the guard is not at`, `printed nothing`, `(not open)`), but exits `3`, not `2`, when the guard answered `unverifiable`, and an unbound `R` shows there as python's `can't open file '/lib/mr_followup.py'` (rc `2`), not as a `/lib/` guard path.

**A non-zero exit here ends the cascade** — steps 6-10 must not run: the branch was deliberately not pushed, so whatever step 6 finds — the merged MR itself, or nothing conclusive — it must not act on, and step 10 must not hand that state to `simo-dev:mr-followup`. This and step 9's create block are the two places the "auto-cascades, unconditional (no gate)" contract at the top of this skill does not apply — including the step-10 hand-off that `simo-dev:guide` and `simo-dev:afk` both describe as unconditional. An unattended run must not apply `simo-dev:afk`'s "a command failed → retry, don't stop" wholesale, and cannot classify by exit status — `git push` is the last command of the push branch, so a rejected push exits `1` too. Classify on the message, and match the two never-answered arms **first** — they share the `could not verify` prefix on purpose, because that prefix is also what a human reader scans for. No `STOP:` line → git failed, so fetch, rebase, retry. A `STOP: could not verify …` naming `the guard is not at` or `printed nothing` → **park**, not retry: those two say the guard never ran or never answered, so a byte-identical retry spins and `simo-dev:afk`'s retry rule carries no attempt cap. One escape hatch, and **each arm has its own evidence** — the generic cause list printed in every message is not evidence. On `the guard is not at`, read the path the message echoes: a `/lib/…` root means `R` was never bound. On `printed nothing`, read the guard's own **stderr** line above it: an argparse usage error names the offending argument (`--cwd must be an absolute path`), while a truncation leaves stderr empty and stays a park. Both of those are the caller's substitution, not a broken plugin — the forgotten-binding hazard the preamble at the top of this skill names — so re-issue the whole block with the literals in place, run it **once** more, then park. Any **other** `STOP: could not verify …` → retry: that one is the guard answering `unverifiable`, i.e. a bad token, a dead network, a transient forge error. `STOP: MR … is now '<state>' (not open)` → park. The split is answered-but-unverifiable versus never-answered, not one code versus another.

Why the block pushes with `-u`, first push or not: a bare `git push` inherits an upstream from the worktree start-point and targets the *base* branch under `push.default=upstream`, and only the default `push.default=simple` happens to refuse it. Still require explicit confirmation: `--force`/`--force-with-lease` (any branch); protected-branch push (wrong path — see Protected-Branch Path).

## Step 7: local base ahead of origin

Mirror-image of step 0.5's guard: that one stops when `origin/<base>` has advanced *beyond* the branch's fork point; this one stops when the *local* `<base>` is ahead of `origin/<base>` and those extra commits are already part of this branch's ancestry (e.g. the branch was created from, or later rebased/merged onto, a local base that was never pushed). `merge-base --is-ancestor` guards against false positives from unrelated local-base work this branch never incorporated. There is no bypass: ask the user for explicit confirmation before pushing the local `<base>` (a protected-branch push when `<base>` is a protected branch — the default branch, or a stacked integration branch — a feature-branch push otherwise; asked either way), never push it unasked; once pushed, re-run step 7's prepare block — it already re-fetches `origin` first, so the re-run passes cleanly with no further action. If the user declines to push, MR generation cannot proceed until they do (or the branch is rebased onto a base that no longer carries the unpushed commits). `prepare` stops before writing `mr_diff.txt` or `mr_draft`, same as step 5's own guard ending the rest of the commit cascade on a non-zero exit — every later step here depends on `mr_diff.txt`/`mr_ctx.json` existing.

## Step 7: other prepare stops

Recovery per `lib/mr_create.py prepare` STOP (`SKILL.md` step 7 routes here):

- `--repo must be the absolute path…` / `detached HEAD` → re-issue the block with the literal worktree path (afk: once, then park).
- `could not resolve the MR target branch` (no `--target`, no stacked parent, no `origin/HEAD`) → ask the user for the base and re-run with `TARGET_ARG` set (afk: park).
- `build_mr_ctx.py failed` / `git diff … failed` / `local base check failed` → report the stderr above and stop (afk: park).
- `a plan's MR draft field …` (rc 3, uncommitted, gitignored or non-bare value; the WARNING names the plan) → fix the value to a bare yes/no, or re-run `simo-dev:commit` from the top staging the plan the WARNING names at step 4 (a gitignored plan cannot be staged: use `--draft`), or `--no-draft` when the plan is not this task's (afk: park).
- `check_mr_draft.py failed` → the detector failed, stderr says why (afk: park).

## Step 9: create block failed

**A non-zero exit from `lib/mr_create.py create` ends the cascade**, same contract as step 5's "A non-zero exit here ends the cascade": the rest of step 9 (the verification-notes comment) and step 10 must not run until it exits 0, except as the rc 4 bullet below routes them; the verification-notes post has no guard of its own. Recover on evidence, never on the cause list a message prints:

- rc 2 → the block ran with `R` unbound or a placeholder left (python could not open `/lib/mr_create.py`, or a bad `--forge`): re-issue it with the literals (afk: once, then park). The same route holds for the transition fence below.
- rc 1, title/target STOP → step 7 did not complete: redo step 7 (prepare, dispatch, body), then create.
- rc 1, `could not read the MR body` → run step 7's body block, then create.
- rc 1, username STOP → transient forge error: re-run create.
- rc 1, forge STOP → `SKILL.md` step 9.
- rc 1, `--repo` / `detached HEAD` → re-issue with the literal.
- rc 4, the create call failed → an MR may exist anyway ("already exists" after a transient step-6 miss, or a create whose response was lost): never retry the create, re-run step 6 once. `EXISTING_MR=yes` → run the CodeReview transition alone with the fence below, then step 10's existing-MR flow. `EXISTING_MR=no` or empty → the create failed for a real reason (a 422 from the wrong cwd, a bad token, a rejected field): stop and report the create's stderr, never back to step 7 — except `gh pr create --draft` failing where the repo's plan has no draft PRs: tell the user, then re-run `simo-dev:commit --no-draft` from the top (prepare rewrites `mr_draft`).
- rc 5, source-branch STOP → an MR exists but not on this branch/project: report it, do not hand its URL on.
- rc 5, Draft STOP → the script's one mark-Draft attempt already failed: tell the user (toggle on the forge, or accept a non-Draft MR), never re-run `create` (rc 4). Once they answer, run the CodeReview transition fence below, then the existing-MR path in `lib/alt-paths.md` (ledger clear, verification notes, step 10). The `mr_created` event is not recorded on this path.

Unattended (`simo-dev:afk`, whose retry rule carries no attempt cap): username STOP → re-run **once**, then park; rc 4 → the step-6 re-run, park on anything but `EXISTING_MR=yes`; every other rc 1 and rc 5 → park.

The CodeReview transition alone:

```bash
W="<absolute path of the task worktree>"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/mr_create.py" transition --repo "$W"
```
