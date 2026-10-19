# Repo pre-flight — cache-copy detection and recovery

Loaded on demand from `task-intake` step 3 when a pre-flight check fires.

The cache-copy section below covers step 3's two **wrong-repo** verdicts. It does **not** cover a linked worktree of a legitimate repo that merely sits under a `plugins/` path — its remote is the owning repo's, so resolving a marketplace source is the wrong move; step 3 forwards that one to step 6, which adopts it in place, moves it and adopts it, leaves it alone, or stops and asks. The other two sections are unrelated to either verdict.

## Cache/marketplace copy detected

Either of step 3's first two verdicts. Both mean the tree is a materialized marketplace cache/install copy, not the source:

- `<REAL>` (`dirname` of the absolute `--git-common-dir`) contains `.claude/plugins/cache/` or `.claude/plugins/marketplaces/` — the copy has its own `.git`, so it is its own main checkout.
- the **prefix** is under one of those paths — the copy has no `.git`, so `rev-parse` walked up and the toplevel points at an unrelated outer repo. Example: task-intake started from a marketplace cache path like `.claude/plugins/cache/<marketplace>/<plugin>/<version>`, a gitignored copy, and created a worktree missing one of the plugin's `skills/<name>/` subdirectories entirely — discovered mid-TDD via a wrong error class. A toplevel-only test cannot see this one.

Find the real source: look up the marketplace in `~/.claude/plugins/known_marketplaces.json` (schema: `<marketplace-name> → {source: {source: "git"|"github"|"directory", repo|url|path}, installLocation}`).

- `source.source` = `git`/`github` → search for a clean local clone with a matching origin remote:
  ```bash
  find ~/projects -maxdepth 2 -iname "<plugin-name>*"
  git -C <candidate> remote get-url origin   # compare against known_marketplaces.json repo/url
  ```
  Propose that clone to the user instead of the cache path; never continue the analysis (or `git worktree add`) in the cache copy.
- `source.source` = `directory` → the marketplace's own `source.path` IS the source — propose it directly.
- No matching entry / no local clone found → stop and ask the user where the real source repo lives; do not guess.

If this switches to a different repo/clone, restart the analysis there — nothing has been written yet, nothing to carry over.

## Untracked task-relevant path

If the task involves an **existing** path expected to already be in the repo (not a brand-new file the task will create):

```bash
git ls-files <task-relevant-path>   # empty output → path is NOT tracked here
```

Empty for a should-exist path → warn the user and wait for confirmation instead of silently continuing.

## Residual case — artifacts already written in the wrong repo

(Pre-flight bypassed, or a session mid-flight from before this check existed.) Do not `git stash` across repos — stashes live in one `.git` and don't cross clones. `cp` the new/modified files (CONTEXT.md, docs/adr/*, the plan file) to the same relative path in the correct repo, then continue there. If the wrong repo was a plugin cache/marketplace copy, no cleanup needed — it's disposable and gets overwritten by the next release/resync ritual (`docs/plugin-release.md`).
