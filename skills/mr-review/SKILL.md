---
name: mr-review
description: Use when reviewing someone else's GitLab MR or GitHub PR. Produces a compact, critical-only review with Impact & value summary, end-to-end mermaid, and severity-scored findings (🟢/🟡/🔴). READ-ONLY — never posts to GitLab/GitHub; writes findings to a local markdown file under ~/Documents/cr/ (override via DEV_REVIEW_MR_OUT_DIR). Trigger phrases: "review MR <link>", "review pull request". Optional MR URL/IID arg; falls back to branch-vs-default if on a non-default branch with no arg.
---

# Dev Review MR

Strictly read-only review of someone else's MR. Output is a local markdown file under `~/Documents/cr/` (override via `DEV_REVIEW_MR_OUT_DIR`).

Every Bash block below that runs a plugin script re-binds `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"` as its first line — shell state does not survive between Bash calls.

## Hard constraint — no remote writes

NEVER call: `glab mr note` / `approve` / `comment` / `update` / `merge` / `close`; `gh pr comment` / `review` / `merge` / `close`; any MCP gitlab/github write tool. The ONLY output is the local markdown file. About to run a write command → stop.

## Steps

### 0. mark_chapter

If `mcp__ccd_session__mark_chapter` is available: invoke it as the first action, title `MR review (others)`, summary describing the target. Not available → skip silently, proceed to step 1.

### 1. Detect input mode

- Arg matches an MR URL or numeric IID → **MR mode**.
- No arg, git repo on a non-default branch → **branch-vs-default mode**.
- No arg, on default branch (or not a repo) → ask for an MR URL/IID.

```bash
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
[ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH=master
```

### 2. Fetch diff + metadata

All artifacts in the per-session tmp dir — re-derive in every Bash block, `review_`-prefixed names (no collision with a dev-commit flow in the same dir). The re-derivation is not optional for the inline commands in the bullets below either: each runs as its own Bash call, so it needs the same line prefixed, and the derivation reads the **ambient cwd** — it must stay the repo under review across every block, or the key silently changes:

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
mkdir -p "$MRTMP"
```

- **MR mode (GitLab):** `glab mr view "$REF" -F json > "$MRTMP/review_meta.json"` + `glab mr diff "$REF" --raw > "$MRTMP/review_diff.txt"`. Extract: `title`, `description`, `source_branch`, `target_branch`, `diff_refs.head_sha`, `web_url`, `author.username`, `iid`, project slug.
- **MR mode (GitHub):** `gh pr view "$REF" --json title,body,headRefName,baseRefName,headRefOid,url,author > "$MRTMP/review_meta.json"` + `gh pr diff "$REF" > "$MRTMP/review_diff.txt"`.
- **Branch-vs-default:** `git fetch origin "$DEFAULT_BRANCH"`; then `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; python3 "$R/lib/check_local_base_ahead.py" --repo "$(git rev-parse --show-toplevel)" --base "$DEFAULT_BRANCH"; BASE_AHEAD_RC=$?; [ $BASE_AHEAD_RC -eq 0 ] || exit 1` — this bullet's spans are separate Bash calls (per this step's own preamble), so the `exit 1` stops only that call; the executing agent still must honor the result and not run the `git diff`/`printf` spans on non-zero, same as it already must for every other STOP in this skill (print the script's stdout/stderr; no bypass — ask before pushing the local base, never push it unasked; do not continue to `review_diff.txt` or step 4); rc 0 → `git diff "origin/$DEFAULT_BRANCH...HEAD" > "$MRTMP/review_diff.txt"`; build minimal meta locally: `printf '{"source_branch":"%s","target_branch":"%s","head_sha":"%s"}\n' "$(git rev-parse --abbrev-ref HEAD)" "$DEFAULT_BRANCH" "$(git rev-parse HEAD)" > "$MRTMP/review_meta.json"`.

MR mode (the two bullets above) is not subject to this guard — it diffs an externally-fetched MR/PR by its own remote head SHA, never local branch state; only branch-vs-default mode touches the local base ref `check_local_base_ahead.py` checks (same script as `mr-followup`'s step 6 guard).

### 3. Compute output path

- `id_slug` = `mr-<iid>`; else ticket key from branch (`R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; python3 "$R/lib/derive_ticket_key.py" "<branch>" --cwd "$(git rev-parse --show-toplevel)"` — spelled out because an unbound `R` exits rc 2 with empty stdout, which falls through to the branch slug instead of surfacing); else `branch-<sanitized-branch>`.
- `topic_slug` = sanitized MR title (or first commit subject): lowercase, `[^a-z0-9._-]`→`-`, collapse, trim 50 chars, aim 2-5 words.
- Filename `<YYYY-MM-DD>-<id_slug>-<topic_slug>.md` (date from session `currentDate`, not shell). Path: `${DEV_REVIEW_MR_OUT_DIR:-~/Documents/cr}/<filename>`; auto-create dir; on collision append `-2`, `-3`, ….

### 4. Generate presentation

Before fetching, make sure the ticket's prefix has a `scope_fetch_cmd` configured — `build_review_ctx.py` below silently returns an empty `jira_scope` otherwise:

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
SOURCE_BRANCH=$(python3 "$R/lib/json_field.py" "$MRTMP/review_meta.json" source_branch headRefName "")
KEY=$(python3 "$R/lib/derive_ticket_key.py" "$SOURCE_BRANCH" --cwd "$(git rev-parse --show-toplevel)")
if [ -n "$KEY" ]; then
  PREFIX="${KEY%%-*}"
  CMD=$(python3 "$R/lib/tracker_config.py" get "$PREFIX" scope_fetch_cmd)
  [ -z "$CMD" ] && echo "ASK_SCOPE_FETCH_CMD prefix=$PREFIX key=$KEY"
fi
```

An `ASK_SCOPE_FETCH_CMD prefix=<P> key=<K>` line means no `scope_fetch_cmd` is configured for that prefix yet. Ask the user which command fetches a plain-text ticket scope for that prefix — contract: `<CMD> <key> <comments>`, prints plain text to stdout — offering an explicit "skip" choice. On a concrete answer: `python3 "$R/lib/tracker_config.py" set "$PREFIX" scope_fetch_cmd "<answer>"`. On "skip": `python3 "$R/lib/tracker_config.py" set "$PREFIX" scope_fetch_cmd -` (the sentinel `jira_scope.py` treats as "declined, no error, no re-ask"). No `ASK_SCOPE_FETCH_CMD` line (no key, or `CMD` already set) → proceed straight to the block below with no question.

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/build_review_ctx.py" "$MRTMP/review_meta.json" --repo "$(git rev-parse --show-toplevel)" > "$MRTMP/review_ctx.json"
python3 "$R/lib/mr_presentation.py" --mode review \
  --diff-file "$MRTMP/review_diff.txt" --context-file "$MRTMP/review_ctx.json" > "$MRTMP/review_request.json"
```

Never capture this output into a shell variable (`VAR=$(...)`) to relay it onward — always redirect the command's stdout straight to a file (incident 2026-07-27: zsh's builtin `echo` reinterprets backslash escapes by default, and a literal `\c` anywhere in the JSON — common in file paths, regex, or diffed code — makes `echo "$VAR" > file` silently stop writing at that byte, truncating a large payload down to a few hundred bytes; a direct redirect never goes through `echo` and isn't affected).

Read `$MRTMP/review_request.json` directly (Read tool) to get `prompt`, `schema`, `agent_type`, `model`; dispatch the Agent tool, which should call `StructuredOutput` and return the validated object (may include `ticket_drift` + `drift_note`) — write it to `$MRTMP/review_pres.json`. If `StructuredOutput` wasn't available and the agent returned its answer as plain text instead, extract the outermost `{...}` JSON object from that text (strip markdown code fences if present) and write *that* to `$MRTMP/review_pres.json` — a well-formed object here is not itself a failure. Then run:

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/mr_presentation.py" --normalize-presentation "$MRTMP/review_pres.json"
```

(rc 0: HTML-unescapes any entities the agent introduced — e.g. `--&gt;` / `&lt;br/&gt;` from the plain-text path above — and confirms the required keys are present. Unconditional: it runs on the `StructuredOutput` path too, so a no-op only when the input carries no entities. In the `mermaid` field a quote entity inside a still-quoted label becomes `#quot;` rather than a bare `"`, which would nest quotes inside the label and stop mermaid parsing; escaped label *delimiters* keep being restored to bare `"`.) Then assemble:

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
python3 "$R/lib/mr_presentation.py" --assemble --mode review \
  --presentation-json "$MRTMP/review_pres.json" --context-file "$MRTMP/review_ctx.json" > "$MRTMP/review_body.md"
```

Same rule as step 4's request file: redirect straight to a file, never through a shell variable.

### 4b. Ticket drift escalation

If `ticket_drift` is `true` (from `review_pres.json`) AND `jira_key` non-empty (from `review_ctx.json`) → fetch the full ticket; else skip:

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
rm -f "$MRTMP/review_jira_full.txt"
JIRA_KEY=$(python3 "$R/lib/json_field.py" "$MRTMP/review_ctx.json" jira_key "")
DRIFT=$(python3 "$R/lib/json_field.py" "$MRTMP/review_pres.json" ticket_drift false)
if [ "$DRIFT" = "true" ] && [ -n "$JIRA_KEY" ]; then
  PREFIX="${JIRA_KEY%%-*}"
  CMD=$(python3 "$R/lib/tracker_config.py" get "$PREFIX" scope_fetch_cmd)
  [ -z "$CMD" ] && echo "ASK_SCOPE_FETCH_CMD prefix=$PREFIX key=$JIRA_KEY"
fi
```

An `ASK_SCOPE_FETCH_CMD` line here means the same ask-and-remember as step 4, for a prefix that turned out unconfigured only now (step 4 already asked for most cases — this only fires when step 4 had no key, or the user chose "skip" there and drift escalation later needs a real answer). `-z "$CMD"` alone does not re-ask a prior "skip": the sentinel `-` is a non-empty string, so a previously declined prefix is correctly left alone. **The fetch below runs as a separate block, after any question above is answered** — folding it into the same block as the check would run the fetch immediately with `CMD` still empty on a first-time ask, before the answer is saved, silently losing the drift section for that run:

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
JIRA_KEY=$(python3 "$R/lib/json_field.py" "$MRTMP/review_ctx.json" jira_key "")
DRIFT=$(python3 "$R/lib/json_field.py" "$MRTMP/review_pres.json" ticket_drift false)
if [ "$DRIFT" = "true" ] && [ -n "$JIRA_KEY" ]; then
  python3 "$R/lib/jira_scope.py" --mode full --key "$JIRA_KEY" \
    --repo-root "$(git rev-parse --show-toplevel)" > "$MRTMP/review_jira_full.txt"
fi
```

`[ -s "$MRTMP/review_jira_full.txt" ]` → used twice: "⚠ Ticket drift" section in the md (step 8) + guarded context for step 5. Absent/empty → behave as no-drift.

### 5. Find critical-only findings

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
CODE_REVIEW_MODEL=$(python3 "$R/lib/model_strategy.py" get code_review)
```

Dispatch an Agent, `subagent_type=simo-dev:code-reviewer` (override via `DEV_REVIEW_MR_AGENT_TYPE`), also passing `model: $CODE_REVIEW_MODEL` whenever that variable is non-empty (omitted when empty, same inherit-the-caller's-tier behavior as today). Scope: security / data-loss / breaking bugs / severe invariant violations ONLY — no style, nits, perf hints, praise. Schema: `~/.claude/local-marketplace/simo-dev/lib/schemas/findings.json`. Mitigated/blocker findings carry a `per_finding_mermaid` of the specific failing code path. This dispatch deliberately does **not** carry the `REPO:` marker line that `simo-dev:code-review-protocol` requires: reviewing someone else's merge request must not register as coverage for your own uncommitted work in the session's repo. Do not "fix" the missing marker.

> Full prompt skeleton (mermaid rules, diagram-type choice, drift block): `lib/findings-prompt.md` — load when dispatching this agent.

Findings return as `{findings: [{severity, file, line_range, title, description, category?, per_finding_diagram_type?, per_finding_mermaid?}, ...]}`.

### 6. Verify findings on disk

Per finding: Read the cited `file:line_range`; decide if the issue still exists. **verified** → keep; **partially-mitigated** → downgrade one level (blocker → mitigated, mitigated → resolved); **already-fixed** → drop. Append a `verification` field for the md.

### 7. Score with semaphores

`blocker=🔴`, `mitigated=🟡`, `resolved=🟢`. **Merge verdict** = worst color: any 🔴 → 🔴 blocker; else any 🟡 → 🟡 follow-ups noted; else 🟢 clean.

### 8. Write md file + report

Frontmatter: `mr_iid`, `mr_url` (empty in branch-vs-default), `jira_key` (empty if none), `source_branch`, `target_branch`, `commit_sha`, `reviewed_at` (currentDate), `reviewer` (`R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; glab api user | python3 "$R/lib/json_field.py" - username`). Body: `$MRTMP/review_body.md` content (Read tool, never re-echoed through a shell variable), then the drift section when `review_jira_full.txt` is non-empty, then one block per finding, then the commit/verdict footer.

> Detail: `lib/output-templates.md` — load when writing the review md file (drift block, finding block format, Configuration).

Lint each non-resolved finding's mermaid before writing:

```bash
MRTMP="/tmp/claude_mr_$(basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")_$(git branch --show-current | tr '/' '-')"
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"
printf '%s' "$PER_FINDING_MERMAID" > "$MRTMP/finding_mmd.txt"
python3 "$R/lib/mr_presentation.py" --lint-mermaid \
  --mermaid-file "$MRTMP/finding_mmd.txt" --diagram-type "$PER_FINDING_DIAGRAM_TYPE"
```

Any output lines → append under the finding as `> ⚠ mermaid: <problem>` (warning-only, diagram still ships). 🟢 findings: skip the diagram. No findings at all → `No critical issues on commit <head_sha>.` + `**Merge verdict:** 🟢 clean`. Write to the step-3 path.

**Diagram preview widget (in chat, informational).** If `mcp__visualize__show_widget` is available: `R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; MERMAID=$(python3 "$R/lib/json_field.py" "$MRTMP/review_pres.json" mermaid)`, read `~/.claude/local-marketplace/simo-dev/lib/preview_widget.html`, substitute the literal `__MERMAID__`, call `show_widget` (title `review_diagram_preview`). **Never call the visualize `read_me` tool** — see `simo-dev:commit` step "Mermaid preview (informational, no gate)". Same shared asset as a sibling private team-plugin skill's MR-description generator — keep byte-identical. Not available, or `show_widget` errors → skip (the md's fence renders it).

Chat output: ONLY a one-liner, e.g. `3 findings — 🔴 blocker on #2 — ~/Documents/cr/2026-06-03-mr-932-topn-filter.md`. Never print the full review body to chat.
