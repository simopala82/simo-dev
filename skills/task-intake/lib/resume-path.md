# Resume path — work already in progress

Loaded by `SKILL.md` step 1 on every intake that is not on the fast path, right before the No-argument queue check. Work started outside the flow (uncommitted changes, local commits, a plan, an open MR/PR) re-enters it at the step it has reached, instead of going through analysis, direction and plan again. The check proposes; the user confirms.

## Arguments

Step 1 has already removed a `--adopt` token from `ARGUMENTS`. It takes a resume entry without the gate below, but only when the resume default holds.

The **resume default** decides the gate's `Recommended:` letter: `ARGUMENTS` left empty, or a ticket key equal to the branch's own key (`KEY=` below) → resume. Anything else (another key, a prompt) is probably new work → full intake.

## Detect

Run step 3's **Repo pre-flight** now and apply its verdicts (its STOP rows, and the shared-tree stop for a desktop-app tree). Keep its output lines: step 6 below and, on a full intake, step 3 reuse them without a second call. Then, in one Bash call:

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
B=$(git -C "<TOP>" symbolic-ref --short --quiet HEAD)
G="$R/lib/check_mr_state.py"
MR_RC=-1
case "$B" in
  ""|master|main|develop) ;;
  *) if [ ! -f "$G" ]; then MR_RC=2; echo "WARN: MR state not verified — guard missing at $G"
     else
       MR_STATE=$(python3 "$G" "$B" --cwd "<TOP>" 2>/dev/null)
       MR_STATE_RC=$?
       if [ -z "$MR_STATE" ]; then MR_RC=2; echo "WARN: MR state not verified — rc $MR_STATE_RC, no output"
       else MR_RC=$MR_STATE_RC; fi
     fi ;;
esac
echo "KEY=$([ -n "$B" ] && python3 "$R/lib/derive_ticket_key.py" "$B" --cwd "<TOP>")"
python3 "$R/lib/resume_point.py" --repo "<TOP>" --mr-rc "$MR_RC"
echo "resume_point rc=$?"
```

`resume_point.py` (its docstring has the rules) prints one JSON object; `entry` is `intake`, `stop`, `review-loop`, `plan-execute` or `mr-followup`. It compares against the default branch: on a branch stacked on another one, the parent's commits count as this branch's work, and `simo-dev:commit` resolves the real target later.

- rc 2, or no JSON → `Resume check: skipped — <stderr line>`, full intake.
- `intake` → nothing to say: continue step 1 at the No-argument queue check.
- `stop` → `ARGUMENTS` empty: print the `reason` and end the intake. Otherwise one line, `Resume check: <reason>`, and the full intake.
- any other entry → the gate.

## Gate

Print:

- `Resume point: <entry> — <reason>`;
- one facts line: branch (`detached` when empty), base, `<dirty>` uncommitted paths, `<ahead>` commits ahead of `origin/<base>`, `plans` when any, the MR/PR state when checked, one `⚠ <warning>` line each;
- when `ahead` > 0, those commits: `git -C "<TOP>" log --oneline -10 "origin/<base>..HEAD"`. Confirming the gate confirms them as this task's work: it answers step 6's `adopt` `ASK: foreign-commits` (re-run it with `--foreign-ok`).

`plans` sort by their date prefix: the plan is the last one; name the others in the facts line.

Under `--adopt` with a resume default and no MR/PR warning (an unverifiable state shows the gate), print the same lines plus `Resume: --adopt` and act as on `a` without ending the turn, except that on rows 3a and 3b (step 6's table) with `ahead` > 0 the commits are not confirmed: step 6's `adopt` `ASK: foreign-commits` asks, as written. Otherwise one lettered list over the options that apply, in this order:

- `resume at <entry>` (for `plan-execute`, name the plan);
- `plan-execute <plan>` — only on a `review-loop` or `mr-followup` entry with `plans`: the plan is not finished, resume it instead;
- `continue implementing` — only on a `review-loop` entry: the flow goes on in this tree with no plan, and the eager review-loop rule fires when the work is done;
- `full intake` — new work: run the intake from the queue check.

One `Recommended: <letter> — <why>` line from the resume default, then `[y = <letter> / a–<last>]`. Plain markdown, never AskUserQuestion, and END THE TURN. Under `simo-dev:afk` take the `Recommended:` letter.

On `full intake`, continue step 1 at the No-argument queue check. On any other letter, record the path and the step picked (`<answer>` = the entry, `plan-execute` for the `plan-execute <plan>` option, or `continue`):

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
python3 "$R/lib/flow_telemetry.py" event --type gate --field skill=simo-dev:task-intake --field gate=intake_path --field answer=resume
python3 "$R/lib/flow_telemetry.py" event --type gate --field skill=simo-dev:task-intake --field gate=resume_point --field answer=<answer>
```

## Steps on a resume

Skipped: steps 2.5, 3 (beyond the pre-flight above), 4, 4.5, 5.5, 7, 7.5 and 8. Then, in order:

1. **Ticket.** The key is the one in `ARGUMENTS`, else `KEY=` above (empty on a detached HEAD). With a key, fetch it as step 2's ticket mode does, for its title. No key → step 5 as in prompt mode (markers included); a created ticket takes a one-line gist of the work as its title. Then step 5.6 as written: its transitions are forward-only, so a ticket already past `In Progress` stays where it is.
2. **Branch and worktree.** Step 6 with these changes, by its table's row:
   - row 1 (a main checkout) on a named branch → **adopt in place**: `<WT>` = `<TOP>`, `<branch>` = the current branch; no `worktree add`, no rename, no park. A detached HEAD here → ask for the branch name and `git -C "<TOP>" switch -c "<branch>"` (the uncommitted changes follow). Post-merge leaves a main checkout and its branch in place (`simo-dev:mr-followup` `lib/post-merge.md`, Worktree cleanup);
   - row 2 (a canonical worktree) → adopt in place the same way;
   - rows 3a and 3b on a `claude/*` branch or a detached HEAD → as written, with `<branch>` derived now by step 6's naming rule from the diff and the commit subjects; on a named branch that is not `claude/*` → adopt in place, branch kept, no rename, no park and no move (`simo-dev:commit` step 0.5 freshens the base; `hooks/base_freshness_warn.py` stays silent there on an unmoved 3b tree, as on a main checkout);
   - rows 3s and 4 → stop, as written.

   Then step 6's closing block: the `flow_state.py on` line and the `task_bound` event, with `<WT>`. On row 1, drop `--repo` from the `on` line: a marker in the main checkout's git dir would turn the flow on for every session that opens there, and outlive an abandoned task; the session flag alone carries the task. Only when step 1's `start` returned rc 2 (no session id) keep `--repo`, and say in one line that the marker turns the flow on for every session in that checkout until post-merge clears it. Every later command uses `<WT>` as a literal.
3. **Re-enter** at the step picked at the gate:
   - `review-loop` → invoke `simo-dev:review-loop` on `<WT>`: a ship-flow invocation, so a clean run cascades into `simo-dev:commit` → MR/PR → `simo-dev:mr-followup`; on an open MR/PR, commit takes its existing-MR path;
   - `plan-execute` → invoke `simo-dev:plan-execute` on the plan, with step 8's discipline (`simo-dev:tdd`, `simo-dev:debug` on unexpected red);
   - `mr-followup` → invoke `simo-dev:mr-followup`;
   - `continue implementing` → say in one line that the flow is on in `<WT>` and end the intake.
