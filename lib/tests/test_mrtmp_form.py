"""Guards that a shell block binds MRTMP before it dereferences it.

Shell state does not survive between Bash calls, so a fenced block executed on its own
sees MRTMP unset and every `"$MRTMP/<file>"` resolves from `/`. The consequence is silent
rather than loud: `mr_presentation.py --normalize-presentation "/mr_pres.json"` exits
non-zero and `skills/commit/SKILL.md` step 7 routes that non-zero into its minimal
fallback body, so the MR is created with a degraded description; `json_field.py
"$MRTMP/mr_pres.json" title` on the same missing file yields an empty MR title.

Three mechanical assertions, no classification, in the order the functions appear below:
  1. inside a shell block, any dereference of MRTMP is preceded by a `MRTMP=` binding in the
     same block. Order, not presence: a binding below the first use is unbound at the use.
  2. every `MRTMP=` binding names the shared `/tmp/claude_mr_` prefix. The key is
     `/tmp/claude_mr_<repo>_<branch>` precisely so concurrent sessions do not clobber each
     other (incident 2026-06-10) and so `simo-dev:commit` and `simo-dev:mr-followup` land in
     one directory; a binding with any other prefix is a fresh scratch dir that loses
     whatever the previous block wrote.
  3. every `MRTMP=` binding within one file is byte-identical to the others in that file. The
     prefix is not the constraint — the whole key is — so `/tmp/claude_mr_$(basename "$PWD")_…`
     satisfies assertion 2 and still resolves elsewhere.

Scope is the fence tag, not a judgment about content: `.md` fenced blocks tagged `bash` or
`sh`, plus whole `.sh` files. This differs from `test_plugin_root_form.py`, which treats every
fence as shell — deliberately, because two untagged fences in
`skills/mr-review/lib/findings-prompt.md` are agent-prompt templates that legitimately write
`<paste from $MRTMP/review_diff.txt>` and are not executed. Limit of that choice, observed
2026-08-22 and not a closed set: a real shell block written with an untagged fence would
escape this guard. `sh` is in the tag set so such a fence would be covered, not because one
exists — measured on this tree 2026-08-22: 67 ```bash fences and no ```sh.

A comment line is skipped, because a comment cannot dereference anything. That is not a
courtesy: `skills/mr-followup/lib/pipeline-scripts.md`'s poll script explains its own `$0.err`
naming with `# … one fixed name inside the $MRTMP dir`, and the script it documents never reads
the variable at runtime — it is written INTO that directory and refers to itself as `$0`. Only a
whole-line comment is skipped; a use with a trailing comment after it is still checked, and a
commented-out `# MRTMP=…` still does not count as a binding. `export`, `local` and `readonly`
prefixes DO count, and they have to: while the pattern rejected them, assertion 1 merely reported a
false positive on correct code, but assertions 2 and 3 stopped seeing the binding at all — an
`export MRTMP=/tmp/junk_dir` after a correct binding left all three green while the block resolved
a different directory (constructed and confirmed 2026-08-22).

The pattern matches `MRTMP=` at line start, optionally behind one of exactly those three keywords,
and nothing else. State the boundary that way rather than listing what falls outside it —
enumerating those spellings has already been wrong twice. What an unmatched spelling costs depends
on where it sits, and only one of the two shapes is dangerous (both measured 2026-08-22 on
throwaway copies of the tree, using `declare MRTMP=…` and `true && MRTMP=…`):

  - it is the region's ONLY binding — assertion 1 fires loudly, `never bound in this region`. The
    guard fails safe here.
  - it sits alongside a matched binding placed ABOVE the region's first use — only then does that
    neighbour satisfy assertion 1, so nothing fires and the divergent key is invisible to
    assertions 2 and 3. This is the miss, and it is the shape a careless edit produces. Below the
    first use the neighbour does not count either (assertion 1's own rule: order, not presence),
    so that sub-shape fails safe as well.

Widening the prefix set again would move the boundary, not remove it, because the anchor is what
the region model has to offer. The three that are matched are matched because they are the common
shell spellings, not because this tree uses them — it currently contains none of the three.

Prose outside fences is out of scope, and that is a real gap rather than an empty category. A
census on 2026-08-22 found 16 LINES carrying `$MRTMP` in prose outside any fence, across 7
files. Counting the two untagged agent-prompt fences named above brings it to 18 lines. Six of
those lines are commands a Bash call actually runs and that bind nothing at the site:
`skills/mr-review/SKILL.md`'s
three `> "$MRTMP/review_meta.json"` producer bullets, its `[ -s "$MRTMP/review_jira_full.txt" ]`
test, and the two `MERMAID=$(python3 .../json_field.py "$MRTMP/..." mermaid)` preview lines in
that file and in `skills/commit/SKILL.md`. Those sites rely on their file's convention sentence instead, and this
guard cannot see them. Assertion 3 inherits that blind spot across flows, not only within a
file: it compares per file, so a file holding exactly ONE binding is compared to nothing and a
lone divergent-but-prefixed key passes all three (constructed and confirmed 2026-08-22 by adding
such a fence to `skills/mr-followup/lib/pipeline-scripts.md`). That file is the live instance —
it has no binding in a shell fence at all, and its agreement with `skills/commit/SKILL.md` rests
on a THIRD spelling, written in prose, that keys on the substituted `<worktree-abs>` and
`printf '%s' "<branch>"` literals instead of reading the cwd. Grouping the files by intended form
was the alternative and was rejected: it classifies rather than forbids, and the map ages.

The `MR_TITLE` / `MR_BODY` / `TARGET_BRANCH` trio that `skills/commit/SKILL.md` step 9 used to
read from a different fence than the one binding them — the same defect class in different
variables — was closed the same way: `MR_BODY` now binds `MRTMP` and writes
`$MRTMP/mr_body.md` via a direct redirect inside the same fence it's produced in, exactly like
every other `$MRTMP`-using block in the file, so `test_shell_blocks_bind_mrtmp_before_using_it`
covers it like any other binding with no special-casing added here. `MR_TITLE` and
`TARGET_BRANCH` no longer cross a fence boundary at all: both are derived inline via
`python3 .../lib/json_field.py` in the very fence that consumes them (step 9's GitLab and
GitHub create blocks), so there is no cross-fence binding of that kind left for this guard to
model.

"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

FENCE = re.compile(r"^\s*```(\w*)")
SHELL_TAGS = {"bash", "sh"}
SCANNED_SUFFIXES = {".md", ".sh"}
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}
KEY_PREFIX = "/tmp/claude_mr_"

# A dereference of MRTMP: the bare form (`$MRTMP/`, `$MRTMP"`) and any brace form —
# `${MRTMP}`, `${MRTMP:-…}`, `${#MRTMP}`. The negative lookahead keeps a longer name such as
# `$MRTMPDIR` out, so a future variable is not silently held to this rule.
USES_MRTMP = re.compile(r"\$MRTMP(?![A-Za-z0-9_])|\$\{#?MRTMP\b")
BINDS_MRTMP = re.compile(r"^\s*(?:export\s+|local\s+|readonly\s+)?MRTMP=")
IS_COMMENT = re.compile(r"^\s*#")


def _scanned_files():
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if SKIPPED_DIRS & set(path.parts):
            continue
        yield path


def _shell_regions(path):
    """Yield (start_lineno, [lines]) per region a shell actually runs.

    A whole `.sh` file is one region; in `.md` only a fence tagged bash/sh opens one. The
    region is the unit the assertions group by, because MRTMP bound in one fenced block is
    gone by the next Bash call.
    """
    if path.suffix == ".sh":
        yield 1, path.read_text(errors="replace").splitlines()
        return
    inside, start, buf = False, 1, []
    for num, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        match = FENCE.match(line)
        if match:
            if inside:
                yield start, buf
                inside, buf = False, []
            elif match.group(1) in SHELL_TAGS:
                inside, start, buf = True, num + 1, []
            continue
        if inside:
            buf.append(line)
    if inside and buf:
        yield start, buf


def test_shell_blocks_bind_mrtmp_before_using_it():
    offenders = []
    for path in _scanned_files():
        for start, lines in _shell_regions(path):
            bound = False
            for offset, line in enumerate(lines):
                if IS_COMMENT.match(line):
                    continue
                if not bound and USES_MRTMP.search(line):
                    why = (
                        "bound only afterwards"
                        if any(BINDS_MRTMP.match(rest) for rest in lines[offset:])
                        else "never bound in this region"
                    )
                    offenders.append(
                        f"{path.relative_to(PLUGIN_ROOT)}:{start + offset} ({why}): "
                        f"{line.strip()[:80]}"
                    )
                    break
                if BINDS_MRTMP.match(line):
                    bound = True
    assert not offenders, (
        f"{len(offenders)} shell block(s) dereference MRTMP without binding it first — shell "
        f"state does not survive between Bash calls, and an unbound MRTMP makes the path "
        f"absolute from /:\n  " + "\n  ".join(offenders)
    )


def test_mrtmp_bindings_use_the_shared_key_prefix():
    offenders = [
        f"{path.relative_to(PLUGIN_ROOT)}:{start + offset}: {line.strip()[:80]}"
        for path in _scanned_files()
        for start, lines in _shell_regions(path)
        for offset, line in enumerate(lines)
        if BINDS_MRTMP.match(line) and KEY_PREFIX not in line
    ]
    assert not offenders, (
        f"{len(offenders)} MRTMP binding(s) do not name {KEY_PREFIX} — a different key is a "
        f"different directory, which loses the file the previous block wrote:\n  "
        + "\n  ".join(offenders)
    )


def test_mrtmp_bindings_are_uniform_within_a_file():
    """The prefix alone is not the constraint — the whole key has to come out identical.

    A block keyed differently from the block that wrote the file gets a different directory, so
    `/tmp/claude_mr_$(basename "$PWD")_…` passes the prefix assertion and still loses the file.
    Uniformity per file is the forbid-style form of that: the two files legitimately use different
    forms — `skills/commit/SKILL.md` keys on the task worktree `$W`, `skills/mr-review/SKILL.md` on
    the ambient toplevel — so the comparison cannot be tree-wide. Compared stripped, so an indented
    copy of the same binding is not an offender.
    """
    offenders = []
    for path in _scanned_files():
        seen = {}
        for start, lines in _shell_regions(path):
            for offset, line in enumerate(lines):
                if BINDS_MRTMP.match(line):
                    seen.setdefault(line.strip(), []).append(start + offset)
        if len(seen) > 1:
            spelled = "; ".join(
                f"line(s) {','.join(str(n) for n in nums)}: {form[:70]}"
                for form, nums in seen.items()
            )
            offenders.append(f"{path.relative_to(PLUGIN_ROOT)}: {len(seen)} forms — {spelled}")
    assert not offenders, (
        f"{len(offenders)} file(s) bind MRTMP more than one way — a divergent key is a divergent "
        f"directory, so a later block loses the file an earlier one wrote:\n  "
        + "\n  ".join(offenders)
    )
