# Step replay

`lib/step_replay.py` measures what a change to the flow does to one step, before you adopt it. It runs the same step on the same frozen input several times with plugin variant A and several times with variant B, each run in a fresh, isolated session, and compares cost and outcome against the noise between runs of the same variant.

## Why one step, on a frozen input

Comparing two real tasks mixes the effect of the flow change with how hard each task was. Re-running whole tasks costs too much. So the harness replays a single step (`plan-write`: task-intake steps 7 and 7.5, the plan writing with its `plan-reviewer` critique and gate, `plan-execute`, `review-loop`, `mr-followup`) on a frozen input: same repo state, same brief, a cold session each time. Model output is not deterministic, so one run per variant proves nothing: run at least 2 per variant and read the spread between runs of the same variant as the noise.

## Fixtures

A fixture is a directory:

- `fixture.json` — `schema`, `name`, `step` (a key of `STEPS`), `base` (the commit the step started from), `source_session`, and for plan-write an optional `reference_plan` (`commit`, `path`, `lines`, `tasks` of the plan the real task produced, for comparison).
- `brief.md` — the exact input the step received, with every path of the source repo replaced by `{{REPO}}` (filled with the snapshot path at run time).

Build one from a finished task's session:

```bash
python3 lib/step_replay.py fixture --session <session-id> --base <commit> --out <dir> \
  [--reference <commit>:<plan path>] [--source-root <repo path> ...] [--repo <repo>]
```

It takes the arguments of the first `simo-dev:plan-write` invocation in that session's transcript. `--source-root` (repeatable, replaced longest first) defaults to the session's working directory; pass the repo root when the session ran from a subdirectory, and check the brief holds no absolute path afterwards. Real fixtures carry real task text: keep them under `private/`, which is not exported.

The `plan-write` replay drives task-intake's steps 7 and 7.5 with the brief as their gathered context, so the same fixture runs on a variant where the main session writes the plan (the flow before the plan-writer subagent) and on one where the `plan-writer` subagent does. Known limit: since the plan-writer, `simo-dev:plan-write` loads inside that subagent with no arguments, and the brief is a file under `$TMPDIR/claude_plan-brief.*`, so `fixture` cannot extract a brief from a session that ran it; build new fixtures from earlier sessions, or write `brief.md` by hand from the brief file while it still exists.

## Variants

`--variant LABEL=SPEC`, repeatable. `SPEC` is one of:

- `ref:<git-ref>` — the plugin directory at that commit of the repo;
- `dir:<path>` — a plugin directory on disk (caches skipped);

optionally followed by `,override:<plugin-relative-path>=<file>` parts that replace single files on top.

When the fixture's own task changed the plugin, a variant newer than the fixture's base already contains the answer, readable through `--plugin-dir`. For such fixtures build every variant from the fixture's base (`ref:<base>`) and express the A/B difference only as overrides. `run` refuses any variant that is not the fixture's base or one of its ancestors (a `dir:` variant cannot be checked and counts as newer): the source task's commits are usually rebased or squashed before merge, so "does it contain the reference commit" would never fire. `--allow-newer-plugin` lifts the refusal for a fixture whose task did not touch the plugin; those manifests carry `may_contain_answer: true`. Overrides are not checked: take them only from files the fixture's task did not change, or they carry the answer just the same.

Each manifest records what was pinned: main model and effort (`--model`, default `opus`; `--effort`, default `medium`), the critique model (`--critique-model`, written to a run-local model-strategy file read through `SIMO_DEV_MODEL_STRATEGY_CONFIG`), the plugin version, a hash of the materialized plugin tree, the plugin commit, and the CLI version.

## Isolation

A run must not find the real answer (the plan, the merge request, later commits). Each run:

- builds a snapshot: `git archive` of the base into a new repo with one commit, with a local bare `origin` made from the same snapshot (the step can fetch, and there is no future history to find); the repo's `.claude/settings*.json` are removed, since project settings can enable installed plugins and extra plugin dirs;
- runs `claude -p` in the snapshot with `--setting-sources project` (no user settings, user hooks or installed plugins; in a probe on CLI 2.1.322 the user's memory file was not loaded either — an observation, not a documented guarantee), `--strict-mcp-config` (no MCP servers), `--plugin-dir <materialized variant>`, `--session-id`, `--max-budget-usd`, and the prompt on stdin;
- drops from the child's environment the variables a parent Claude Code session exports (`CLAUDECODE`, `CLAUDE_CODE_*`, `CLAUDE_AGENT_SDK_*`, `CLAUDE_EFFORT`, `CLAUDE_PID`; kept: `CLAUDE_CODE_OAUTH_TOKEN` and the provider switches `CLAUDE_CODE_USE_BEDROCK`/`_VERTEX`/`_FOUNDRY`, `CLAUDE_CODE_API_KEY_HELPER_TTL_MS`), so a harness started from inside a session does not load that session's plugin dirs or mods or reuse its identity; the manifest lists them in `env_dropped`. Other inherited variables that can change behavior or cost (`ANTHROPIC_*`, `DISABLE_*`, `ENABLE_*`, `MCP_*`, `MAX_*`, `BASH_*`, and the kept `CLAUDE*` ones) stay, so a batch started from a desktop session and one from a terminal may differ: the manifest names them in `env_inherited` and hashes their values in `env_sig` (names ending in `_TOKEN`, `_KEY`, `_SECRET` or `_PASSWORD` are left out of the hash; `MAX_THINKING_TOKENS` is in);
- turns on the Claude Code sandbox through `--settings`: no network (`allowedDomains` empty), writes only in the run dir and the CLI's per-user temp dir, and OS-level read denial of the source repo, its main checkout and every worktree, every entry of `/tmp` and the parent's `$TMPDIR` (other sessions leave review diffs and scripts there) except the CLI's per-user temp dir `/tmp/claude-<uid>/`, which sandboxed Bash gets as `TMPDIR` and must stay writable (its existing entries are denied one by one), the manifest dir, every other run dir, and every entry of `~/.claude` (client state that can hold the answer: `state`, `plugins` with newer plugin versions, `file-history`, `history.jsonl`, `backups`, …) except `projects/`, whose entries already there are denied one by one (the source session's transcript among them): the run's own project dir is created there after it starts, and the CLI saves large tool outputs in it for the model to read back. When `~/.claude` is a symlink into a denied tree (a dotfiles checkout, or the source repo itself), that tree is denied entry by entry on the way down to the real `projects/` for the same reason; matching `Read` and `Edit` deny rules, plus `WebFetch`/`WebSearch`. Inside `/tmp` there is one `Read` rule per denied entry, plus an `Edit` rule for each entry already inside `/tmp/claude-<uid>/` (other sessions' scratch files: the run can write its own files there, not overwrite theirs), and no rule on `/tmp` or that dir as a whole: the sandbox turns `Edit` deny rules into write denials for Bash and `Read` deny rules into read denials for Bash (both observed on CLI 2.1.322), so a rule on all of `/tmp` would also cover Bash's own temp dir. A first version had one `Read` rule for all of `/tmp`: Bash could create files there but not read them back, and critique subagents retried around it, which inflated rounds and cost;
- points the flow's own state (telemetry, journal, dashboard, model strategy, tracker and repo config) and `TMPDIR` into the run dir. Run dirs live under `$SIMO_DEV_REPLAY_WORK`, else `~/.cache/simo-dev-replay/` — not under `/tmp`, which is denied.

`--safe-mode` is not used: it also drops `--plugin-dir`. `HOME` is not overridden: the CLI's login lives there. `claude` runs in its own process group, so its Bash children die with it on a timeout or a Ctrl-C (which the harness forwards as a kill: the group no longer gets the terminal's signal). `run` refuses when `CLAUDE_CONFIG_DIR` is set: the deny list and the transcript lookup assume `~/.claude`. Entries denied one by one are those present when the run starts: anything created later in `/tmp`, in `~/.claude` or in `~/.claude/projects` (another session's or another batch's transcript) is not denied to it.

The CLI shows the model the remaining `--max-budget-usd` in a system reminder, and in a pilot run the model cut its critique short "to stay within budget": a tight cap changes the step it measures. Set it well above the expected cost and keep the real spending limit in `--budget`.

Fidelity: memory files, skills and agents tracked in the repo's own `.claude/` stay in the snapshot on purpose, because the source session had them too; the manifest says so in `snapshot_claude_md`. The replay prompt answers every gate itself (the Recommended option, `proceed` instead of `edit` or `restart`) and caps the plan gate at two showings.

Check the sandbox before spending on real runs: the `sandbox-smoke` step runs the Bash commands listed in its fixture's brief, one per line as `NAME: command`, and passes only when `ECHO` prints `ok`, `DENIED` (a read inside the source repo; point it at the main checkout, not at a worktree that will be removed) fails with "Operation not permitted" (the sandbox, not just a permission rule), `FETCH` (a fetch from the local origin) prints `fetched`, `NET` (a request to the internet) prints `blocked` or the sandbox proxy's `CONNECT tunnel failed`, and `TMP` (a write in `$TMPDIR`, read back) prints `tmpok`. Run it with a small model and a small cap.

## Outcome checks for plan-write

`check_plan` reads the plan the run wrote (the new file under `docs/plans/`, preferring the path on the run's `PLAN:` line) against the snapshot:

- `tasks`, `lines`, `files` — size;
- `modify_missing` — `Modify:` paths that exist neither in the snapshot nor as a `Create:`/`Test:` of the same plan;
- `create_existing` — `Create:` paths that already exist;
- `python_blocks`, `python_errors` — fenced python blocks, and those that parse neither as a module nor as a function body (reported only: approved plans carry fragments);
- `pytest_refs`, `pytest_unresolved` — test files named in `pytest` commands that neither exist nor are created by the plan (paths are matched by suffix, so absolute paths of another checkout still resolve);
- `pass` — at least one task and no missing, existing or unresolved path.

Calibrate a new fixture by running `check_plan` on its `reference_plan` against a snapshot of its base: an approved plan must pass. To grade a plan by what it builds, execute it (next section).

## Executing a plan: the plan-execute step

The `plan-execute` step runs `simo-dev:plan-execute` on a frozen plan and grades the code it leaves behind. Use it to compare plans (a lean plan against a full-code one) on what they cost to write *and* to execute, and on whether the result works.

The plan is the variant, the plugin stays fixed: every variant of a plan-execute fixture carries one `,plan:<file>` part, and other steps refuse it. A plan saved by an earlier run (`<run-id>.plan.md` next to `<run-id>.json`) goes back to the path that run wrote it to, its old snapshot paths rewritten to the new one, and the run records `input_run` and `input_cost` (that run's cost); any other file lands under `docs/plans/` with no input cost. The plan is committed in the snapshot, so the diff is the execution only.

The prompt answers the skill's questions itself, skips every release, version bump, commit, push or merge request step, invokes no skill after the Report, and ends with `DONE` or, on a hard stop, `BLOCKED: <reason>`. The run-local model strategy pins `plan_execute` to `--execute-model` (default: `--model`). The run uses `--permission-mode auto` (recorded as `permission_mode`): under `acceptEdits` the CLI refuses Write and Edit under the repo's `.claude/` as sensitive files, explicit allow rules included (CLI 2.1.322), so a plan touching a plugin there could not run; in auto mode the classifier decides, as in an interactive session, and deny rules still hold. Other steps keep `acceptEdits`. `run --permission-mode <mode>` overrides the step's mode for the whole batch. Use `bypassPermissions` when auto mode's safety check is unavailable in the child session: every Bash call then comes back denied and the run ends `BLOCKED` within seconds (observed 2026-11-13; the canary, under "Running and comparing", now stops such a batch after its first run). The sandbox still confines reads and network. Compare batches only when they ran in the same mode.

The fixture: `fixture.json` with `step: plan-execute`, `base`, optional `reference_commit`, `suite` (test dirs whose files at the base are run again after execution), `estimate_usd` (the per-run estimate before any run exists), `acceptance_stretch` (test names that count apart), and an `acceptance/` dir of pytest files; no `brief.md` is needed. `check_execution`, after the run:

- `done`, `blocked`, `reason` — from the last `DONE` / `BLOCKED:` line;
- `diff_files`, `diff_lines` — against the plan commit; the full diff is saved as `<run-id>.diff.txt`;
- `own_tests` — pytest on the test files the plan names (`Test:` bullets and `pytest` commands);
- `suite` — pytest on the suite's base test files; `regressions` are failing test ids that do not fail on a clean snapshot of the base (computed once, cached as `baseline-<fixture>-<base>.json` in the manifest dir);
- `acceptance` — the fixture's `acceptance/` copied into the snapshot as `.replay-acceptance/` only after the run ends (the fixture dir is inside the denied source repo, so the run never reads it), run with `REPLAY_REPO` set to the snapshot: `passed`/`total` mandatory, `stretch_passed`/`stretch_total`, `failed`;
- `confined` — whether grading ran under `sandbox-exec`;
- `pass` — done, not blocked, no failing own test, no regression, every mandatory acceptance test passed.

Pytest output goes to `<run-id>.tests.txt`. Grading runs code the model wrote outside the run's sandbox: on macOS it runs under `sandbox-exec` (no network, writes only in the run dir) with a scratch `HOME` and `TMPDIR`; elsewhere, or with `SIMO_DEV_REPLAY_GRADE_SANDBOX=0`, it runs unconfined. `SIMO_DEV_REPLAY_PYTEST` picks the pytest binary (default: the one on `PATH`).

Acceptance tests grade the contract the brief fixed (CLI, paths, environment, required output), never internal names: a fresh implementation is free to name its helpers. Calibrate them before a paid run, with no model:

```bash
python3 lib/step_replay.py calibrate --fixture <dir> [--ref <commit>]
```

It runs the acceptance tests on a snapshot of `--ref` (default `reference_commit`) and exits 0 only when every mandatory test passes. A probe the reference fails cannot be mandatory: move it to `acceptance_stretch`. Run it on the base too: a mandatory test that passes there grades nothing.

### Preloading the main session's context

A replay starts from a fresh session that holds only the step's prompt. A real flow reaches the step with the main
session already holding the intake, the plan-write and the talk around them (240-390k tokens at plan-execute), and
every main-chain call re-reads that context. Subagents start clean either way. So a fresh replay measures only what a
subagent adds, never what it saves: on 2026-11-17 the clean executor (`TS`) cost ~$0.8 more per run than in-session
execution (`T`) on `flow-dashboard-exec`, with the same outcome.

```bash
python3 lib/step_replay.py preload --session <source-session-id> --out <file> [--until simo-dev:plan-execute]
python3 lib/step_replay.py run --fixture <dir> --variant ... --preload <file>
```

`preload` renders the source session's main chain as text, from its last compaction up to the first invocation of
`--until` (left out): user and assistant text, each tool call with its input, each tool result, and the attachments
that carry the session's own context (`PRELOAD_ATTACHMENTS`: loaded CLAUDE.md files, nested memory, hook output,
queued messages). Left out: thinking (the transcript keeps only its signature), subagent chains, and the attachments
the replay session has in its own version (system prompt, skill, tool and agent listings). Repo paths become
`{{REPO}}` as in `fixture` (`--source-root`, default the session cwd). It prints the file size (a rough count at ~4
characters per token) next to the context the source session's invoking call carried. The two never match: the
source session's system prompt and tool listings are not in the text, and a headless replay has smaller ones. On
the `flow-dashboard-exec` source session (2026-11-17): 442k characters, ~110k tokens by that count, against 297k
tokens of real context; the runs it fed wrote ~170k tokens more to the cache than unpreloaded ones (255-269k against
79-98k), so the text runs nearer 2.6 characters per token. With it, the clean executor (`TSP`, $3.12 / $3.45) no
longer cost more than in-session execution (`TP`, $3.27 / $3.71): main-chain cache reads fell from 5.1-6.7M to
2.4-2.6M tokens, outcome the same (11/11, pass), 2 runs each, no difference beyond noise. `run --preload` puts the text, framed as data the run must not
act on, before the step's prompt in every run of the batch, with `{{REPO}}` set to the run's snapshot. The manifest
records `preload` (`path`, `chars`) and `preload_sig` (a hash of the text): `compare` warns when one label mixes
values, and the per-run estimate counts only earlier runs with the same preload. A first preloaded batch therefore
falls back to the fixture's `estimate_usd`, which does not cover the preload: set `--max-budget-usd` with it in mind.
Compare preloaded variants only with each other. For plan-execute the text holds the plan the source session
wrote, which need not be the variant's `plan:` file: the run is told to execute that file, but an earlier version
of it sits in its context, as it would after a real plan-write rewrite.

## Reviewing a change: the review-loop step

The `review-loop` step runs `simo-dev:review-loop` on a frozen change with seeded bugs of known severity and measures how many the reviewer finds. Use it to compare reviewer variants (model, effort) on recall and cost.

The fixture: `fixture.json` with `step: review-loop`, `base`, `branch`, `estimate_usd`, `tolerance` (lines, default 3) and `items`, one per seeded bug: `id`, `severity` (`low` items are listed but left out of recall), `file` (repo-relative), `lines` (a list of `[first, last]` ranges, more than one when the bug can be pointed at in two places), `test` (the acceptance test that passes once it is fixed, or empty); plus `changes.patch` (the input), `reference.patch` (the same change without the bugs) and `acceptance/`. No `brief.md`. Before the run the harness checks out `branch` in the snapshot and applies `changes.patch` to the working tree, uncommitted, as plan-execute leaves it in the flow. The prompt makes the run headless: fixes are applied, nothing is committed, the reply ends with `DONE`. The run-local model strategy pins `code_review` to `--critique-model`; an effort variant overrides `agents/code-reviewer.md`. The step runs in `--permission-mode auto`, like plan-execute.

`check_review` reads the findings (`[severity] file:line — message`) from the reports of the run's `simo-dev:code-reviewer` subagents. A report does not end up in the subagent's own transcript: it reaches the main transcript as a hand-back (`origin.handbackReportText`, on an attachment for the first report and on a user line for each `SendMessage` resume; observed on CLI 2.1.322), and the subagent's own text is read only when no hand-back exists. Items are matched on the first report only, the review of the frozen input (later reviews read fixed code whose lines have moved): a finding hits an item when its file is the item's (suffix match: the reviewer may cite plugin-relative paths) and its line falls within one of the item's ranges ± `tolerance`:

- `items` — per item: `severity`, `found`, `fixed` (its acceptance test passes after the run);
- `seeded`, `found`, `recall` — items other than `low`, how many were found, the share;
- `fixed` — items other than `low` whose test passes;
- `findings` — distinct findings across every review; `first_findings`, `unmatched`, `by_severity` — those of the first review, those that hit no item (false or unseeded), per severity;
- `rounds` — `verdict:` lines across the reports, i.e. reviews (a resume continues the same subagent, so counting subagents would undercount); the manifest's `rounds` takes it;
- `own_tests` — pytest on the test files `changes.patch` adds; `acceptance` as in plan-execute; `done`, `blocked`, `reason`;
- `pass` — done, not blocked, the patch's own tests green, every `high`/`critical` item found.

## Following up a merge request: the mr-followup step

The `mr-followup` step runs `simo-dev:mr-followup` on a pushed change with fake review comments and a failing CI check, and grades whether each item was resolved. The fixture is shaped like the review-loop one, with `commit_message`, `items` carrying `kind` (`ci`, `comment`, or `decline` for a comment that asks for the wrong thing) and `test`, and a `forge` object: `pr` (`number`, `title`, `slug`), `threads` (`path`, `line`, `author`, `body`), `reviews` (`author`, `state`, `body`), `ci` (`name`, `command`). Before the run the harness applies `changes.patch` on `branch`, commits it and pushes it to the run's local origin.

The flow reaches GitHub only through `gh` and reads the forge from the origin URL. The run gets `lib/replay_fake_gh.py` as `<run dir>/bin/gh`, first on its `PATH`, with its state in `<run dir>/forge/`, and `DETECT_REMOTE_URL` set to the fixture's GitHub slug (the flow's `detect_remote.py` reads it before the origin URL), so `origin` stays the local bare repo. The fake answers `pr view`/`pr list` (the pull request's head is the branch head in the local origin), `api graphql` (the review threads and reviews of the fixture, always unresolved), `pr checks` and `run view --log-failed` (one check that runs `ci.command` on an archive of the head commit, cached per commit, so a pushed fix turns it green), `api` writes, `pr comment`/`edit`/`ready`; any other call exits 1. Every call goes to `forge/calls.jsonl`. The `sandbox-smoke` fixture carries a `forge` too and a `GH` line, which passes when `gh --version` inside the sandbox prints the fake's version (the CLI's shell snapshot does not reorder `PATH`).

`check_followup`:

- `items` — per item: `kind`, `resolved` (its acceptance test passes; for a `decline` item that test checks the behavior was left as it was); `resolved`, `total_items`;
- `pushed` (local HEAD is the branch head in the origin), `dirty` (tracked files changed and not committed), `fix_commits` (commits after the input head);
- `gh_calls`, `ci_reads` (`pr checks` calls), `replies` (thread replies), `rounds` (review-thread fetches, one per round; the manifest's `rounds` takes it);
- `own_tests`, `acceptance`, `done`, `blocked`, `reason`;
- `pass` — done, not blocked, pushed, nothing left uncommitted, the fake `gh` reached, every item resolved.

The prompt skips every release, version bump, merge, QA gate and post-merge step: a baseline measured before a change to mr-followup's own release behavior lands measures the old skill.

`calibrate` on a change fixture needs no `--ref`: it applies `reference.patch` to the base and requires every acceptance test to pass, then applies `changes.patch` and requires every item's test to fail (items with `fails_on_input: false`, such as a `decline`, to pass). It also prints the patch's own tests on both: green on the input for review-loop (the loop's verification gate stops on red tests), red for mr-followup when the CI item is one of them.

After a fix to this grading, `step_replay.py regrade --fixture <dir> [--batch ID]` recomputes the transcript-derived fields of a review-loop fixture's manifests (the transcripts outlive the run dir; `fixed` keeps the value the run's acceptance tests gave) and stamps them `regraded`.

Fixtures hold the answers. A plugin variant newer than this commit would carry them under `private/replay-fixtures/`, so the harness removes that directory from every materialized plugin.

## Manifests

One JSON per run in `$SIMO_DEV_REPLAY_DIR`, else `~/.claude/state/replay/`, named `<run-id>.json`, plus `<run-id>.plan.md` with the plan the run wrote. Fields: `schema`, `run_id`, `batch`, `step`, `fixture`, `base`, `variant` (`label`, `spec`, `version`, `tree`, `commit`), `model`, `effort`, `critique_model`, `execute_model` (plan-execute), `permission_mode`, `input_plan`, `input_run`, `input_cost` (plan-execute), `cli_version`, `max_budget_usd` (the cap the run got), `capped_by_budget` (true when the remaining `--budget` lowered it), `session_id`, `started`, `ended`, `wall_s`, `status` (`ok`, `error`, `timeout`, `interrupted`; the manifest is written even when the harness itself fails or is interrupted mid-run, with `error`), `rc`, `env_dropped`, `env_inherited`, `env_sig`, `may_contain_answer`, `cost` (`total` and `by_model` in dollars from the transcripts with `transcript_usage.PRICES`, `unpriced`), `cli_cost_usd` and `cli_model_usage` (the CLI's own figures), `rounds` (critic subagents launched), `subagents`, `num_turns`, `denials` (permission prompts refused), `sandbox_blocks` (Bash results with a sandbox refusal; the CLI's system prompt and files the run reads quote the same words, so nothing else counts), `plan_path`, `input_head` (review-loop, mr-followup), `preload` and `preload_sig` (with `--preload`), `outcome`, `snapshot_claude_md`, `result_tail`. The run dir is removed unless `--keep`; remove a kept one once read.

## Running and comparing

```bash
python3 lib/step_replay.py estimate --fixture <dir> -n 2 --variants 2
python3 lib/step_replay.py run --fixture <dir> --variant A=ref:<base> \
  --variant B=ref:<base>,override:skills/plan-write/SKILL.md=<file> -n 2 --max-budget-usd 8 --budget 40
python3 lib/step_replay.py compare --fixture <name>
```

Two checks guard the spend, both measured on batches that lost money: a fixture whose fake CI never turned green ($12.93) and 13 runs that ended `BLOCKED` in ~12 s because the auto mode's safety check was unavailable in the child ($5.72).

- **Fixture CI check, free, before any model call.** When the fixture has a `forge.ci.command`, `run` builds two snapshots of the base, one with `reference.patch` and one with `changes.patch`, each committed and pushed as `mr-followup` gets it, and reads the check through the fake `gh` (`gh pr checks`) as a run does: under `sandbox-exec` with a profile built from the run's own sandbox settings (the same deny list, writes only in the run dir and the CLI's per-user temp dir, which is the `TMPDIR` the fake `gh` inherits there, no network). The check must be `pass` with `reference.patch` and `fail` with `changes.patch`; otherwise `run` prints the CI log tail and exits 2 with no run started. Where `sandbox-exec` is missing, or with `SIMO_DEV_REPLAY_GRADE_SANDBOX=0`, the check runs unconfined and says `confined=False`. Fixtures without a CI command skip it.
- **Canary.** The batch's first run (index 1 of the first variant) runs alone. The batch stops after it, exit 3, when its status is not `ok` (error, timeout), it ended `BLOCKED`, or it reported permission denials in under `CANARY_FAST_S` (60) seconds. Its manifest stays, as for any run. Otherwise the remaining runs start.

Runs alternate between variants. The per-run estimate is the mean cost of earlier ok runs of the fixture with the same main model, critique model and effort (`estimate` takes `--model`/`--critique-model`/`--effort` too, with the same defaults as `run`; a run with no transcript cost counts with the CLI's own figure), else a flat default, never above `--max-budget-usd`; `run` refuses when the estimate passes `--budget` (unless `--yes`, which runs until the budget is spent) and stops when the remaining budget is below the estimate. A single run can cost twice the mean, so each run's `--max-budget-usd` is also lowered to the remaining budget: the batch stays within `--budget`, up to the cost of the turn the CLI is in when it hits the cap. A lowered cap is visible to the model and can change the run; such manifests carry `capped_by_budget: true`.

`compare` groups runs by step and fixture, then by variant label (cost = the run's own plus `input_cost` when present; the table then adds `run $`, the run's own mean, and `accept`, the mean of mandatory acceptance tests passed; `recall` for review-loop runs, where a run with no recall counts as 0), across batches (`--batch` filters), with `n`, `err` (runs not ok), mean, min, max, spread, pass rate, mean rounds and mean wall time; it warns when one label mixes plugin trees, models, efforts, critique models, CLI versions, bases, `env_sig` or `preload_sig`. Every run counts except an `interrupted` one (a Ctrl-C says nothing about the variant; `compare` says how many it left out): an error or a capped run lowers the pass rate. The verdict against the baseline (label `A`, else the first):

- fewer than 2 runs in either variant → insufficient;
- lower pass rate → worse outcome;
- `max(B) < min(A)` → cheaper beyond noise; `max(A) < min(B)` → costlier beyond noise;
- otherwise, a higher pass rate for B → no cost difference beyond noise, better outcome;
- otherwise no difference beyond noise.

With 2 runs per variant, two identical variants still get a "beyond noise" verdict about 1 time in 3; with 3 runs, about 1 in 10. An A/A run measures the noise; read such a verdict there as data about the noise, not as a failure.

## Adding a step

A step is one `STEPS` entry: the skill to invoke, the critic agent type whose subagents count as rounds (an outcome's own `rounds` wins), a prompt template (with the gate answers the replay scripts), an outcome check `(snapshot, result text, ctx) -> (output path, outcome dict with pass)`, the permission mode, whether the fixture needs a `brief.md`, and an optional `prepare(snapshot, fixture, run dir)` that lays out the frozen input and returns extra environment for the child.

A dashboard section reading the manifests is the next step: per fixture, one row per variant with n, mean cost, spread, pass rate and rounds, and the verdict.
