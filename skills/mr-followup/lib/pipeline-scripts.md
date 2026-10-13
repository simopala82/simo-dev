# Pipeline poll — launch commands

Loaded on demand from `mr-followup` step 2.

Both forges are polled by a static Python script, launched directly in background, never piped (piping would swallow the exit-code verdict — the poll reports rather than judges, and the rc is the only thing that says whether it concluded).

## Env vars (shared, identical names and defaults on both forges)

- `POLL_INTERVAL` (default 30s) — gap between polls.
- `POLL_NOPIPE_MAX_WAIT` (default 300s) — ceiling while nothing has been seen yet for HEAD (no pipeline for the HEAD commit matching the branch on GitLab, no checks for it on GitHub). On GitLab it only bounds the ambiguous case: when the no-CI probe (below) finds no CI config for the branch, the poll ends at the first read instead.
- `POLL_MAX_WAIT` (default 1800s) — ceiling once something has been seen, but it has not concluded.

Both ceilings and the interval are wall clock, not a count of sleeps, so per-poll latency on the forge call counts against them. `POLL_NOPIPE_MAX_WAIT` is clamped down to `POLL_MAX_WAIT` if the caller sets it higher, so a lowered `POLL_MAX_WAIT` cannot make the "nothing seen" ceiling outrun the "seen but not concluded" one.

## Pinned to HEAD

Both scripts resolve the SHA of `<worktree-abs>`'s HEAD once at startup and report only on that commit's own pipeline/checks, never the newest run on the branch: right after a push the newest run is still the previous commit's, often already concluded. GitLab filters `glab pipeline list --sha <HEAD>` server-side and keeps the branch match, because a tag pipeline can share the SHA. GitHub reads the PR head (`gh pr view --json headRefOid`) every poll and reads the checks only once it equals HEAD. Until then nothing counts as seen, so `POLL_NOPIPE_MAX_WAIT` bounds the wait.

Limits (observed, not a closed set): when the remote branch is ahead of the local HEAD (another session pushed), GitLab reports the local HEAD's own, possibly older, pipeline, and GitHub ends `3` with a `NO CHECKS for <sha>` head-mismatch line; on GitHub a PR head that moves off HEAD after checks were seen ends the poll at once with `4` (`HEAD MOVED`). A `glab` without `--sha` fails every read, ending `5`.

## Exit codes (the verdict, identical on both forges)

- `0` — a terminal status was read. A `failed`/red verdict is still `0`: the poll reports, it does not judge.
- `1` — refused to start (GitLab: empty `<branch>` literal, `<worktree-abs>` does not resolve to a real directory, or `glab` cannot run in `<worktree-abs>` — worktree gone mid-poll, or `glab` missing; GitHub: missing `<pr-num>` or `<worktree-abs>` argument, or `gh` cannot run in `<worktree-abs>` — worktree gone mid-poll, or `gh` missing; both: empty `<worktree-abs>` literal, or HEAD does not resolve in `<worktree-abs>`).
- `3` — nothing to poll for the HEAD commit (no pipeline for the branch at HEAD / no checks for HEAD on the PR, including a PR head that never became HEAD), and a reachable forge said so. GitLab has two causes, told apart by the last line: `NO PIPELINE` once `POLL_NOPIPE_MAX_WAIT` came due, or `NO CI CONFIG` at the first read, when the no-CI probe found no CI config for the branch. Neither is green.
- `4` — a ceiling came due with no conclusion — still transitional, something that vanished from the list, or a line that matched with an unparseable status. On GitHub also at once when the PR head moves off HEAD after its checks were seen (`HEAD MOVED`).
- `5` — the read that hit the ceiling could not be verified (the forge did not answer; on GitHub this includes a PR head that could not be read), so the state is UNVERIFIED — never treated as green.

Both ceilings are gated on the CURRENT read's success, never on "the forge answered at some point": a forge that dies mid-poll always returns `5`, never a stale `3` or `4` off an earlier successful read.

## GitLab no-CI probe

Runs once, lazily: only on a `glab pipeline list` read with rc 0, no line for the branch, and nothing seen yet — so a visible pipeline always wins, and an unreachable `glab` never reaches it (`5` stays intact). With the `--sha <HEAD>` filter the first read right after a push is usually empty, so the probe runs on most polls (still once per poll). A repo whose pipelines come only from the blind spots below therefore gets an early `NO CI CONFIG` on every push, not just a branch's first. It asks `glab api projects/:id` for Auto DevOps (must be off) and `ci_config_path` (must be empty or local — a `…@project` or URL path is ambiguous), then the files API for that path on the branch. Only a `404 File Not Found` body counts as "no CI config"; any other answer (api error, missing key, `404 Commit Not Found`) keeps the ceiling.

Known limits (an observed list, not a closed one), each giving an early rc `3`, never green: pipelines that need no config file on the branch — pipeline and scan execution policies (GitLab Ultimate), and `external` pipelines, which a CI outside GitLab creates by posting a commit status — and merged-results pipelines (GitLab Premium), which run on the merge ref and can pick up a config the target branch gained after this branch was cut.

GitHub has no equivalent: checks can come from apps (external CI, security scanners, deploy previews) with no file under `.github/workflows`, so a missing workflow file proves nothing there.

## Launch

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/skills/mr-followup/lib/pipeline_poll_gitlab.py" "<worktree-abs>" "<branch>"
```

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/skills/mr-followup/lib/pipeline_poll_github.py" "<pr-num>" "<worktree-abs>"
```

`pipeline_poll_gitlab.py` scopes its own `glab pipeline list` call to `<worktree-abs>` internally (passed as the subprocess `cwd`) — `glab` resolves its project from the process cwd, so this replaces the old external `cd`. `pipeline_poll_github.py` does the same with every `gh` call (`cwd=<worktree-abs>`): `gh` resolves the repo, and so which PR the number names, from the cwd's git remotes. Both scripts resolve the HEAD SHA once at startup ("Pinned to HEAD"). GitHub's poll still needs the extra liveness probe (`gh pr view --json number`) to split `3` from `5` — `gh pr checks`'s own exit code is unusable for that (non-zero for failing/pending/absent checks alike) — while GitLab's does not, since `glab pipeline list`'s exit code IS usable directly. GitLab's no-CI probe answers a different question (can a pipeline exist at all), never `3` vs `5`.

Launched at `mr-followup` step 2 (background, non-blocking); caller-side handling of the exit code happens at step 7, the flow's sync point for it.
