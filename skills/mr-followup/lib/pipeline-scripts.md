# Pipeline poll — launch commands

Loaded on demand from `mr-followup` step 2.

Both forges are polled by a static Python script, launched directly in background, never piped (piping would swallow the exit-code verdict — the poll reports rather than judges, and the rc is the only thing that says whether it concluded).

## Env vars (shared, identical names and defaults on both forges)

- `POLL_INTERVAL` (default 30s) — gap between polls.
- `POLL_NOPIPE_MAX_WAIT` (default 300s) — ceiling while nothing has been seen yet (no pipeline matching the branch on GitLab, no checks reported on GitHub). On GitLab it only bounds the ambiguous case: when the no-CI probe (below) finds no CI config for the branch, the poll ends at the first read instead.
- `POLL_MAX_WAIT` (default 1800s) — ceiling once something has been seen, but it has not concluded.

Both ceilings and the interval are wall clock, not a count of sleeps, so per-poll latency on the forge call counts against them. `POLL_NOPIPE_MAX_WAIT` is clamped down to `POLL_MAX_WAIT` if the caller sets it higher, so a lowered `POLL_MAX_WAIT` cannot make the "nothing seen" ceiling outrun the "seen but not concluded" one.

## Exit codes (the verdict, identical on both forges)

- `0` — a terminal status was read. A `failed`/red verdict is still `0`: the poll reports, it does not judge.
- `1` — refused to start (GitLab: empty `<branch>` literal, or `<worktree-abs>` does not resolve to a real directory; GitHub: missing `<pr-num>` argument).
- `3` — nothing to poll (no pipeline for the branch / the PR has no checks), and a reachable forge said so. GitLab has two causes, told apart by the last line: `NO PIPELINE` once `POLL_NOPIPE_MAX_WAIT` came due, or `NO CI CONFIG` at the first read, when the no-CI probe found no CI config for the branch. Neither is green.
- `4` — a ceiling came due with no conclusion — still transitional, something that vanished from the list, or a line that matched with an unparseable status.
- `5` — the read that hit the ceiling could not be verified (the forge did not answer), so the state is UNVERIFIED — never treated as green.

Both ceilings are gated on the CURRENT read's success, never on "the forge answered at some point": a forge that dies mid-poll always returns `5`, never a stale `3` or `4` off an earlier successful read.

## GitLab no-CI probe

Runs once, lazily: only on a `glab pipeline list` read with rc 0, no line for the branch, and nothing seen yet — so a visible pipeline always wins, and an unreachable `glab` never reaches it (`5` stays intact). It asks `glab api projects/:id` for Auto DevOps (must be off) and `ci_config_path` (must be empty or local — a `…@project` or URL path is ambiguous), then the files API for that path on the branch. Only a `404 File Not Found` body counts as "no CI config"; any other answer (api error, missing key, `404 Commit Not Found`) keeps the ceiling.

Known limits (an observed list, not a closed one), each giving an early rc `3`, never green: pipelines that need no config file on the branch — pipeline and scan execution policies (GitLab Ultimate), and `external` pipelines, which a CI outside GitLab creates by posting a commit status — and merged-results pipelines (GitLab Premium), which run on the merge ref and can pick up a config the target branch gained after this branch was cut.

GitHub has no equivalent: checks can come from apps (external CI, security scanners, deploy previews) with no file under `.github/workflows`, so a missing workflow file proves nothing there.

## Launch

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/skills/mr-followup/lib/pipeline_poll_gitlab.py" "<worktree-abs>" "<branch>"
```

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/skills/mr-followup/lib/pipeline_poll_github.py" "<pr-num>"
```

`pipeline_poll_gitlab.py` scopes its own `glab pipeline list` call to `<worktree-abs>` internally (passed as the subprocess `cwd`) — `glab` resolves its project from the process cwd, so this replaces the old external `cd`. `pipeline_poll_github.py` is not cwd-scoped at all: `gh` resolves the PR by number, no repo context needed. This asymmetry is why GitHub's poll needs the extra liveness probe (`gh pr view --json number`) to split `3` from `5` — `gh pr checks`'s own exit code is unusable for that (non-zero for failing/pending/absent checks alike) — while GitLab's does not, since `glab pipeline list`'s exit code IS usable directly. GitLab's no-CI probe answers a different question (can a pipeline exist at all), never `3` vs `5`.

Launched at `mr-followup` step 2 (background, non-blocking); caller-side handling of the exit code happens at step 7, the flow's sync point for it.
