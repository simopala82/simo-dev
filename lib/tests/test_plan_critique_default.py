"""Tests for the plan_critique default/sentinel bash logic used at
plan-write SKILL.md's Plan critique and review gate section (the block that resolves PLAN_CRITIQUE_MODEL).
model_strategy.py itself is unchanged; this snippet is the only place that
knows plan_critique defaults to "sonnet" and that "none" disables dispatch."""
import subprocess
from pathlib import Path

LIB = Path(__file__).parent.parent / "model_strategy.py"
SKILL_MD = LIB.parent.parent / "skills" / "plan-write" / "SKILL.md"

SNIPPET = '''
PLAN_CRITIQUE_MODEL=$(python3 "{lib}" get plan_critique --config "{config}")
if [ "$PLAN_CRITIQUE_MODEL" = "none" ]; then
  PLAN_CRITIQUE_MODEL=""
elif [ -z "$PLAN_CRITIQUE_MODEL" ]; then
  PLAN_CRITIQUE_MODEL="sonnet"
fi
printf '%s' "$PLAN_CRITIQUE_MODEL"
'''


def _run_snippet(config_path):
    script = SNIPPET.format(lib=LIB, config=config_path)
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    return result.stdout


def test_unconfigured_defaults_to_sonnet(tmp_path):
    config = tmp_path / "model-strategy.json"
    assert _run_snippet(config) == "sonnet"


def test_none_sentinel_disables_dispatch(tmp_path):
    config = tmp_path / "model-strategy.json"
    subprocess.run(
        ["python3", str(LIB), "set", "plan_critique", "none", "--config", str(config)],
        check=True,
    )
    assert _run_snippet(config) == ""


def test_explicit_override_wins(tmp_path):
    config = tmp_path / "model-strategy.json"
    subprocess.run(
        ["python3", str(LIB), "set", "plan_critique", "opus", "--config", str(config)],
        check=True,
    )
    assert _run_snippet(config) == "opus"


def test_empty_string_reset_also_defaults_to_sonnet(tmp_path):
    config = tmp_path / "model-strategy.json"
    subprocess.run(
        ["python3", str(LIB), "set", "plan_critique", "", "--config", str(config)],
        check=True,
    )
    assert _run_snippet(config) == "sonnet"


def test_invalid_json_falls_through_to_sonnet(tmp_path):
    config = tmp_path / "model-strategy.json"
    config.write_text("{not json")
    assert _run_snippet(config) == "sonnet"


def _if_block(lines):
    start = next(i for i, l in enumerate(lines) if l.startswith("if ["))
    end = next(i for i, l in enumerate(lines) if i >= start and l.strip() == "fi")
    return "\n".join(lines[start : end + 1])


def test_skill_md_if_block_matches_tested_snippet():
    """Anti-drift guard: the if/elif/fi block shipped in plan-write/SKILL.md's Plan critique and
    review gate section must stay byte-identical to the block exercised by the tests above. A
    future edit to either side without the other would otherwise leave this test suite green
    while the shipped logic diverges from what was actually verified."""
    shipped = _if_block(SKILL_MD.read_text().splitlines())
    tested = _if_block(SNIPPET.strip("\n").splitlines())
    assert shipped == tested
