"""Pins the verification-cwd rule: a subdirectory verification command never leaves the Bash cwd there.

The rule lives in verify-done's gate step 2, where verification commands run (e.g. review-loop step 0,
plan-execute's direct branch, the fix rounds); review-loop 0b points at it. The Bash cwd persists between
calls; once it sat inside a repo subdirectory, the next skill load attached that subdirectory's nested
CLAUDE.md and path-scoped rules to the main session a second time (observed once).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VERIFY = (ROOT / "skills/verify-done/SKILL.md").read_text()
RUN_STEP = VERIFY[VERIFY.index("2. **Run**"):VERIFY.index("3. **Read**")]
REVIEW = (ROOT / "skills/review-loop/SKILL.md").read_text()
STEP0 = REVIEW[REVIEW.index("### 0."):REVIEW.index("### 1.")]
STEP0B = STEP0[STEP0.index("**0b."):STEP0.index("**0c.")]


def test_directory_flag_examples():
    for flag in ("npm --prefix <dir>", "pnpm -C <dir>", "yarn --cwd <dir>", "go -C <dir>", "uv --directory <dir>",
                 "cargo test --manifest-path <dir>/Cargo.toml", "dotnet test <dir>"):
        assert flag in RUN_STEP, flag


def test_subshell_is_the_fallback():
    assert '( cd "<worktree-abs>/<dir>" && <command> )' in RUN_STEP
    assert RUN_STEP.index("directory flag") < RUN_STEP.index("subshell")


def test_bare_cd_is_forbidden_with_its_reason():
    assert "Never a bare `cd`" in RUN_STEP
    assert "the Bash cwd persists into later calls" in RUN_STEP
    assert "(observed once)" in RUN_STEP


def test_review_loop_points_at_the_gate():
    assert "never a bare `cd` (`simo-dev:verify-done` gate step 2)" in STEP0B
