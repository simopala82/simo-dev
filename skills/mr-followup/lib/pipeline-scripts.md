# Pipeline poll — launch commands

Loaded on demand from `mr-followup` step 2.

Both forges are polled by a static Python script, launched directly in background, never piped (piping would swallow the exit-code verdict — the poll reports rather than judges, and the rc is the only thing that says whether it concluded).

## Env vars (shared, identical names and defaults on both forges)

- `POLL_INTERVAL` (default 30s) — gap between polls.
- `POLL_NOPIPE_MAX_WAIT` (default 300s) — ceiling while nothing has been seen yet (no pipeline matching the branch on GitLab, no checks reported on GitHub).
- `POLL_MAX_WAIT` (default 1800s) — ceiling once something has been seen, but it has not concluded.

Both ceilings and the interval are wall clock, not a count of sleeps, so per-poll latency on the forge call counts against them. `POLL_NOPIPE_MAX_WAIT` is clamped down to `POLL_MAX_WAIT` if the caller sets it higher, so a lowered `POLL_MAX_WAIT` cannot make the "nothing seen" ceiling outrun the "seen but not concluded" one.

## Exit codes (the verdict, identical on both forges)

- `0` — a terminal status was read. A `failed`/red verdict is still `0`: the poll reports, it does not judge.
- `1` — refused to start (GitLab: empty `<branch>` literal, or `<worktree-abs>` does not resolve to a real directory; GitHub: missing `<pr-num>` argument).
- `3` — nothing to poll (no pipeline for the branch / the PR has no checks), and a reachable forge said so.
- `4` — a ceiling came due with no conclusion — still transitional, something that vanished from the list, or a line that matched with an unparseable status.
- `5` — the read that hit the ceiling could not be verified (the forge did not answer), so the state is UNVERIFIED — never treated as green.

Both ceilings are gated on the CURRENT read's success, never on "the forge answered at some point": a forge that dies mid-poll always returns `5`, never a stale `3` or `4` off an earlier successful read.

## Launch

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/skills/mr-followup/lib/pipeline_poll_gitlab.py" "<worktree-abs>" "<branch>"
```

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/skills/mr-followup/lib/pipeline_poll_github.py" "<pr-num>"
```

`pipeline_poll_gitlab.py` scopes its own `glab pipeline list` call to `<worktree-abs>` internally (passed as the subprocess `cwd`) — `glab` resolves its project from the process cwd, so this replaces the old external `cd`. `pipeline_poll_github.py` is not cwd-scoped at all: `gh` resolves the PR by number, no repo context needed. This asymmetry is why GitHub's poll needs the extra liveness probe (`gh pr view --json number`) to split `3` from `5` — `gh pr checks`'s own exit code is unusable for that (non-zero for failing/pending/absent checks alike) — while GitLab's does not, since `glab pipeline list`'s exit code IS usable directly.

Caller-side handling of the exit code: `mr-followup` step 2.
