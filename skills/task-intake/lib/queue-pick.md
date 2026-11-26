# No-argument queue check

Loaded on demand from `task-intake` `SKILL.md` step 1, only when `ARGUMENTS` is empty. "The regex check below" means step 1's ticket/prompt mode check in `SKILL.md`.

Locate the repo root and check for `.claude/tracker-queue-cmd`:

```bash
TOP=$(git rev-parse --show-toplevel 2>/dev/null) && [ -f "$TOP/.claude/tracker-queue-cmd" ] && cat "$TOP/.claude/tracker-queue-cmd"
```

(This is a minimal, read-only check — step 3's own repo pre-flight runs later.) Empty output or non-zero exit (not a git repo, or no `.claude/tracker-queue-cmd`) → fall through to the regex check below, same as `ARGUMENTS` empty today.

Otherwise, run the one-line command this printed and capture its stdout. Split into lines; a line without a tab is malformed and is dropped. Empty result, non-zero exit, or every line malformed → same fallback as above (state in one line that the queue command returned no candidates before falling back).

`.claude/tracker-queue-cmd` is a normal committed file, so this command runs unconditionally, with no confirmation before execution — the `y`/`skip`/`manual` gate below only confirms the resulting *candidate*, never the command itself. Same trust model this plugin already applies to `tracker_config.py`'s `transition_cmd`/`create_skill`/`link_cmd` (also configured commands run without a per-invocation confirmation): a repo checkout is already a trust boundary, and a malicious edit to this file is no different from one to any other committed script in the tree.

Otherwise, gate on the first remaining candidate `<key-or-path>` / `<title>`:

> "No argument given — queue candidate: `<key-or-path>` — <title>. [y / skip / manual]"

- `y` → if `<key-or-path>` matches `^[A-Z][A-Z0-9]+-[0-9]+$`, treat it exactly as if it had been passed as `ARGUMENTS` (continue at the regex check below, which now classifies it as ticket mode). Otherwise (a `.scratch/...` path, always relative to the repo root per `local_queue_fetch.py`'s own contract) skip the regex check entirely and continue directly in prompt mode: read the file at `$TOP/<key-or-path>`, substituting the `$TOP` value this same block already computed as a literal — never a bare relative path, and never re-deriving it from the ambient shell/tool cwd (this repo's own CLAUDE.md "Worktree cwd gotcha" documents why that is unreliable). Use that file's content as the task description, per `docs/agents/issue-tracker.md`'s "fetch the relevant ticket" convention.
- `skip` → drop the first candidate and re-show this same gate on the next remaining line, with no re-invocation of the command. Once the list is exhausted, apply the same fallback as the empty-result case above.
- `manual` → fall through to the regex check below exactly as if `.claude/tracker-queue-cmd` had been absent.
