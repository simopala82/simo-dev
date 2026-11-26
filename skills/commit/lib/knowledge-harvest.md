# Knowledge-harvest procedure

Shared three-question routing procedure for the knowledge harvest. Loaded from `commit` step 0.7
(full pass, every commit) and `mr-followup` step 7's clean-exit branch (catch-up pass, scoped to
what emerged since). Both call sites must actually load this file — never paraphrase it from
memory.

## The three questions

- **(a) A domain term surfaced** → glossary entry in `CONTEXT.md` (repo root, or the context dir
  if a `CONTEXT-MAP.md` exists — same rule `skills/grill-me/SKILL.md`'s "Domain capture (inline)"
  section already uses). Glossary only, zero implementation detail.
- **(b) A decision was made** → apply `skills/grill-me/SKILL.md`'s "Offer an ADR sparingly"
  three-condition test by cross-reference — do not re-copy the three conditions (the old
  `post-merge.md` paraphrased them; that duplication is the wart this file fixes). All three
  hold → `docs/adr/NNNN-<slug>.md`. Any missing → skip.
- **(c) A gotcha, dead end, or invariant discovered** → the repo's `CLAUDE.md`, or an existing
  `~/.claude/reference/<domain>.md`. First: never put in `CLAUDE.md` (or a file it `@`-imports)
  what a hook or script already enforces — point at that file instead of restating it. Then the
  norm goes in `CLAUDE.md`, the proof (dated incidents, measurements) goes to the reference file,
  cited as file + identifying string, never `file:line`.

## Home-selection gate

Uniform across (a)/(b)/(c). Evidence-gated: fires only when the natural home for that question
doesn't exist yet in this repo.

- `CONTEXT.md` missing → ask once "Create `CONTEXT.md`?" before writing the first entry.
  Approved → create; every later domain term in this or a future session writes straight in, no
  gate.
- `docs/adr/` missing → same shape: ask once before the first ADR in this repo. Approved → create
  the directory + `0001-<slug>.md`; every later ADR writes straight in.
- (c): neither `CLAUDE.md` nor an existing reference file fits → ask the user for a home,
  presenting "create a new repo-specific file (e.g. a `docs/gotchas.md`-style catch-all)" as one
  valid answer, alongside picking an existing reference file or `CLAUDE.md`. Never invent one
  silently — the prohibition is "never invented silently", not "never created".

Same policy `skills/grill-me/SKILL.md`'s live capture uses (its "Domain capture (inline)"
bullets) — cross-reference so the two never drift apart again.

## Routing when the right home isn't in the current repo

Right home in the current repo → minimal direct edit now. Home elsewhere (e.g. a personal
`~/.claude/reference/*.md` while working in another repo) → when `mcp__ccd_session__start_session`
is not yet callable, run `ToolSearch` `select:mcp__ccd_session__start_session` once first (never
skipped because the name is unlisted); callable after that → `own_initiative` cannot set `cwd`, so say it in one line to the user
(the fact and its target file); on their yes start it with `initiation` `user_asked`, `cwd` = the
target repo root, `use_worktree` `true`, a `prompt` naming the fact and target file plus the `parent task:` line
(`docs/canonical-flow.md` "Parent task"), a `background` of ≥80 chars. No schema, or no `ToolSearch` (not available) → surface
the fact to the user directly instead (no automated path to a home outside the current repo
without it).

## Session-review framing

Caller reviews the session with the three questions above; act only on hits, no hits → done,
nothing to announce. A caller re-running this file later in the same session (the `mr-followup`
catch-up pass) skips whatever the earlier pass already captured — same "skip already captured
this session" phrasing style as `skills/commit/SKILL.md`'s existing grill-me-term dedup, pure
session-memory, no new state file.

## Guardrails

Everything harvested loads into context every future session; bloat is poison.
- Durable, non-obvious, **verified** facts only — no speculation, no session-specific noise,
  nothing already documented.
- One tight line per fact.
