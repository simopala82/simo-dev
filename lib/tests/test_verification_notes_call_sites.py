"""Pins where the "Verification notes (auto)" comment is posted.

The spec and the only post call live in skills/commit/lib/verification-notes.md; commit step 9,
the existing-MR path, mr-followup's report-ending stops and post-merge load it instead of posting
on their own.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = "skills/commit/lib/verification-notes.md"


def read(rel):
    return (ROOT / rel).read_text()


def test_spec_owns_the_post_and_the_state_calls():
    text = read(SPEC)
    assert text.count('lib/post_tracker_comment.py"') == 1
    for sub in ("has", "show", "stale", "record"):
        assert f'verification_notes_state.py" {sub}' in text, sub
    assert "h4. Verification notes (auto)" in text


def test_every_call_site_loads_the_spec():
    for rel in ("skills/commit/SKILL.md", "skills/commit/lib/alt-paths.md", "skills/mr-followup/SKILL.md",
                "skills/mr-followup/lib/post-merge.md", "skills/mr-followup/lib/pre-merge-qa-gate.md"):
        assert "verification-notes.md" in read(rel), rel


def test_mr_followup_report_runs_the_supersede_check():
    text = read("skills/mr-followup/SKILL.md")
    section = text[text.index("### 7.4. Final report"):text.index("### 7.5.")]
    assert "Supersede check" in section
    assert "verification-notes.md" in section


def test_post_merge_no_longer_posts_on_its_own():
    text = read("skills/mr-followup/lib/post-merge.md")
    assert "post_tracker_comment.py" not in text
    assert 'verification_notes_state.py" has' in text
    assert 'verification_notes_state.py" stale' in text


def test_no_other_skill_posts_the_notes():
    for path in (ROOT / "skills").rglob("*.md"):
        rel = path.relative_to(ROOT).as_posix()
        if rel != SPEC and "Verification notes (auto)" in path.read_text():
            assert "post_tracker_comment.py" not in path.read_text(), rel
