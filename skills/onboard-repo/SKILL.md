---
name: onboard-repo
description: Use when the user wants to set up or improve Claude Code / simo-dev conventions for a repo — the CLAUDE.md "Agent skills" section, the issue-tracker choice (local markdown vs external tracker vs none), and domain docs (CONTEXT.md / docs/adr). Triggers: "onboard this repo", "configure claude for this repo". Standalone utility, exempt from `task-intake`'s mandatory entry — same class as `spawn-task`: low blast radius, easily reversible doc/config files.
---

# Onboard Repo

Detects what of simo-dev's per-repo conventions a target repo already has,
lets the user pick what to scaffold or improve, writes the chosen files.
Does not write a generic `CLAUDE.md` from scratch (that's `/init`'s job) and
never touches `~/.claude/settings.json` or any plugin-enablement state —
`simo-dev` is enabled per user/machine, not per repo, so there is nothing
repo-level to "install".

## When to invoke

- User asks to onboard / set up / configure Claude Code or simo-dev
  conventions for a repo.
- Not gated by `task-intake`'s mandatory entry — same exemption class as
  `spawn-task` (low blast radius, easily reversible doc/config files).

## How

1. **Resolve the target repo root.** Default: current repo via
   `git rev-parse --show-toplevel`. If the invoking cwd is not a git repo,
   or the toplevel resolves under `.claude/plugins/cache/` or
   `.claude/plugins/marketplaces/`, refuse and say why — same hazard
   `task-intake` step 3 guards against (editing a copy instead of the
   source), stated here inline rather than invoking that flow.

2. **Detect state.**

   ```bash
   R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
   python3 "$R/skills/onboard-repo/lib/detect_state.py" "<repo-root>"
   ```

   Parse the JSON: `claude_md.exists`, `claude_md.path`,
   `agent_skills_section_present`, `docs_agents.issue_tracker`,
   `docs_agents.triage_labels`, `docs_agents.domain`, `no_tracker_marker`,
   `context_md`, `context_map_md`, `docs_adr`.

3. **Build the menu**, omitting any item detection already shows fully
   satisfied:
   - **CLAUDE.md**: `claude_md.exists == false` → *Create minimal (Agent
     skills section only)* / *Skip (I'll run `/init` first)*.
     `claude_md.exists == true` and `agent_skills_section_present ==
     false` → *Add Agent skills section* / *Skip*.
     `agent_skills_section_present == true` → *Review section for
     improvements* / *Skip*.
   - **Issue tracker**: unless `no_tracker_marker == true`, **or**
     `DEV_FLOW_NO_TRACKER=1` is set in the environment (`detect_state.py`
     only checks the filesystem marker — the env var is this skill's own
     responsibility to check, same convention every other flow skill
     follows), **or** (`docs_agents.issue_tracker == true` **and**
     `docs_agents.triage_labels == true`) (item already satisfied, omit
     it) → *Local markdown* / *External tracker* / *No tracker* / *Skip*.
     `no_tracker_marker` alone is not enough: it means "no *external*
     tracker" in `task-intake`'s own sense, and a repo can carry that
     marker while still running the local-markdown tracker this skill
     scaffolds (`docs_agents.issue_tracker` true at the same time) — a
     repo like this one (`myrepo`) is exactly that shape. Checking either flag
     covers both real "already resolved" states without conflating them.
     Both `docs_agents` fields, not just one: the "Local markdown"
     execution step (step 5 below) always writes `issue-tracker.md` and
     `triage-labels.md` together, so a repo with only one of the two
     (hand-written before adopting this skill, or an interrupted earlier
     run) is genuinely incomplete and should still surface the question,
     not be treated as already resolved.
   - **Domain docs**: unless (`context_md` **or** `context_map_md`) and
     `docs_agents.domain` are already `true` → *Yes, single-context* /
     *Yes, multi-context* / *No*. Not an AND of all three: execution
     creates only `CONTEXT.md` or only `CONTEXT-MAP.md`, never both, so a
     fully-onboarded repo never has all three at once.
   - If every item is already satisfied, skip the question entirely and
     report "nothing to do" — never call `AskUserQuestion` with no real
     options.

4. **Ask** the surviving (non-omitted) questions in one `AskUserQuestion`
   call, one question per menu item still open.

5. **Execute each answer:**
   - *CLAUDE.md — create minimal*: write a new `CLAUDE.md` containing
     only an `## Agent skills` section, one line per item scaffolded in
     this same run, pointing at the files just created.
   - *CLAUDE.md — add section / review for improvements*: read the
     existing file, build a diff (append the new section, or close gaps
     against what detection found on disk — e.g. point at
     `docs/agents/domain.md` if it now exists but the section doesn't
     mention it yet). Show the diff, get an explicit yes before writing.
     A decline leaves the file untouched; move on to the next item.
   - *Issue tracker — Local markdown*: for each of
     `lib/templates/issue-tracker.md` and `lib/templates/triage-labels.md`,
     if the destination under `docs/agents/` in the target repo already
     exists (this path is reachable with exactly one of the two present —
     see the omission note above), treat it like the `CLAUDE.md` path:
     show a diff against the template, get an explicit yes before
     overwriting — `triage-labels.md` in particular is meant to be
     hand-edited per repo ("Edit the right-hand column…"), so an existing
     copy is very likely customized. Only a missing destination is copied
     without asking. Create `docs/agents/` if missing; create `.scratch/`
     if missing.
   - *Issue tracker — External tracker*: ask for the ticket-key prefix,
     then for each of `fetch_skill` / `create_skill` / `transition_cmd`
     ask what handles it — same fallback shape as `task-intake` step 2:
     offer to remember via
     `python3 "$R/lib/tracker_config.py" set <PREFIX> <field> <value>`
     (same `$R` fallback form as step 2 above).
   - *Issue tracker — No tracker*: create `.claude/` if missing, write an
     empty `.claude/no-tracker` file.
   - *Domain docs — Yes*: this item can surface with `docs_agents.domain`
     already `true` (e.g. `context_md`/`context_map_md` still missing) —
     if `docs/agents/domain.md` already exists in the target repo, treat
     it like the `CLAUDE.md` path: show a diff before overwriting, get an
     explicit yes. Otherwise copy `lib/templates/domain.md` to
     `docs/agents/domain.md` directly, then in the copy keep only the
     "File structure" block matching the single- vs multi-context answer
     (the template carries both, delimited by
     `<!-- single-context --> … <!-- /single-context -->` and
     `<!-- multi-context --> … <!-- /multi-context -->`) and delete the
     other block, then remove every remaining HTML comment — the
     leading instructional one AND the surviving block's own opening
     and closing delimiter tags — so the shipped file has no HTML
     comments at all. Create `CONTEXT.md` (single-context answer) or
     `CONTEXT-MAP.md`
     (multi-context answer) if missing; create `docs/adr/` if missing.

6. **Summarize** every file created or modified, one line each.

7. **Confirm and commit.** If nothing was written, stop here — no commit
   prompt. Otherwise ask for a single yes/no confirm, then:
   - If the target repo's current branch is `master`/`main`/`develop`,
     apply the always-on protected-branch confirm before committing
     directly (offer a feature branch as an alternative).
   - `git add` exactly the files this run touched, then
     `git commit -m "chore: onboard repo for simo-dev conventions"` — a
     plain commit, no push, no MR. This skill never invokes
     `simo-dev:commit`'s cascade.

## Common shapes

- **Brand-new repo, nothing configured** — all three menu items offer
  their full "create" option.
- **Repo with `CLAUDE.md` but no Agent skills section** — only that item
  offers *Add Agent skills section*; the other two are asked normally.
- **Repo with an external tracker partially set** (some
  `tracker_config.json` fields already present for its prefix, some
  missing) — the tracker question still surfaces; only the missing
  fields get asked for, the rest confirmed as already set.

## Anti-patterns

- Silently overwriting an existing `CLAUDE.md` — always propose a diff
  and get an explicit yes first.
- Touching `~/.claude/settings.json` or any plugin-enablement state —
  out of scope, `simo-dev` is enabled per user/machine already.
- Writing a full generic `CLAUDE.md` from scratch (build/test commands,
  architecture, style) — point at `/init` instead.
- Routing this through `task-intake`'s full flow (worktree, plan,
  review-loop, MR) — this skill is exempt, see frontmatter.
