"""Guards the removal of the visualize `read_me` precondition from the preview call sites.

Six assertions:
  1. the tool's full name appears in no scanned file;
  2. the counter-instruction is still present in both bodies;
  3. the needle is proven able to match, against a fixture that keeps the literal on purpose;
  4. the scan still reaches both call sites;
  5. the title `mr-review` cites for the rationale is present on both sides of that hop;
  6. all three render paths still fall back when `show_widget` errors, not only when it is absent.

The `show_widget` tool description says "IMPORTANT: Call read_me before your first show_widget
call", so `skills/commit/SKILL.md` and `skills/mr-review/SKILL.md` each carry a counter-instruction
against it, and prose is the weakest possible defence against a description that shouts. This is
the mechanical half. Rationale and the measurement behind the removal:
`~/.claude/docs/decisions/2026-08-22-no-read-me-before-show-widget.md`.

Why a bare forbid rather than "an instruction to call it": an exception list would have to sort
mentions into instructing and forbidding ones, and CLAUDE.md's rule is that a forbid is cheap
while a classifier is not. The counter-instructions are therefore worded "the visualize `read_me`
tool", never the full name, which leaves nothing to classify.

What this does and does not catch. Blind spots observed as of 2026-08-23, not a closed set:

  - A re-add that spells the name in pieces, or across a line break, passes assertion 1.
  - **A re-add needs no full name at all.** Inverting the counter-instruction to "Always call the
    visualize `read_me` tool first (module `diagram`)" is as actionable as the sentence it
    replaces and assertion 1 sees nothing — the counter-instruction is itself the proof that an
    instruction can name the tool loosely. Assertion 2 covers that shape by pinning the exact
    "Never …" sentence, so an inversion or a deletion fails. It does not cover an "Always" clause
    added *beside* an intact "Never" one.
  - Assertion 1 reads `SCANNED_SUFFIXES` only, and skips `SKIPPED_DIRS` plus the one control
    fixture — so a re-add in a file type not listed is invisible, as is one written into that
    fixture. Everything else under `fixtures/` IS scanned: excluding the single file rather than
    the directory keeps the other fixtures covered. `.txt` is scanned because `lib/prompts/*.txt`
    are real LLM prompt files where a re-add would be actionable.
  - Only the plugin tree is scanned. The ADR lives outside it and keeps the full name by design.
  - The needle is the tool's *canonical* name. An MCP server can also register under a hashed
    id — this repo's session on 2026-08-23 exposed the same two visualize tools under an
    `mcp__6f616b42-…__` prefix as well — and a re-add spelled that way passes assertion 1.
    Assertion 2 still catches it if it replaces the counter-instruction, not if it is added
    beside one.

The forbidden literal is assembled from parts so this file does not contain it. That is the
concatenation idiom `test_plugin_root_form.py` uses (`FALLBACK_FORM = TOKEN + ":-"`), though that
file also self-excludes via `SELF`, because it must spell `CLAUDE_PLUGIN_ROOT` in full elsewhere.
Nothing here needs the full literal, so concatenation alone is enough and no self-exclusion is
required — a weaker claim than the precedent, not the same one.

Assertion 3 exists because a needle that matches nothing anywhere makes assertion 1 pass forever
while asserting nothing that could come out false — the shape a personal engineering-notes file,
if you keep one, is worth recording it in. Two earlier versions of it were wrong, both measured:

  - Deriving the control from `FORBIDDEN`'s prefix left the discriminating half unguarded;
    `readme`, `read_mee` and `Read_me` each passed every assertion (2026-08-22).
  - Matching the whole literal, but against two superseded plan docs in the HOST REPO, broke the
    release: rsync ships the entire plugin into the cache and
    `.claude/scripts/plugin-release-verify.py` runs `pytest tests/ -q` from `<cache>/lib`, where
    no host-repo path resolves and the cache layout adds a `<version>` segment besides. Measured
    against a cache-shaped copy on 2026-08-23: `1 failed, 815 passed`.

So the control ships inside the plugin, in `fixtures/`, and matches the whole literal. A missing
fixture FAILS rather than skips — a skipped control is the vacuity again.
"""
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "read_me_control.txt"

FORBIDDEN = "mcp__visualize__" + "read_me"
COUNTER_INSTRUCTION = "Never call the visualize `read_me` tool"

CALL_SITES = ("skills/commit/SKILL.md", "skills/mr-review/SKILL.md")

# `mr-review` sends the reader to this heading in `commit` for the rationale. Nothing else guards
# a by-title cross-reference, and renaming it on either side would break the hop with no error —
# so assertion 5 pins the string in BOTH files. The step NUMBER is deliberately not part of it:
# `commit`'s steps get renumbered, and the citation quotes the title alone, so pinning `### 8. `
# would fail a renumbering that breaks nothing. The ADR path the heading in turn points at is
# deliberately NOT asserted either: it lives outside the plugin, and a check reaching out there
# fails inside the release cache — the defect this file carries a note about under assertion 3.
CITED_TITLE = "Mermaid preview (informational, no gate)"

# The ADR names the widened trigger as what makes the removal safe independent of the single
# render observation, and points here for the check rather than asking a reader to make it. It
# regressed once already on the branch that introduced it: two clauses were widened and the protected-branch one was missed,
# leaving that path showing nothing at all on an error. Pinned per file because the disposition
# differs — the two bodies skip, `alt-paths.md` falls back to `--stat`.
WIDENED_TRIGGERS = {
    "skills/commit/SKILL.md": "Not available, or `show_widget` errors →",
    "skills/mr-review/SKILL.md": "Not available, or `show_widget` errors →",
    "skills/commit/lib/alt-paths.md": "If `show_widget` is unavailable or errors →",
}

SCANNED_SUFFIXES = {".md", ".sh", ".py", ".txt"}
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}


def _scanned_files():
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if SKIPPED_DIRS & set(path.parts):
            continue
        if path == FIXTURE:  # holds the control literal on purpose; would self-fail assertion 1
            continue
        yield path


def _scan(paths, needle, label_root):
    found = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if needle in line:
                found.append(f"{path.relative_to(label_root)}:{lineno} — {line.strip()}")
    return found


def _hits(needle, only=None):
    paths = _scanned_files()
    if only is not None:
        paths = (p for p in paths if p.relative_to(PLUGIN_ROOT).as_posix() == only)
    return _scan(paths, needle, PLUGIN_ROOT)


def test_no_file_names_the_read_me_tool():
    offenders = _hits(FORBIDDEN)
    assert not offenders, (
        "the visualize read_me precondition is back; it is not required and costs ~62k chars "
        "(see ~/.claude/docs/decisions/2026-08-22-no-read-me-before-show-widget.md):\n"
        + "\n".join(offenders)
    )


def test_both_call_sites_still_forbid_it():
    """Covers the re-add shape assertion 1 cannot see: inverting or deleting the sentence."""
    for site in CALL_SITES:
        assert _hits(COUNTER_INSTRUCTION, only=site), (
            f"{site} no longer carries the counter-instruction "
            f'"{COUNTER_INSTRUCTION}" — assertion 1 alone does not catch a re-add '
            "that never spells the tool's full name"
        )


def test_the_whole_needle_can_match():
    """Positive control: a typo in either half of the needle fails here instead of going quiet."""
    assert FIXTURE.is_file(), (
        f"positive-control fixture gone: {FIXTURE} — without it the forbid assertion cannot be "
        "shown to match anything. The fixture ships with the plugin; restore it, do not skip."
    )
    assert _scan([FIXTURE], FORBIDDEN, FIXTURE.parent), (
        f"{FORBIDDEN!r} not found in {FIXTURE.name} — either the needle is misspelled (which "
        "would make the forbid assertion vacuous) or the fixture's literal was removed against "
        "its own instruction"
    )


def test_the_plugin_scan_reaches_the_call_sites():
    """Separate from the needle: proves rglob/PLUGIN_ROOT still cover what assertion 1 scans."""
    scanned = {p.relative_to(PLUGIN_ROOT).as_posix() for p in _scanned_files()}
    for site in CALL_SITES:
        assert site in scanned, f"{site} not scanned — the forbid assertion would skip it"


def test_the_cited_title_matches_on_both_sides():
    """A by-title hop breaks silently if either end moves, so pin the string in both files."""
    for site in CALL_SITES:
        assert _hits(CITED_TITLE, only=site), (
            f"{site} no longer carries {CITED_TITLE!r}. `mr-review` cites that title to reach "
            f"`commit`'s rationale, so both ends must spell it the same. If you renamed the "
            f"step on purpose, update CITED_TITLE in this file too — a rename applied correctly "
            f"on both sides still fails here until the constant follows."
        )


def test_every_render_path_falls_back_on_an_error():
    """Not only on an absent tool: a trigger widened in one path and missed in another regressed
    the protected-branch path to showing nothing at all."""
    for site, trigger in WIDENED_TRIGGERS.items():
        assert _hits(trigger, only=site), (
            f"{site} no longer carries {trigger!r} — its fallback has narrowed back to the "
            "tool being absent, so a `show_widget` error leaves that path with no output"
        )
