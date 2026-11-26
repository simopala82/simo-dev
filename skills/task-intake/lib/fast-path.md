# Fast path — decided brief

Loaded by `SKILL.md` step 1 when the intake carries a `direction: decided` block. The session that wrote the brief already made the direction, scope and file decisions, so this path checks the brief against the code instead of re-deriving it. Each section below names the step it replaces; any step not named runs as written.

## Unchanged

Steps 1, 2, 2.5, step 3's repo pre-flight (and the shared-tree stop), 5, 5.6, 6, 8, and step 7.5's pre-gate eligibility check: an existing remote branch stays a follow-up there, never a brief contradiction. Downstream, review-loop on the diff, the tests and the merge gate do not change.

Step 3's tracked-path paragraph ("Also, if the task involves an existing path") is replaced by the brief check below: an untracked path means fallback, not a confirmation.

## Brief check (replaces step 3's reference and glossary checks and the Explore dispatch)

Inline, no subagents, against the tree step 3's pre-flight resolved (`git -C "<TOP>"`, absolute paths):

- every path the brief presents as existing (to modify or read) is tracked: `git -C "<TOP>" ls-files --error-unmatch <path>`;
- every path the brief says to create does not exist yet;
- a base branch the brief names resolves: `git -C "<TOP>" fetch origin <base> --quiet`, then `git -C "<TOP>" rev-parse --verify --quiet refs/remotes/origin/<base>`; none named → nothing to check. A failed fetch is not a contradiction: warn in one line and skip this item, no fallback;
- each decision whose claim can be checked against the code (a function, a flag, a file section, a field name) holds. A decision about intent or scope has nothing to check.

Print the result in at most 5 lines. Wherever a later step uses step 3's gist (step 5's ticket title, the 7.5 gist line, the branch slug, step 4.5's draft), take the brief's title or first sentence.

## Fallback

Any contradiction found by the brief check (a missing file, a false claim, a path to create that already exists) leaves the fast path:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" event --type gate --field skill=simo-dev:task-intake --field gate=intake_path --field answer=fallback
```

Announce `Fast path: fallback — <reason>` and resume the full flow at step 3's tracked-path paragraph ("Also, if the task involves an existing path"), right after the pre-flight: an untracked path then gets its warning and the `lib/repo-preflight.md` recovery, and the `EXPLORE_MODEL` lookup runs before Explore. Never fall back silently, and never on taste: only a contradiction found in the code counts. A brief that is merely thin is still the starting session's call.

## Step 4 replaced

No direction gate. Print the brief's decisions as `Choices made (from brief):`, one line each, then record:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" event --type gate --field skill=simo-dev:task-intake --field gate=direction --field answer=brief
```

Steps 4.5 and 5.5 take that list as the approved direction, with no candidate list.

## Step 7 replaced

No `simo-dev:plan-write`. Write the plan directly, same path convention as step 7 (`docs/plans/YYYY-MM-DD-<key>-<desc>.md`):

- a `**Goal:**` header line;
- one `### Task N:` section per independently verifiable change, each with a `**Files:**` block in the `- Modify: \`path\`` / `- Create: \`path\`` bullet form, literal repo-relative paths (`check_base_freshness.py` parses exactly that form);
- the steps and the verification command of each task.

No full code: code steps name the function and the behavior, prose steps an anchor and the outcome (`simo-dev:plan-write` "Prose targets"). The brief's decisions are the plan's constraints; do not re-decide them.

## Step 7.5 changed

The gate is kept, with the summary line `Fast path (decided brief)` in place of `Bare plan`, and no Approach bullets (the plan has no Architecture section).

Plan critique: at most one `plan-reviewer` round, and only when the brief has a `risk:` line, passed to the reviewer with the plan. Use step 7.5's `PLAN_CRITIQUE_MODEL` lookup (`lib/gate-critique.md`) (`none` → no dispatch). Apply its `fix:` findings, do not re-dispatch, and carry its `decide:` findings to the gate. No `risk:` line → no dispatch.

Re-entry after `edit` or `apply and re-present`: targeted `Edit`s to the plan only — never `plan-write`, never a second critique round. Show `apply and re-present` as "apply the same edits, show this gate again" (no re-run of critique).

`restart` leaves the fast path: discard the plan, announce `Fast path: fallback — restart at plan gate`, record `fallback` as in the Fallback section, and resume there.

## What the marker is not

A hand-off child (`lib/handoff-spawn.md`) skips intake entirely and needs no marker. A brief with any open decision carries no marker: it goes through the full flow.
