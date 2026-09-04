---
name: task-intake
description: Use when starting work from a ticket OR a free-text task prompt. Produces a worktree + detailed plan. First step of the canonical task flow.
---

# Dev Task Intake

Entry point of the canonical task flow:
`simo-dev:task-intake → simo-dev:plan-execute → simo-dev:review-loop → simo-dev:commit → simo-dev:mr-followup`

Arguments: a ticket key (e.g. `ACME-123`) or a free-text prompt. If missing, prompt the user.

## Steps

### 1. Determine input mode

Argument matches `^[A-Z][A-Z0-9]+-[0-9]+$` → **ticket mode**; else → **prompt mode**.

### 2. Gather context

**ticket mode**: fetch by project prefix — look up `fetch_skill` for the ticket's prefix (`python3 "$R/lib/tracker_config.py" get <PREFIX> fetch_skill`); configured → invoke it. Not configured → ask the user which skill/CLI fetches tickets for that prefix, then offer to remember the answer (`python3 "$R/lib/tracker_config.py" set <PREFIX> fetch_skill <value>`). Capture title, description, AC, status, links, project key.

**Ensure ticket is In Progress** (idempotent, forward-only):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/tracker_config.py" run "<ticket-prefix>" transition_cmd "<ticket-key>" "In Progress" --sync-parent || echo "WARNING: transition_cmd failed for <ticket-key> -> In Progress (continuing)"
```

No-op if `transition_cmd` is not configured for the prefix — this covers a read-only tracker (fetch works, transitions fail) with no special-case in this skill body, and also if the ticket is already at/past target. Any tracker-specific idempotency or flag-clearing behavior lives entirely inside the configured `transition_cmd` script, invisible to this skill. `DEV_FLOW_NO_TRACKER=1` / `.claude/no-tracker` marker skip all auto-transitions AND the step-5 ticket question. `--sync-parent`: when `<ticket-key>` is a sub-task (step 2.5 already routed here instead of the umbrella), this also syncs the umbrella's status — see step 2.5.

**Prompt mode**: use the prompt as task description. No fetch.

### 2.5. Umbrella split check

Work crossing **more than one project/repo or team** is not a single ticket: split it into one sub-task per boundary, leaving the original as an **umbrella** — a coordination ticket, never "the work to do" directly. Each sub-task carries its own branch, worktree, MR and status cycle.

Signals — any one of them fires the check: the ticket names more than one repo; it already has sub-tasks; the description carries a numbered "rollout steps" list; some steps belong to another team; some steps are blocked by external dependencies while others are not.

On a hit, **propose the split before opening the worktree** (step 6) and before planning (step 7): one line per sub-task, `[<repo>] <what>`, then ask. Never plan an umbrella as one unit.

- Already has sub-tasks → intake the **sub-task** key, not the umbrella.
- Sub-task creation is capability-gated: look up `supports_subtasks` and `create_skill` for the umbrella's prefix (`python3 "$R/lib/tracker_config.py" get <PREFIX> supports_subtasks`, `... get <PREFIX> create_skill`). `supports_subtasks` == `true` AND `create_skill` set → create each sub-task via the configured `create_skill`, passing the parent/umbrella key. Otherwise (either unset, or `supports_subtasks` == `false`) → do not attempt automated creation; present the one-line-per-sub-task list above as guidance only, and tell the user to create/link the tickets by hand, or configure `create_skill`/`supports_subtasks` to automate it next time.
- **Gated sub-tasks get a "blocked by" link, when configured.** The split signals above already distinguish gated parts from ready ones — record that dependency instead of only stating it in the split proposal text. After creation, for each sub-task blocked by another part of the same split (or by an already-known external ticket key), look up `link_cmd` for the prefix (`python3 "$R/lib/tracker_config.py" get <PREFIX> link_cmd`); set → `python3 "$R/lib/tracker_config.py" run <PREFIX> link_cmd <blocked-key> <blocker-key>`, best-effort (non-zero exit only warns, continue — same pattern as `transition_cmd`). Not configured → no special-case; the dependency stays stated in the split proposal text only, same fallback shape as unconfigured `create_skill`.
- **Opening state**: whatever `create_skill` gives a new ticket by default (the project's own initial workflow state — often `Backlog`, not necessarily `To Do`). Never force a specific state here — that is tracker/project-specific and step 2's own "Ensure ticket is In Progress" already moves it forward once intake for that sub-task actually starts.
- **After creation, continue on the sub-task(s) ready to start now, when automated creation ran.** Applies only when the capability gate above actually created tickets — the manual path (guidance-only, no `create_skill`) has no key yet to continue on; stop there as today, after presenting the list. With created keys: exactly one ready now (not blocked/gated, per the signals above) → continue this session's intake on it directly (its key; skip re-fetching what step 2 already has for the umbrella if the sub-task's own scope is a strict subset). More than one ready now → ask which to start here, or — if `mcp__ccd_session__spawn_task` is available — offer to spawn one session per ready sub-task (each gets its own worktree at step 6). None ready now (every sub-task is blocked/gated) → the split genuinely is this session's whole output; report it and stop, same as late promotion's step 4 leaves a not-yet-startable part for its own future `task-intake` invocation (re-invoking on that key lands in ticket mode per step 1 — it already exists).
- **Too big for ticket + sub-tasks** (more than ~5 sub-tasks, or the split itself spans multiple planning horizons) → escalate one level: the umbrella becomes an **Epic**, each sub-task becomes a **User Story** under it (optionally further split into its own sub-tasks). Same capability gate and creation flow, one level up; propose the Epic/Story breakdown the same way, then ask.

#### Parent status sync

The umbrella's status tracks the **aggregate of its sub-tasks**, forward-only, never a status frozen at intake: one sub-task still `In Progress` → umbrella `In Progress`; every sub-task at `Testing` → umbrella `Testing`; every sub-task at/past `Done` → the umbrella needs a **whole-feature verification** before it can close (a per-sub-task check does not confirm the parts work together), so it is never auto-closed — verify, then transition it to `Done` by hand.

Mechanism: `jira_forward_transition.py` (the JIRA `transition_cmd` binding) takes `--sync-parent` — after transitioning a leaf, it walks the leaf's Jira parent (if any) forward to the minimum-advanced state among ALL its sub-tasks, or exits 7 (with a "verify and close by hand" message) when that minimum is `Done`. `simo-dev:commit` and `simo-dev:mr-followup` add `--sync-parent` to their existing `transition_cmd ... --leaf-only` / post-merge calls — no action needed here beyond intaking sub-tasks normally; sync happens automatically at every leaf transition from then on. `--leaf-only` (refusing to transition an umbrella key directly) and `--sync-parent` (syncing a leaf's parent) are orthogonal — both can be passed together with no conflict, though in practice only leaf calls carry `--sync-parent`.

The exit-7 case only fires through a call that actually carries `--sync-parent` when a sub-task reaches `Done`. simo-dev's own wired call sites stop at `post_merge_state` (`Ready for Testing` by default) — `Ready for Deploy`/`Done` happen later, at release time, via each project's own release skill (`docs/canonical-flow.md` "Ticket auto-transitions"), which lives outside this plugin and does not (yet) call `jira_forward_transition.py` with `--sync-parent`. Until a release skill adopts the flag, a sub-task reaching `Done` does not auto-sync its umbrella — check it by hand at release time, same as before this feature.

#### Late promotion — multi-system impact discovered mid-flow

The signals above can surface after intake ends — typically during `simo-dev:commit` step 2 knowledge harvest, when the diff or a doc turns up an impact on another system that wasn't visible from the ticket alone (e.g. a schedule change that also needs a Grafana alert rule flipped in another repo) — or earlier, still **mid-intake**, when step 4's own gate investigation (a redirect loop chasing a fuzzy signal) turns up the same kind of signal before any worktree or plan exists (see step 4's "Split signal found mid-gate"). Treat both the same way, with one exception for the mid-intake case (below):

1. Propose the split exactly as above (one line per part, ask).
2. Create the sub-task(s) under the **current** ticket via the same `create_skill`/`supports_subtasks` gate — the current ticket becomes the umbrella retroactively. One sub-task should cover the work already done on the current branch; the caller decides what its Jira state should be (usually the same state the umbrella is currently at).
3. **Point this branch's flow at the sub-task, without renaming an already-pushed branch**: write the sub-task key to `.claude/ticket-key-override` in the worktree root (single line, e.g. `PROJ-1994`) — `derive_ticket_key.py` reads it before falling back to the branch-derived key, so every later transition in this session (commit, mr-followup) targets the sub-task automatically. Worktree-local and untracked; never commit it — check `.claude/ticket-key-override` is gitignored in this repo BEFORE writing it, same as step 6 does for `.worktrees/`: this repo's own `.claude/.gitignore` carries the entry, but another repo running this flow needs its own (`.git/info/exclude` — local, uncommitted, same mechanism step 6 uses — is safer than a tracked `.gitignore` line, since it needs no commit of its own in a repo this session may not otherwise be touching).
4. If a second, not-yet-startable part exists (e.g. gated on a deploy), create its sub-task too and leave it for its own future intake — it gets its own branch/worktree when picked up.

**Mid-intake (from step 4, before step 6 opens a worktree): steps 1-2 apply as written, but skip step 3** — there is no branch yet to redirect via `.claude/ticket-key-override`. Continue from step 2 on the sub-task key instead (context re-fetch optional per step 2.5's "ready now" bullet, but re-run the In-Progress transition — the ordinary case's guarantee that step 2 moves the new key forward still applies here), then step 5 onward.

Worked example: PROJ-100 (a service's schedule tune) turned out to also require flipping 2 live Grafana alert rules. Split into PROJ-101 (the schedule work, already done — override written on the existing branch) and PROJ-102 (the alert gate flip, gated on deploy — a fresh sub-task, intaken separately later). PROJ-100 became the umbrella, synced per the section above once both land.

### 3. High-level system-impact analysis

**Repo pre-flight (check first)** — one call, because reading the toplevel and the cwd separately lets them answer from different repos:

```bash
git rev-parse --show-toplevel --show-prefix --abbrev-ref HEAD --path-format=absolute --git-dir --git-common-dir
```

**Check the exit code and the line count before reading anything positionally.** Five lines and exit 0 is the good case: toplevel, prefix (**empty line** when the cwd *is* the toplevel), `HEAD` if detached else the branch, absolute git dir, absolute common git dir. An **unborn HEAD** prints only **three** and exits 128 — `rev-parse` echoes the literal `HEAD` before the fatal — so a positional read takes `HEAD` as the branch and gets no line 5, making `<REAL>` = `dirname ""` = `.`: a relative path that resolves against the ambient cwd instead of failing, which then passes the tests below (verified 2026-08-05, git 2.55.0). Short output or non-zero → STOP, and do not read the lines.

`--path-format=absolute` only affects the options after it, so it must precede both dir options. Lines 4 and 5 are equal exactly when this is **not** a linked worktree: git gives each linked worktree its own git dir under `<common>/worktrees/<x>` while the common dir stays shared (`git-worktree(1)`, "the .git directory"), so equality holds for a main checkout and for a submodule working tree and fails for every linked worktree, including one of a bare repo (checked on the 11 live worktrees here plus 11 constructed shapes, 2026-08-05). `<REAL>` = `dirname` of line 5 = the main checkout that **owns** this tree. The verdict is a function of those values — never of how absurd the path looks:

- `<REAL>` contains `.claude/plugins/cache/` or `.claude/plugins/marketplaces/` → a marketplace/cache copy, or a worktree of one: a different project. **STOP.**
- **prefix** under one of those paths → a git-less rsync copy. `rev-parse` walked *up* to an unrelated outer repo, so the toplevel carries no `plugins/` segment and a toplevel-only test never fires — example: the worktree came out missing one of the plugin's `skills/<name>/` subdirectories entirely. **STOP.**
- **line 4 ≠ line 5** and neither of the above → a linked worktree of a legitimate repo. **Not a pre-flight stop**: forward to step 6, which moves it, leaves it alone, or stops and asks — depending on its location. Do not route it to `lib/repo-preflight.md` — that resolves the *marketplace* remote, and this tree's remote is `<REAL>`'s. Use the two dirs here, not `<REAL>` ≠ toplevel: for a submodule working tree `<REAL>` is a `<super>/.git/modules/<sub>` path, so that proxy would label a perfectly normal checkout a misplaced worktree.
- **line 4 == line 5** → a main checkout or a submodule working tree: nothing special, step 6 creates a worktree the normal way.

Also, if the task involves an existing path, verify it's tracked: `git ls-files <path>` (empty = not here → warn, wait for confirmation). Must clear before anything below writes artifacts to the tree.

> Recovery procedure (find the real clone, residual-case cleanup): `lib/repo-preflight.md` — load when either STOP or the tracked-path check fires.

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
EXPLORE_MODEL=$(python3 "$R/lib/model_strategy.py" get explore_research)
[ -z "$EXPLORE_MODEL" ] && EXPLORE_MODEL="sonnet"
```

Dispatch **up to 2 `Explore` subagents in parallel** (single message; both pass `model: $EXPLORE_MODEL`):

- Agent A: impacted repos / services / modules (file paths, boundaries).
- Agent B: existing relevant patterns (similar features, utilities, tests; reusable functions + paths).

Aggregate into a ≤200-word summary printed as normal reply text in chat (never only in tool-call narration or a question widget): 1-line gist, impacted systems, 2-4 bullet initial direction, open questions/risks.

### 4. Direction confirmation gate (always-on)

Fires unconditionally — the main defense against "implemented the wrong thing".

**4a.** Append to the summary: 2-3 candidate directions, one line each, recommended one marked (`→ A (recommended): ...`). Surface any fuzzy signal explicitly ("⚠ AC empty / sync vs async unresolved / ...").

**4b. Fuzzy signals** (drive the default answer): AC empty/vague/contradictory; one-liner prompt with no clear target; multiple plausible interpretations; unresolved design choices; undocumented cross-team impact; unaddressed perf/migration/data-loss risk; open architecture-level decisions; **each non-trivial choice silently resolved inside the recommended direction** (a decision you made for the user is an ambiguity you hid, not one you removed — count it).

**4b-bis. Recommended path** (print one line: `Recommended path: <X> — <why>`):

| Signal | Path |
|---|---|
| 0-1 fuzzy signal, scope clear | `direct` — gate default `y` |
| 2-4 discrete open decisions | `simo-dev:grill-me` (decisions mode) |
| open scope / architecture-level / ≥2 fuzzy signals / prompt-without-ticket | `simo-dev:grill-me` (design mode) |

Escalation is safe wherever grill-me writes artifacts — the step-3 pre-flight already confirmed the cwd is a legitimate repo. If it forwarded a misplaced worktree to step 6, the artifacts still survive (the move carries untracked files) but any absolute path already quoted to the user goes stale — re-state it after the move.

**Split signal found mid-gate.** If the investigation behind this gate — including a redirect loop chasing a fuzzy signal — surfaces one of step 2.5's split signals (e.g. an external dependency blocks only part of the work), apply **late promotion** (step 2.5's "Late promotion" section, mid-intake case) right here, before asking for `y`: propose the split, create sub-task(s), continue on the right key. Don't wait for the user to ask — worktree and plan don't exist yet at this point, so redirecting now is free.

**4c. Summarize, then ask (always).** Restate the recommended direction in 2-4 plain bullets (what, where, key choices) — the user must be able to approve from this block alone. Then a **`Choices made:` list** — every non-trivial decision embedded in the recommended direction (sync vs async, new field vs reuse, library picks, ...), one line each, ending with "— `deep-dive` to challenge these". Omit the list only when there is genuinely nothing decided beyond the literal request. **Do NOT use AskUserQuestion for this gate** (observed twice: the summary gets crammed into truncating widget fields). END THE TURN with plain markdown: bullets, candidates, then:

> "Direction look right? [y / redirect / deep-dive]"
> Default: `deep-dive` if ≥2 fuzzy signals OR prompt mode without a ticket OR open architecture decision; else `y`.

- `y` → step 5.
- `redirect <reasoning>` → incorporate, re-run Explore if needed, re-present. Loop until `y`.
- `deep-dive` → escalate per 4b-bis, announce in one line, invoke, re-present this gate.

Gate exits only on `y`.

### 5. Ticket decision (prompt mode only)

Skip in ticket mode (an explicit key always wins; the marker does not apply to it).

`.claude/no-tracker` marker or `DEV_FLOW_NO_TRACKER=1` → skip the question silently, continue with no ticket (branch/plan naming without scope segment). Check both mechanically, using step 3's already-established `<TOP>` as `<repo-root>` — never rely on remembering the marker exists:

```bash
[ -f "<repo-root>/.claude/no-tracker" ] && echo "no-tracker marker present — skip the question silently"
[ "$DEV_FLOW_NO_TRACKER" = "1" ] && echo "DEV_FLOW_NO_TRACKER=1 — skip the question silently"
```

Either line printing → skip straight past the question below, no ticket.

Otherwise ask — never default to creating a ticket:

> "Create a ticket for this work? (y/N) Project/prefix?"

Default is **always `N`**, even if the task brief implies a ticket. On explicit `y` → look up `create_skill` for the given prefix (`python3 "$R/lib/tracker_config.py" get <PREFIX> create_skill`); configured → use it. Not configured → ask which skill/CLI creates tickets there, then offer to remember the answer (`python3 "$R/lib/tracker_config.py" set <PREFIX> create_skill <value>`). Description = analysis summary; capture the new key; continue in ticket mode. On `N` → continue without.

### 5.5. Direction comment

Runs always, both modes, right after step 5 resolves (so the ticket key — if any, including one just created in step 5 — is known). No key, or `comment_cmd` unconfigured for its prefix: `post_tracker_comment.py` (`lib/post_tracker_comment.py`) handles both as a silent no-op on its own — nothing here needs to special-case them.

Body = the direction approved at step 4's gate: the candidate directions with the chosen one marked, the final `Choices made:` list, and any `⚠` fuzzy-signal lines that were surfaced. Write it to a temp file, then:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/post_tracker_comment.py" "<KEY-or-empty>" "<tmpfile>"
```

Announce the result in one line: `Direction: <script's stdout line>` (e.g. `Direction: posted: PROJ-1234` / `Direction: skipped: no ticket key`).

### 6. Branch and worktree

Branch name: `<type>/<jira-key-lowercase>-<short-desc>` with a ticket key (e.g. `feat/proj-1234-topn-by-maxrank`), `<type>/<short-desc>` without. `<type>` ∈ `feat|fix|chore|refactor|docs|test|perf`; `<short-desc>` = 3-6 hyphenated lowercase tokens.

**Worktree pre-flight**: already verified at step 3. Re-check only if the cwd changed since (redirect to another repo, manual switch) — re-run the step-3 check there. Artifacts already written in a wrong repo (pre-flight bypassed / older mid-flight session) → recovery in `lib/repo-preflight.md` (cp, never cross-repo stash).

**Worktree — always, no opt-out** (the `.claude/no-worktree` marker is retired): **manual `git worktree add`** at `<repo>/.worktrees/<name>` (branch name minus `<type>/` prefix). Create `.worktrees/` if missing, ensure gitignored. ⚠ **Never the native `EnterWorktree` tool**: it moves only the session dir while the Bash cwd is unreliable in both directions — split-tree writes have put commits on `develop`. Always `git -C "<worktree-abs>"` + absolute paths, substituted as literals (canonical wording: CLAUDE.md "Worktree cwd gotcha"). **Do not act on this paragraph before reading the next one** — if this session already sits in a worktree, adding one here is the duplicate-MR bug.

**Already sitting in a harness worktree — move it into place and adopt it, never add a second one.** A session launched from the spawn menu's "spawn in worktree" entry already sits in one: random dir name, and a `claude/*` branch whose name is a *different* random word pair (`heuristic-robinson-1869ce` carried `claude/dazzling-herschel-d2018d`). **Step 3's pre-flight already produced all five values — reuse those literals rather than re-deriving them here**, including its exit-and-line-count verdict. What must not happen is a *bare* re-read: step 3 can be many turns back (gate loop, grill-me, ticket creation) and an ambient `git rev-parse` answers from whatever repo the cwd points at by then. Re-reading with `git -C "<TOP>"` is fine and is what the step-4 `redirect` case needs — re-run step 3's call in the new repo and take the new values. Never detect from the branch alone — most harness worktrees observed 2026-07-28 sat on detached HEAD, where `git branch --show-current` prints nothing and exits 0.

Substitute `<TOP>` and `<REAL>` as **literal absolute paths** into every command below — never a shell variable, which is empty by the next Bash call and degrades silently rather than failing. Mechanism and evidence: CLAUDE.md "Worktree cwd gotcha" (the canonical copy — do not restate it here).

**The `.claude/worktrees/` path is the harness signal that held up — do not try to infer it from the branch.** A detached HEAD says nothing about who made the tree: a release or inspection worktree checked out on a tag reports `HEAD` too, whether it sits inside `.worktrees/` or somewhere like `~/projects/myservice-v1.2` (verified 2026-08-05). Two designs were tried and both hijacked such a tree — `switch -c`ing it off its tag and rebasing it. `<HEADREF>` is used only *inside* the adopt steps, to pick the rename form. What authorizes a rename is the path.

Definitions — the first three are step 3's output lines, bound here so nothing below has to re-derive them (`git branch --show-current` in particular prints nothing on a detached HEAD, which would send every harness tree to row 4):

- **`<TOP>`** = line 1, **`<HEADREF>`** = line 3 (`HEAD` when detached, else the branch), **`<GITDIR>`** = line 4, **`<REAL>`** = `dirname` of line 5.
- **harness path** — `<TOP>` contains a `/.claude/worktrees/` segment, at any depth. That one pattern covers every misplaced shape seen here: `.claude/worktrees/<random>`, the doubled `.claude/.claude/worktrees/<random>`, one parked under `.claude/plugins/cache/**`, and one nested inside another worktree — all 7 live harness trees match it (2026-08-05). Treat it as the observed set, not a closed one.
- **canonical path** — `dirname "<TOP>"` equals `<REAL>/.worktrees` exactly. A prefix test is not enough: a worktree spawned from *inside* a worktree lands at `<parent-worktree>/.claude/worktrees/<random>`, which starts with `<REAL>/.worktrees/` yet sits in the directory `mr-followup` step 8 removes when the parent task merges. Nested worktrees share the main `.git`, so `<REAL>` is the main checkout either way (verified 2026-08-05).
- **`<WT>`** — the worktree this step ends up working in: `<REAL>/.worktrees/<name>` once row 3's move-or-resume below has run (moved fresh, or already there from an earlier run), `<TOP>` for every other row. A literal too, never re-derived from `$(pwd)`.

**First matching row wins**, and row 1 is evaluated **before** the `<REAL>` assert below, because `<REAL>` is meaningless for the shapes it catches:

| # | Condition | Action |
|---|---|---|
| 1 | **line 4 == line 5** | not a linked worktree at all — a main checkout or a submodule working tree → normal `git worktree add`, `<repo>` = the toplevel. First, because a main checkout can sit on a detached HEAD (bisect, tag inspection) and a submodule's `<REAL>` is a `.git/modules/**` path that is not a checkout |
| 2 | **canonical path** | already the canonical layout, so not a harness tree → normal `git worktree add`, `<repo>` = `<REAL>`. Someone else's task tree gets a sibling. If it is this task's own tree resumed, the existing-branch block at the end of this step handles it — with a gap that predates this rule and is not fixed here: both of that block's outcomes issue a `git worktree add`, which cannot run from inside the tree (`fatal: '<path>' already exists`). The old `*/.claude/worktrees/*` gate routed the same input to the same place |
| 3 | **harness path** | → **move** to `<REAL>/.worktrees/<name>`, then adopt |
| 4 | anything else | **STOP and ask.** A linked worktree of `<REAL>` that is neither canonical nor a harness path was placed deliberately — a release checkout, a bisect tree, a hand-made worktree. Moving it and renaming its branch is the hijack this row exists to prevent |

Row 3 is the only row that renames, and it requires the harness path. That is the invariant, enforced by construction rather than restated — though it only holds **unattended**: row 4 hands the decision to the user, who may of course authorize anything.

Before rows 2-4 use `<REAL>`, assert it is a real checkout. On a worktree of a **bare** repo, `dirname` of the common dir is the bare repo's *parent* (CLAUDE.md "Repo name from a worktree"), which need not be a repo at all:

```bash
git -C "<REAL>" rev-parse --show-toplevel   # MUST print <REAL> exactly; error or mismatch → STOP and ask
```

Not `--is-bare-repository`: it answers from the nearest **ancestor** repo, so it prints `false` for a plain directory sitting inside somebody's working tree (verified 2026-08-05). Toplevel equality rejects that, and a bare `<REAL>` errors outright. Row 1 must precede it: a submodule's `<REAL>` fails this assert (`fatal: this operation must be run in a work tree`) even though the tree itself is perfectly normal.

Row 3 — moving, or resuming a move an earlier run already made. `<name>` is the canonical branch minus the `<type>/` prefix. A `plugins/` path is not special here: step 3 already stopped on the two shapes that are a **different repo**, so whatever reaches this point is a linked worktree of `<REAL>` in a directory nobody chose. This runs **before** the foreign-commit check below, because that check needs `<WT>` resolved — and on a re-entry, `<TOP>` from this run's step 3 call may already be gone (the previous run moved it):

```bash
# Guard FIRST. Onto an existing directory `worktree move` exits 0 and registers the worktree one
# level deeper (`.worktrees/<name>/<random>`) — silent and wrong. Reachable on a retried adopt, or
# when an earlier `worktree remove --force` left the directory behind. One occupant is benign: THIS
# tree, already moved by an earlier run (the foreign-commit question was answered, then re-entry).
# It is the same worktree iff its git dir is <GITDIR> — that is a resume, not a collision.
[ -e "<REAL>/.worktrees/<name>" ] && {
  [ "$(git -C "<REAL>/.worktrees/<name>" rev-parse --path-format=absolute --git-dir)" = "<GITDIR>" ] \
    && { echo "already moved — skip straight to the foreign-commit check and the adopt steps"; exit 0; } \
    || { echo "STOP: a different worktree occupies the destination, ask"; exit 1; }
}
mkdir -p "<REAL>/.worktrees"
git -C "<REAL>" worktree move "<TOP>" "<REAL>/.worktrees/<name>" || { echo "STOP: move failed"; exit 1; }
# Assert, like the normal-add path does: exit 0 alone does not prove the destination is the worktree.
git -C "<REAL>/.worktrees/<name>" rev-parse --show-toplevel   # MUST equal that path exactly
```

**A non-zero move is a stop, not a soft error.** Refusals observed on git 2.55.0: a main working tree (`fatal: … is a main working tree`), a **locked** worktree (`fatal: cannot move a locked working tree`) — both 128; `git-worktree(1)` also states a worktree containing submodules cannot be moved, and the move is a rename, so a cross-device destination fails too. Not a closed set — treat any non-zero as a stop. Continuing is what makes it dangerous rather than merely failed: `<WT>` would then name a directory that does not exist, and `git -C <missing-dir> ls-remote` exits 128 with **empty stdout**, which adopt step 1 below reads as "not on the remote → safe to proceed" (verified 2026-08-05).

`<WT>` = `<REAL>/.worktrees/<name>` from here on, whether this run moved it or found it already there. Now the **foreign-commit check**, before renaming. A harness tree may carry unpushed commits from an *earlier* task the same session worked on; adopt step 1's `ls-remote` comes back empty for them (never pushed) and adopt step 3's rebase preserves them, so they would ride into this task's MR. Fetch first and verify the ref, or the range itself is the trap this file warns about elsewhere — an unfetched `origin/<base>` exits 128 with **empty stdout**, which reads as "no foreign commits":

```bash
git -C "<WT>" fetch origin <base> --quiet || { echo "STOP: fetch failed — a stale origin/<base> would pass the next check silently"; exit 1; }
git -C "<WT>" rev-parse --verify --quiet "origin/<base>" >/dev/null || { echo "STOP: origin/<base> unresolved"; exit 1; }
git -C "<WT>" log --oneline "origin/<base>..HEAD"   # non-empty → report the commits and ask before renaming
```

The move carries the branch, the commits, tracked modifications **and** untracked files, and deregisters the old path (verified 2026-08-04, git 2.55.0) — nothing to stash for the move itself. It succeeds even when the session's cwd is inside `<TOP>`. Afterwards the old directory is gone, so any later `git -C` on that stale literal fails loudly (`fatal: cannot change to …: No such file or directory`) — that half is safe. The silent half is the opposite: a **bare** command still works, because the shell's cwd follows the rename, while the `pwd` builtin keeps printing the old path and only `pwd -P` shows the new one (verified 2026-08-05, macOS). So a bare `git status` after a move reports the moved tree under a name that no longer exists.

Adopting — `<branch>` is the full canonical name from the top of this step, **including** the ticket segment in ticket mode. Run in this order; each step restores an invariant `git worktree add --no-track ... origin/<base>` would have given for free:

1. **Is the old name already on the remote?** Only row 3 routes here, and the harness leaves a tree either detached or on a `claude/*` branch, so `<HEADREF>` is one of those two in every case observed (2026-07-28 and 2026-08-05, 7 live trees). Detached means there is no old branch at all — nothing can have been pushed, skip this step. Anything else is unexpected: stop and report it rather than picking a rename form. On `claude/<random>`, check before touching anything, substituting the name step 3 reported (never the literal `claude/<random>`):
   ```bash
   git -C "<WT>" ls-remote origin "<HEADREF>"
   ```
   Empty output with a **non-zero** exit is not an answer — see the move-failure note above. Non-empty → the harness branch is pushed and may already carry an MR, so renaming and pushing the new name opens a **second** MR for one change. Ask which resolution, never pick silently: close the existing MR and let `simo-dev:commit` open one on the canonical branch, or keep working under the old name. An MR's source branch **cannot** be retargeted — `glab mr update` has `--target-branch` only (glab 1.110.0) and the API rejects `source_branch` — so "just repoint it" is not an option. The freshen-the-base step below rewrites history, so once pushed the rebase also needs a force-push — a CLAUDE.md permission gate.
2. **Rename — pick exactly ONE form**, per step 3's `abbrev-ref` answer (line 3 of that call):
   ```bash
   # detached HEAD (`branch -m` exits 128: "cannot rename the current branch while not on any")
   git -C "<WT>" switch -c "<branch>"
   ```
   ```bash
   # on a claude/* branch — `branch -m` carries `origin/claude/<random>` over verbatim
   # (verified 2026-07-28), so a bare `git push` would write the OLD remote name. The unset must
   # be guarded: with no upstream configured it exits 128 ("has no upstream information"), which
   # is the normal state whenever step 1's ls-remote came back empty.
   git -C "<WT>" branch -m "<branch>"
   git -C "<WT>" rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1 \
     && git -C "<WT>" branch --unset-upstream
   ```
   Never run both: after `switch -c`, the `branch -m` is a **silent no-op** (exit 0 — the current branch already has that name) and leaves `claude/<random>` behind as a second local branch, which `mr-followup` step 8's `branch -D <name>` never removes. A retried adopt fails on **either** form when `<branch>` already exists locally, both exit 128 (`branch -m` → "a branch named … already exists"; `switch -c` → the same) — so run `git -C "<WT>" branch --list "<branch>"` before whichever form you pick, not just before `branch -m`. And on the detached path a `claude/<random>` that exists but is not checked out survives `switch -c` regardless, so delete it by hand once adopted. Only the first `git push -u origin <branch>` may set an upstream (see `--no-track` below). The rename comes before the park below because `park` refuses on a detached HEAD (`the park commit would not land on any branch`, rc 2) — a state harness trees are routinely found in (7 live trees, 2026-07-28 and 2026-08-05, each detached or on a `claude/*` branch).
3. **Park the pending work, freshen the base, put it back.** Adoption exists because a session was already working in here, so a dirty tree is the normal case — and `git rebase` refuses one outright (`error: cannot rebase: You have unstaged changes`). Not a stash: `refs/stash` is one stack shared by every worktree of the repo, and on a clean tree `stash -u` exits 0 having created nothing, so the `pop` takes whatever was already on top (logged in the author's own notes, §17 — worth keeping a similar log). Freshening belongs in the same step — the harness cut the branch from whatever HEAD the spawning cwd had, five different SHAs across live worktrees on one day, and a stale base makes the branch appear to revert already-merged work (incidents 2026-07-24):
   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/park_and_rebase.py" park --repo "<WT>" --base <base>
   # rc 3 → resolve, then continue WITH the hook suppression, NEVER `--skip`:
   #   git -C "<WT>" -c core.hooksPath=/dev/null rebase --continue
   # then restore — on rc 0, after a resolved rc 3, and on the rc 5 branches whose message
   #   asks for it; never on rc 2 or rc 4. Re-bind R: the `--continue` above is a separate Bash
   #   call, and an unbound R makes this exit rc 2, which reads as "refused, repo unchanged"
   #   while the park commit is in fact still at HEAD:
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/lib/park_and_rebase.py" restore --repo "<WT>"
   ```
   `park` fetches `origin/<base>` and rebases itself, so no `merge-base --is-ancestor` conditional is left here: an already-fresh base makes its rebase a no-op. `--base` is not optional — without it `park` resolves `origin/HEAD`, and this step already knows its base. rc `0` → run `restore`; rc `3` → resolve, `--continue`, then `restore`; rc `4` → nothing to restore, skip it; rc `2` → refused, repo unchanged, **stop**; rc `5` → the repo IS changed, read the message — two of its branches leave a park at `HEAD` and ask for `restore` once you have fixed the cause. Full table: `simo-dev:commit` step 0.5. Read `restore`'s stderr even at rc `0` — a path it could not re-stage is named only there — and the park does not preserve the index/worktree split.

Report one line: final path + new branch name, and whether it was moved or adopted where it stood. Adding a second worktree instead is what produced two branches, two MRs and one piece of work for the same change (`!35` closed by hand against `!36`; `!20` and `!21` same title). `.claude/worktrees/` is excluded via `.git/info/exclude` — local and uncommitted, so a worktree adopted in place shows up untracked in the main checkout on a fresh clone; add the exclusion there if that happens. A moved one lands in `.worktrees/` — ensure that is gitignored in `<REAL>`, same as the normal-add path does, rather than assuming it (this repo's `.gitignore` carries a `.worktrees/` entry; other repos need not).

**Fetch base first, branch from `origin/<base>`** (not the possibly-stale local ref): `git fetch origin <base> --quiet` then `git worktree add --no-track <repo>/.worktrees/<name> -b <branch> origin/<base>`. A stale local base makes the branch appear to revert already-merged work (incidents 2026-07-24); this removes the stale-at-creation case. Later drift — a concurrent pull, or the remote simply advancing mid-session — is the base-freshness guard's job: per task in `simo-dev:plan-execute` when the work runs from a plan, and in `simo-dev:commit` step 0.5 before every commit.

`--no-track` is mandatory: without it git tracks `origin/<base>`, giving the branch an upstream that points at **master** before it exists on the remote — a bare `git push` then targets the base under `push.default=upstream`. Keep the upstream meaningful (set only by the first `git push -u origin <branch>`); never read it as "already pushed" regardless — see `simo-dev:commit` step 5.

**Follow-up on an EXISTING branch/MR — re-verify right before creating the worktree** (idempotent; the step-3 pre-flight ran too early — the race is mid-intake). A concurrent merge can delete the target branch between the first observation and now. Check the **remote** branch — it is authoritative for a live MR, and a merged MR's local `refs/heads/<branch>` can survive a remote delete (`fetch --prune` never removes local branches):

```bash
git ls-remote --exit-code origin "<branch>"   # exit 2 → remote branch gone
```

- Gone → MR merged/closed mid-intake: **pivot to a fresh branch off base** (new MR, the `--no-track ... -b <branch> origin/<base>` form above), do NOT fold onto the dead branch. `git -C <pruned-dir>` silently resolves to the parent repo, so a `git worktree add` failing on the missing ref — not the toplevel assert below — is the only signal without this check. Target code already read from a pre-pivot snapshot of the dead branch → re-read it from the real base after the pivot.
- Live → fetch first, then create the worktree on the existing branch: `git fetch origin <branch> --quiet && git worktree add <repo>/.worktrees/<name> <branch>` (no `-b`, not the new-branch form above). `ls-remote` checked existence, not freshness — a stale local `<branch>` lags the MR head after concurrent pushes, so fast-forward it to `origin/<branch>` before working.

If the worktree's plan (`docs/plans/...`) has a `Depends on: <TICKET>` field, resuming this branch/worktree is not enough on its own — a stale worktree can still compile after its dependency merged with a different shape (incident 2026-08-05). That verification runs in `simo-dev:plan-execute` step 1, not here; a green build is not proof the dependency is current.

Assert before any edit/test/commit:

```bash
git -C "<repo>/.worktrees/<name>" rev-parse --show-toplevel   # MUST equal the worktree abs path
```

**Uncommitted artifacts on the base branch** (grill-me wrote docs in steps 3-4): carry them into the worktree via a throwaway commit on a detached HEAD, never the shared stash stack — do NOT branch in-place.

> Carry commands + full EnterWorktree ban rationale: `lib/worktree-carry.md` — load when creating a worktree or carrying artifacts.

### 7. Detailed plan

Invoke `simo-dev:plan-write` (in the worktree) with the gathered context. Plan path: `docs/plans/YYYY-MM-DD-<jira-key-lowercase>-<short-desc>.md` (no ticket → drop the key segment). Create `docs/plans/` if missing.

### 7.5. Plan review gate (always-on)

Second structural check-point — last chance before implementation. **Read the plan file from disk first** (always — subagent-authored plans never reached this context). Then run the base-freshness check against the plan's declared files — once, synchronously, right here; not repeated later in this same gate loop (an `edit`/`restart` round-trip re-runs it naturally next time this step executes from the top):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/check_base_freshness.py" --repo "<worktree-abs>" --plan-files
```

- rc 1 → fold the printed overlap (the overlapping file paths — `analyze()` returns no commit data) into the Plan summary below, under a `⚠ Base advanced` heading, right after Risks/verification.
- rc 0 or rc 2 → no change to the summary.

Then read the `plan_critique` model config — defaults to `fable` when unconfigured; set the sentinel value `none` (`model_strategy.py set plan_critique none`) to disable automatic plan critique entirely:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
PLAN_CRITIQUE_MODEL=$(python3 "$R/lib/model_strategy.py" get plan_critique)
if [ "$PLAN_CRITIQUE_MODEL" = "none" ]; then
  PLAN_CRITIQUE_MODEL=""
elif [ -z "$PLAN_CRITIQUE_MODEL" ]; then
  PLAN_CRITIQUE_MODEL="fable"
fi
```

Empty (only reached via the explicit `none` sentinel now) → no dispatch. Non-empty → dispatch an `Agent` with `subagent_type: simo-dev:plan-reviewer`, `model: $PLAN_CRITIQUE_MODEL`, `run_in_background: false` (same reasoning as `code-review-protocol` item 2: the gate must see the findings before the user is asked to proceed) — prompt = the plan file content just read from disk plus the gathered context already available at this step. Parse the response's findings in the same `[severity] location — message` line format `code-reviewer` findings use elsewhere in this plugin.

Then show:

- Clickable absolute path; 1-line gist + ticket key + branch.
- **Plan summary** from the file just read (never memory): Goal (header line), Approach (2-3 bullets from Architecture), Tasks (one line per `### Task N:`), Files touched (deduped), Risks/verification (omit if absent), `⚠ Base advanced` (only on rc 1 above — the overlapping file paths), `⚠ Plan critique` (only when the `plan-reviewer` dispatch above returned ≥1 finding — list each as `[severity] location — message`). Cap ~25 lines.

Path + gist + summary MUST be visible markdown in the chat reply BEFORE the gate prompt — unlike 4c, AskUserQuestion IS allowed here, but only in the same turn AFTER the text block (widget fields truncate; never their only carrier). Multiple plan files → one summary block each, single gate.

On rc 1 above, print immediately above the gate prompt: `Recommended: restart — base advanced on file(s) this plan also targets.` (mirroring the `Recommended path:` line style at step 4b-bis). On rc 0 or rc 2, no such line — the gate looks exactly as it does today.

When the `plan-reviewer` dispatch returned ≥1 finding, print a second line immediately above the gate prompt: `Recommended: edit — <n> finding(s) from plan critique.` (`<n>` = the finding count) — on its own line. When `plan_critique` is explicitly disabled (set to `none`), or the dispatch ran and returned zero findings, no such line prints. The two `Recommended:` lines are independent and can both print together; when both fire, `Recommended: restart` prints first, `Recommended: edit` second.

The "Tasks" and "Files touched" bullets above already produce a task count and a deduped file count for the plan just read — reuse those two counts here, do not recompute them. A plan is "large" when its task count is greater than 5, or its file count is greater than 8. When the plan just read is "large" by either measure, print a third line immediately above the gate prompt — `Suggestion: consider spawn_task — plan has <n> tasks across <m> files.` (`<n>`/`<m>` = the two counts) — after any `Recommended:` line(s) above, on its own line. This suggestion never blocks the gate, never mentions a model (spawn_task takes none), and is independent of both `Recommended:` lines — it can appear alone, or alongside either or both of them.

> "Plan written to `<path>`. Review it:
> - **`y`** — proceed to hand-off (step 8).
> - **`edit <feedback>`** — apply changes, re-present this gate.
> - **`restart`** — back to step 4; editing won't fix it."

On `edit` → re-invoke plan-write (or targeted Edit) and loop back. On `restart` → discard the plan, ask: restart from direction (step 4, reuse analysis) or analysis (step 3, re-dispatch Explore)? A `Recommended:` line above is a recommendation, not a block — `y` still proceeds to hand-off regardless of what was recommended. Gate exits only on `y`.

Once the gate above exits on `y`: post a plan comment, same mechanism as step 5.5. Body = the "Plan summary" block already built for the gate prompt (goal, approach, tasks, files touched, risks/verification) — never the raw plan file, which can run long; the summary is already the right size for a ticket comment. Write it to a temp file, then the same `R`-bound call as step 5.5:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/post_tracker_comment.py" "<KEY-or-empty>" "<tmpfile>"
```

Announce `Plan: <script's stdout line>`.

### 8. Hand-off

Discipline: `simo-dev:tdd` (default for feature/fix) + `simo-dev:debug` on unexpected red.

| Profile | Mode |
|---|---|
| Plan-only / single edit / exploration | **In-session** with worktree discipline |
| Multi-step / verbose / context-heavy | **`spawn_task`**, cwd = worktree (preferred) |
| Independent parallel sub-investigations | **parallel `Agent`** calls, not spawn |

- **In-session worktree**: continue here + `simo-dev:plan-execute`; Bash cwd does NOT follow the worktree — `git -C "<worktree-abs>"` + absolute paths for everything. State the worktree path and this rule. More than a couple of edits → prefer spawn_task (its cwd is set at launch), where `mcp__ccd_session__spawn_task` is available — not available → this row is the fallback, use it regardless of edit count.
- **spawn_task**: if `mcp__ccd_session__spawn_task` is available, invoke it with cwd = worktree. Field detail: `lib/handoff-spawn.md` — load when building the call. After the spawn, tell the user to click the chip; this session can be archived. Not available → fall back to the In-session worktree row above.
- **Parallel agents**: `Agent` tool with `subagent_type`, for research/analysis splits, not code-writing.

## Notes

- This skill never edits source code or commits — editing happens in `simo-dev:plan-execute`, plan-file commits are handled by the caller's gates.
- Step 3 opens with the repo pre-flight, then Explore runs on the current tree BEFORE the worktree exists (intentional: the analysis answers "what changes relative to base"). For an existing feature branch, check it out before invoking.
