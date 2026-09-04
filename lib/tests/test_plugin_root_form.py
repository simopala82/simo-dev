"""Guards the plugin-root call-site form across the plugin tree.

Three mechanical assertions, no classification:
  1. the shell-default spelling appears nowhere — the harness substitutes only the
     bare `${CLAUDE_PLUGIN_ROOT}` literal, so a `:-` default is never replaced and
     silently resolves the source tree at every call site;
  2. inside code, any line that USES the placeholder — the brace form, or the bare form with a
     path separator — carries the canonical prelude. A prose mention does not count as a use;
  3. inside code, any region that dereferences `R` binds it BEFORE the first use. Assertions 1
     and 2 look only at lines naming the placeholder, so a block whose sole content is
     `python3 "$R/lib/x.py"` passes both while `R` is unbound — the very defect the prelude
     exists to prevent (constructed and confirmed 2026-08-20). This is the assertion that
     gates it, and it compares indices rather than testing presence, because a binding placed
     below the first use is just as unbound at the point of use.

Assertion 2 tests for the prelude as a substring, not equality, so an indented block and a
comment quoting the form both pass; assertion 3 is what stops that latitude from mattering.

Limits of the region model observed as of 2026-08-22, not a closed set: a fenced block is treated
as shell whatever language tag it carries, and a `.sh`/`.py` file is one region, so a prelude
quoted in a top-of-file comment satisfies a use anywhere below it. That second shape WAS present —
`skills/mr-followup/lib/test_detect_remote.sh` (removed in OSS migration step 7c, superseded by
`lib/tests/test_detect_remote.py`) opened with the prelude inside its `# Run:` recipe — so only
the exploiting use was missing there, not the blind spot. No file currently reproduces this shape;
the blind spot itself is untouched.
Assertion 3 compares columns as well as lines, so the one-liner shape this change made canonical
is ordered too; assertion 2 strips the prelude rather than skipping its line, so a second wrong
spelling beside a correct prelude is still caught. Both were constructed and confirmed to fail.

Prose is out of scope by construction: the fence is the boundary, not a judgment. Inline
prose sites carry a per-file convention sentence instead. Four sites observed spelling the prelude
out at the site rather than relying on that sentence: the two `derive_ticket_key.py` calls and
`skills/afk/SKILL.md`'s `afk_state.py stop`, whose rc the caller absorbs (rc 2 with empty stdout,
read as "no JIRA" or as "no tombstone written"), and `skills/afk/lib/wake-park.md`, which has one
site and folds the binding into its sentence rather than adding a convention line and a fence.

The prelude is NOT `set -u` safe: under `set -u` the bare token aborts the shell before the
`[ -d ]` fallback runs (measured 2026-08-20, bash and zsh, rc 1). It must therefore precede
any `set -u`, including one reached by sourcing another script. The safe spelling
`${CLAUDE_PLUGIN_ROOT-}` is not an option: the harness pattern requires the closing brace
immediately after the name, so that form would never be substituted and the pin would be
lost again — which is why this file pins the exact literal.

This file excludes itself — a guard that guarded itself could not name what it forbids.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SELF = Path(__file__).resolve()

TOKEN = "CLAUDE_PLUGIN_ROOT"
FALLBACK_FORM = TOKEN + ":-"
PRELUDE = (
    'R="${' + TOKEN + '}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"'
)

FENCE = re.compile(r"^\s*```(\w*)")
SCANNED_SUFFIXES = {".md", ".sh", ".py"}
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}


def _scanned_files():
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if path.resolve() == SELF:
            continue
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if SKIPPED_DIRS & set(path.parts):
            continue
        yield path


def _code_regions(path):
    """Yield (start_lineno, [lines]) per code region: the whole file for .sh/.py, each fenced block for .md.

    A region is the unit a shell actually runs, which is why assertion 3 groups by it: `R`
    bound in one fenced block is gone by the next Bash call.
    """
    inside = path.suffix in {".sh", ".py"}
    start, buf = 1, []
    for num, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if path.suffix == ".md" and FENCE.match(line):
            if inside and buf:
                yield start, buf
            inside, start, buf = not inside, num + 1, []
            continue
        if inside:
            buf.append(line)
    if inside and buf:
        yield start, buf


def _code_lines(path):
    """Yield (lineno, line) for every code line, flattening the regions above."""
    for start, lines in _code_regions(path):
        for offset, line in enumerate(lines):
            yield start + offset, line


def test_shell_default_form_is_absent():
    offenders = [
        f"{path.relative_to(PLUGIN_ROOT)}:{num}"
        for path in _scanned_files()
        for num, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
        if FALLBACK_FORM in line
    ]
    assert not offenders, (
        f"{len(offenders)} site(s) use the shell-default form, which the harness never "
        f"substitutes — it resolves the source tree every time:\n  "
        + "\n  ".join(offenders)
    )


# A USE of the placeholder, not a mention of it. `${…}` is the only spelling the harness
# substitutes, so any other dereference is a call site that would silently resolve nothing.
# The backtick lookbehind is what separates the two: house style writes a prose mention as
# `$CLAUDE_PLUGIN_ROOT` in backticks (lib/session_tokens.py's module docstring is the live
# example), while code never does. An earlier form of this pattern required a `/` right after
# the name and let three real spellings through — `"$TOKEN"/lib/x.py`, `'$TOKEN'/lib/x.sh` and
# `cd $TOKEN && …` all passed all three assertions (measured 2026-08-20). A prose mention that
# forgets the backticks is flagged rather than missed, which is the safe direction. The third
# alternative exists because code CAN legitimately put a backtick immediately before the name —
# a legacy `` `$TOKEN/lib/x.sh` `` command substitution — which the lookbehind alone would drop;
# requiring the path separator there keeps the prose form excluded.
USES_PLACEHOLDER = re.compile(
    r"\$\{" + TOKEN + r"\b|(?<!`)\$" + TOKEN + r"\b|`\$" + TOKEN + r"/"
)


def test_code_sites_use_the_canonical_prelude():
    # Strip the prelude before looking, rather than skipping any line that contains it: a line
    # carrying the prelude AND a second, wrong spelling of the placeholder would otherwise pass.
    offenders = [
        f"{path.relative_to(PLUGIN_ROOT)}:{num}: {line.strip()[:90]}"
        for path in _scanned_files()
        for num, line in _code_lines(path)
        if USES_PLACEHOLDER.search(line.replace(PRELUDE, ""))
    ]
    assert not offenders, (
        f"{len(offenders)} code line(s) name {TOKEN} without the canonical prelude:\n  "
        + "\n  ".join(offenders)
    )


# Dereferences of R: the bare form (`$R/`, `$R"`) and any brace form — `${R}`, `${R:-…}`,
# `${R%…}`, `${#R}` — which the `\b` and the optional `#` are what reach. That matters beyond
# tidiness: assertion 1 bans `CLAUDE_PLUGIN_ROOT:-` and not `R:-`, so while this pattern covered
# only `${R}` a site written `python3 "${R:-$HOME/.claude/local-marketplace/simo-dev}/lib/x.py"`
# — a respelling of the very default this file exists to keep out — passed all three assertions
# (measured 2026-08-22: of the four brace forms named above, `${R}` was already matched and the
# other three escaped, as did a backtick-adjacent bare placeholder). Not a closed set: the
# lookahead keeps `$REPO` and `$RES` out, but an indirection through a second name would not be
# seen. The prelude dereferences R itself in its own `[ -d "$R" ]` test, so the caller counts
# only matches whose COLUMN is left of the prelude's — not whole lines.
USES_ROOT = re.compile(r"\$R(?![A-Za-z0-9_])|\$\{#?R\b")


def test_code_regions_bind_the_root_before_using_it():
    """Order matters, not mere presence: a binding below the first use leaves R unbound at the use.

    That ordering is load-bearing — `skills/mr-followup/SKILL.md` keeps a comment demanding the
    binding stay above its `source`, and prose alone cannot gate it.
    """
    offenders = []
    for path in _scanned_files():
        for start, lines in _code_regions(path):
            bound = False
            for offset, line in enumerate(lines):
                # Compare COLUMNS, not just line numbers: the canonical one-liner puts the
                # binding and the use on one physical line, so a use to the left of the
                # prelude is unbound even though the same line binds it.
                at = line.find(PRELUDE)
                limit = at if at >= 0 else len(line)
                if not bound and any(m.start() < limit for m in USES_ROOT.finditer(line)):
                    why = (
                        "bound only afterwards"
                        if any(PRELUDE in rest for rest in lines[offset:])
                        else "never bound in this region"
                    )
                    offenders.append(
                        f"{path.relative_to(PLUGIN_ROOT)}:{start + offset} ({why}): "
                        f"{line.strip()[:80]}"
                    )
                    break
                if at >= 0:
                    bound = True
    assert not offenders, (
        f"{len(offenders)} code region(s) dereference R without binding it first — shell state "
        f"does not survive between Bash calls, and an unbound R makes the path absolute from /:\n  "
        + "\n  ".join(offenders)
    )
