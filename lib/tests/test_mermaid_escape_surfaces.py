"""Pins the mermaid escape rule on the surfaces that feed it to a model.

The root cause of MR !135 was not the normalizer — it was a prose surface telling
the model that `&quot;` was an acceptable way to write a literal quote inside a
label. The normalizer fix has its own tests; this file covers the other half, and
only mechanically. A green run here means the assertions in this file held and
nothing beyond them — in particular it does NOT mean no surface recommends an HTML
entity. Read the blind-spot paragraph for the measured limit before trusting it
further.

Four mechanical assertions, no classification (plus the bullet-identity check
`test_mermaid_prompt_bullets_match`, which is what makes a one-sided edit to
either prompt template fail):
  1. every listed surface names `#quot;`;
  2. every listed surface carries the prohibition marker, so dropping the
     "never HTML entities" sentence fails here rather than at the next MR;
  3. no listed surface, and not `mr_presentation.py`, carries either retired
     claim — `mermaid has no escape …` (false: `#name;` is one) or `mermaid
     needs literal characters` (the wrong remedy for `&quot;` and `&lt;type&gt;`);
  4. `lint_mermaid`'s own entity message recommends `#quot;`. Asserted through the
     function, not the file text: that message is copied verbatim into review
     output a person or a model then reads, which makes it a surface too.

The surface list is hardcoded on purpose. Discovering it — globbing prompts, or
matching files that "look like" they state the rule — would be the classifying
rule CLAUDE.md warns about, and a surface that drifts out of the pattern would
drop out of the guard silently. A new surface has to be added here by hand; that
edit is the point.

Assertion 2 tests for a marker substring, not for the whole sentence, so the
wording stays free to be reviewed and reworded — which it was, four times, while
this change was being written.

Out of scope by construction, observed rather than a closed set: this file cannot
tell a prohibition from a recommendation, so a surface that names `#quot;`, keeps
the marker, and *also* recommends `&quot;` somewhere else in its text passes all
four. Measured 2026-08-22 on a throwaway copy — appending "(or `&quot;`, which
also works)" to the `#quot;` clause of `lib/schemas/mr_presentation.json`, and the
same to both prompt bullets, each left the suite at 18 passed. That is the MR !135
shape, green. Assertion 3 catches the two specific claims that actually caused
trouble, not the general shape.

Nor is `SURFACES` every place the rule is stated. `CONTEXT.md`'s glossary and the
`--normalize-presentation` parentheticals in `skills/commit/SKILL.md` and
`skills/mr-review/SKILL.md` are out because they describe what the normalizer does,
or define a term — they do not tell an author how to write a label. NOT because
they go unread by a model: this repo's own glossary calls a rendered `SKILL.md` a
model-fed surface, and `docs/agents/domain.md` has the skills read `CONTEXT.md`
before exploring. The criterion is what the text is for, and it stays checkable —
both `SKILL.md` parentheticals name `#quot;` yet carry no prohibition, so listing
them here would fail assertion 2 rather than strengthen anything.

And nothing pins the schemas to each other — only `test_mermaid_prompt_bullets_match`
pins a pair.
"""
import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent
PLUGIN = LIB.parent

spec = importlib.util.spec_from_file_location("mr_presentation", LIB / "mr_presentation.py")
mr_presentation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr_presentation)

# The surfaces that tell an author how to write a label. Not every place the rule
# is stated — see the docstring. Hardcoded on purpose, also per the docstring.
SURFACES = (
    LIB / "prompts" / "mr_presentation_create.txt",
    LIB / "prompts" / "mr_presentation_review.txt",
    LIB / "schemas" / "mr_presentation.json",
    LIB / "schemas" / "findings.json",
    PLUGIN / "skills" / "mr-review" / "lib" / "findings-prompt.md",
)

PROHIBITION_MARKER = "NEVER HTML entities"
RETIRED_CLAIMS = ("mermaid has no escape", "needs literal characters")

# The shared bullet the two prompt templates must keep identical.
BULLET_ANCHOR = "Mermaid node labels MUST be double-quoted"


@pytest.mark.parametrize("surface", SURFACES, ids=lambda p: p.name)
def test_surface_names_the_mermaid_quote_escape(surface):
    assert surface.is_file(), f"listed surface is missing: {surface}"
    assert "#quot;" in surface.read_text(), (
        f"{surface.name} no longer names #quot; — the only in-label quote escape mermaid reads. "
        "If this surface moved, update SURFACES; do not delete the assertion."
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda p: p.name)
def test_surface_forbids_html_entities(surface):
    assert PROHIBITION_MARKER in surface.read_text(), (
        f"{surface.name} lost the '{PROHIBITION_MARKER}' prohibition. Its absence is what let an "
        "agent write &quot; into a mermaid label in the first place (MR !135)."
    )


@pytest.mark.parametrize(
    "path", SURFACES + (LIB / "mr_presentation.py",), ids=lambda p: p.name
)
def test_no_retired_escape_claim_survives(path):
    text = path.read_text()
    for claim in RETIRED_CLAIMS:
        assert claim not in text, (
            f"{path.name} carries the retired claim {claim!r}. Mermaid does have an escape "
            "syntax (#quot; / #lt; / #gt;), and a literal character is the wrong remedy for "
            "&quot; and for &lt;type&gt;."
        )


def test_mermaid_prompt_bullets_match():
    bullets = []
    for name in ("mr_presentation_create.txt", "mr_presentation_review.txt"):
        lines = [ln for ln in (LIB / "prompts" / name).read_text().splitlines() if BULLET_ANCHOR in ln]
        assert len(lines) == 1, f"{name}: expected exactly one {BULLET_ANCHOR!r} bullet, found {len(lines)}"
        bullets.append(lines[0])
    assert bullets[0] == bullets[1], "the two prompt templates' label bullet has drifted apart"


def test_lint_message_recommends_the_mermaid_escape():
    problems = mr_presentation.lint_mermaid('flowchart LR\n  A["x &quot;y&quot;"]', "flowchart")
    entity = [p for p in problems if "HTML-escaped entities" in p]
    assert entity, f"lint_mermaid stopped reporting HTML entities: {problems}"
    assert "#quot;" in entity[0], (
        f"the entity lint message no longer names #quot;: {entity[0]!r}. It is copied verbatim "
        "into review output, so a wrong remedy there steers a reader back into the bug."
    )
