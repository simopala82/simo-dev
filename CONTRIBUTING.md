# Contributing to simo-dev

## Proposing changes to a skill

Changes are proposed as a branch + merge request against this GitLab repository. A
standalone mirror also exists at `github.com/simopala82/simo-dev` (currently private),
but that mirror is a one-way, regenerated export — its history is rebuilt from scratch
on every publish, so it never accepts contributions directly. This GitLab monorepo stays
the only place to propose changes. Keep one skill or concern per MR.

Skill sources live under `skills/<name>/SKILL.md`. Note: those files are currently
being refactored on another branch, so a PR touching `skills/**/SKILL.md` may need a
rebase before it can merge cleanly.

## Style conventions

- Skill body and instruction text is always written in English.
- Keep skill bodies terse: state the rule, list the cases, skip rationale prose.
  Cross-reference other docs instead of restating them.
- Add a comment only when the *why* is non-obvious (a hidden constraint, a
  workaround, a surprising behavior) — never to restate what the code already
  says.
- A rule that *forbids* is cheap to write and to follow; a rule that
  *classifies* inputs into categories is not. Before writing a classifying
  rule, check whether forbidding something gets the same outcome more
  cheaply. If it must classify, give it an escape hatch that fires on
  evidence.
- Never write `python3 -c '...'` / `-e '...'` inside a SKILL.md bash fence: a
  skill body is executed as literal Bash tool calls, so a personal
  `inline-script-guard` PreToolUse hook some users run (denying inline
  `-c`/`-e` scripts, per this repo's own CLAUDE.md) blocks it at execution
  time, not just at authoring time. Call a named `.py` file under `lib/` (or
  a skill-local `lib/`) instead, even for a genuinely single-use extraction.

## Testing locally

Run from `.claude/local-marketplace/simo-dev/` (this plugin is stdlib-only — no
`requirements.txt` or `pyproject.toml` to install first):

```bash
python3 -m pytest lib/tests/
python3 -m pytest hooks/tests/
```

`hooks/tests/` is a pytest suite for hooks that have been ported from bash to
Python (a Windows-compatibility effort). Every `hooks/*.sh` hook has been ported
this way — there are no bash hook self-tests left to run separately, and the
`hooks/tests/` run above already covers them.

## Writing hooks

- Hooks read the tool payload's `session_id` field to key state per-session.
- `PostToolUse` on the `Skill` tool fires when the skill-tool call *returns* —
  for a skill that dispatches a background agent, that's a start signal, not
  a completion signal.
- A `settings.json` `if`-condition on a Bash command fails **open** (lets the
  hook run) on any command it can't parse as "simple" — see
  `hooks/mr_followup_remind.py`'s module docstring for the incident this
  caused (it fired after every `ls`/`rg`/`git rev-parse` before the fix).
  Hooks in this repo self-gate by parsing `tool_input.command` directly in
  their own Python code instead of relying on `settings.json`'s `if` for
  filtering.

## Continuous integration

There is no CI configured for this plugin yet. It still lives as a subtree of a
private company GitLab repository, and a GitLab pipeline there would need to be
scoped to the whole monorepo rather than just this plugin. A standalone mirror
exists at `github.com/simopala82/simo-dev`, but it is currently private and
regenerated wholesale on every publish (see "Proposing changes" above) rather than
a live target for CI. This will be revisited once that mirror is public.

## Code of Conduct

There is no `CODE_OF_CONDUCT.md` yet. This is a single-maintainer project with
no active external contributors today. This will be revisited if that changes.

## License

Contributions are accepted under this project's MIT license (see
[`LICENSE`](LICENSE)).
