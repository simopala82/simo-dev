"""Wiring of the clean-context executor (plan-execute) and fixer (review-loop) subagents."""
import re
import subprocess
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent.parent / "skills"
PLAN_EXECUTE = (SKILLS / "plan-execute" / "SKILL.md").read_text()
EXECUTOR = (SKILLS / "plan-execute" / "lib" / "executor-dispatch.md").read_text()
AGENT_DISPATCH = (SKILLS / "plan-execute" / "lib" / "agent-dispatch.md").read_text()
REVIEW_LOOP = (SKILLS / "review-loop" / "SKILL.md").read_text()
FIXER_STEPS = (SKILLS / "review-loop" / "lib" / "fixer-steps.md").read_text()
AGENTS = SKILLS.parent / "agents"


def test_gate_has_three_modes_in_order():
    # The order itself is decided by lib/plan_execute_preflight.py (test_plan_execute_preflight.py).
    assert "Execution: <direct|subagent|agent-dispatch>" in PLAN_EXECUTE
    gate = PLAN_EXECUTE[PLAN_EXECUTE.index("**Execution delegation gate.**"):PLAN_EXECUTE.index("Print one line and continue")]
    assert gate.index("**`direct`**") < gate.index("**`agent-dispatch?`**") < gate.index("**`subagent`**")
    assert "**12 or less**" in gate
    assert "`lib/agent-dispatch.md`" in gate
    assert "`lib/executor-dispatch.md`" in PLAN_EXECUTE[PLAN_EXECUTE.index("Print one line and continue"):]
    decide = AGENT_DISPATCH[AGENT_DISPATCH.index("## Decide"):AGENT_DISPATCH.index("## Execution")]
    assert "`Consumes:`/`Produces:`" in decide and "substantial" in decide


def test_moved_branches_keep_their_trigger_lines():
    assert "`depends-on:` other than `none` → read `lib/dependency-check.md`" in PLAN_EXECUTE
    assert "**Dependency freshness check" not in PLAN_EXECUTE
    for lib in ("dependency-check.md", "executor-dispatch.md", "agent-dispatch.md"):
        text = (SKILLS / "plan-execute" / "lib" / lib).read_text()
        assert text.splitlines()[2].startswith("Loaded on demand from `plan-execute` `SKILL.md` step 1"), lib


def test_executor_branch_and_report_format():
    branch = EXECUTOR
    assert branch.startswith("# `subagent` — one clean-context executor")
    assert "`run_in_background: false`" in branch
    for key in ("tasks:", "files:", "verified:", "deviations:", "improvements: Task <N>", "notes:", "status: DONE | BLOCKED:"):
        assert key in branch
    assert "STOP-BASE rc=<1|4>" in branch
    assert "not `simo-dev:plan-execute` (the plan header's sub-skill line is satisfied by this dispatch)" in branch
    consumer = branch[branch.index("2. Mark each"):]
    for used in ("`improvements:`", "`DONE`", "`BLOCKED`", "`STOP-BASE`", "background-launch acknowledgement"):
        assert used in consumer
    assert "never dispatch an `Agent`" in branch
    assert "Write throwaway files only under a fresh `mktemp -d /tmp/claude_<slug>.XXXXXX`" in branch


def _frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n")
    return text[4:text.index("\n---\n", 4)]


def test_executor_dispatches_the_1h_cache_agent():
    branch = EXECUTOR
    assert "`subagent_type: simo-dev:plan-executor`" in branch
    assert "never `run_in_background`" in branch
    fm = _frontmatter(AGENTS / "plan-executor.md")
    assert re.search(r"^name: plan-executor$", fm, re.M)
    assert re.search(r"^experimental:\n[ \t]+cacheTtl: ['\"]?1h['\"]?$", fm, re.M) or re.search(
        r"^experimental: \{ ?cacheTtl: ['\"]?1h['\"]? ?\}$", fm, re.M)
    assert re.search(r"^disallowedTools: Agent$", fm, re.M)
    assert not re.search(r"^model:", fm, re.M), "the model comes from model_strategy plan_execute, never a pin"


def test_fixer_and_per_task_dispatches_keep_the_default_type():
    # A 1 h cache write costs 1.6x; only the executor pauses long enough to gain from it.
    fixer = REVIEW_LOOP[REVIEW_LOOP.index("### 4.5."):REVIEW_LOOP.index("\n### 5-6. ")]
    assert "(default `subagent_type`)" in fixer
    assert "plan-executor" not in AGENT_DISPATCH


def test_fixer_reads_sections_that_exist():
    # The fixer prompt names these headings: renaming one silently breaks the fixer.
    assert "### 4.5. Fixer subagent (runs steps 5-6)" in REVIEW_LOOP
    fixer = REVIEW_LOOP[REVIEW_LOOP.index("### 4.5."):REVIEW_LOOP.index("\n### 5-6. ")]
    for heading in ("## 5. Validity filter", "## 6. Auto-apply fixes"):
        assert f"`{heading}`" in fixer
        assert re.search(rf"^{re.escape(heading)}", FIXER_STEPS, re.M)
        assert not re.search(rf"^#{{2,3}} {re.escape(heading[3:])}", REVIEW_LOOP, re.M), "moved to lib/fixer-steps.md"
    assert "read `lib/fixer-steps.md` and run steps 5-6 here" in fixer
    assert "`lib/fixer-steps.md`" in REVIEW_LOOP[REVIEW_LOOP.index("\n### 5-6. "):REVIEW_LOOP.index("\n### 7. ")]
    for key in ("applied:", "rejected:", "ambiguous:", "escalated-during-fix:", "deferred (needs decision):",
                "ledger-add:", "ledger-drop:", "ledger-error:", "plan-resync:", "plan-drift-error:",
                "confirmed-correct:", "verified:"):
        assert key in fixer
    consumer = fixer[fixer.index("Its report feeds the rest of the loop"):]
    for key in ("applied:", "rejected:", "ambiguous:", "escalated-during-fix:", "deferred (needs decision)",
                "ledger-add:", "ledger-drop:", "ledger-error:", "plan-resync:", "plan-drift-error:",
                "confirmed-correct:", "verified:"):
        assert f"`{key}" in consumer, key
    assert "invoke no skill" in fixer
    assert "background-launch acknowledgement" in fixer
    assert '/skills/review-loop/lib/fixer-steps.md"; echo' in fixer
    assert "minus `/skills/review-loop/lib/fixer-steps.md`" in fixer
    assert "`run_in_background: false`" in fixer
