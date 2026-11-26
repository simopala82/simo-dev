"""Pins the plan phase of a task-intake run: a writer subagent, one critique, one gate.

task-intake step 7 writes a brief file and dispatches `simo-dev:plan-writer`, which writes the plan by
`simo-dev:plan-write`'s rules and reports. Step 7.5 dispatches the `plan-reviewer` rounds with paths only,
sends fixes and gate edits back to the same writer, and builds its summary with `lib/plan_summary.py`, so the
main session never reads the plan. plan-write has no gate of its own and no standalone caller; grill-me run on
its own points to task-intake instead.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PLAN_WRITE = (ROOT / "skills/plan-write/SKILL.md").read_text()
INTAKE = (ROOT / "skills/task-intake/SKILL.md").read_text()
CRITIQUE = (ROOT / "skills/task-intake/lib/gate-critique.md").read_text()
GATE_RARE = (ROOT / "skills/task-intake/lib/plan-gate-rare.md").read_text()
WRITER = (ROOT / "agents/plan-writer.md").read_text()


def section(text, start, end):
    return text[text.index(start):text.index(end)]


STEP7 = section(INTAKE, "### 7. Detailed plan", "### 7.5.")
STEP75 = section(INTAKE, "### 7.5.", "### 8. Hand-off")
STEP8 = INTAKE.split("### 8. Hand-off", 1)[1].split("## Notes", 1)[0]


def test_plan_write_is_writer_only():
    for gone in ("## Plan critique and review gate", "gate: caller", "Then show:", "post_tracker_comment.py",
                 "plan-write's own gate"):
        assert gone not in PLAN_WRITE, gone
    for part in ("## Brief", "## Resumes", "## Report", "never invoke it in the main session"):
        assert part in PLAN_WRITE, part


def test_writer_report_fields_match_what_task_intake_reads():
    report = PLAN_WRITE.split("## Report", 1)[1]
    fields = re.findall(r"^([a-z-]+): ", report.split("```")[1], re.MULTILINE)
    assert fields == ["plan", "tasks", "critique-rounds", "assumptions", "applied", "rejected", "status"]
    assert "status: DONE | BLOCKED:" in report
    for field in ("`status:`", "`assumptions:`", "`rejected:`"):
        assert field in STEP7 or field in STEP75, field


def test_plan_writer_agent():
    front = WRITER.split("---")[1]
    assert "disallowedTools: Agent" in front
    assert re.search(r"experimental:\n\s+cacheTtl: 1h", front)
    assert "\nmodel:" not in front
    assert "`simo-dev:plan-write`" in WRITER and "SendMessage" in WRITER


def test_step_7_writes_a_brief_and_dispatches_the_writer():
    for part in ('mktemp -d "${TMPDIR:-/tmp}/claude_plan-brief.XXXXXX"', "`Choices made:`", "in their own words",
                 "subagent_type: simo-dev:plan-writer", "run_in_background: false", "no `model`",
                 "Never Read the plan file in this session", "to the gate's approving exit", "Scratchpad directory",
                 "**Resume**", "apply only the items below, do not rewrite it", "(`simo-dev:afk` answers it itself)"):
        assert part in STEP7, part
    assert "gate: caller" not in INTAKE


def test_step_7_5_summarizes_by_script_and_has_one_shape():
    assert 'lib/plan_summary.py" "<plan-abs-path>"' in STEP75
    assert "This step never reads the plan file" in STEP75
    for gone in ("Shape A", "Shape B", "hand-off signal", "table-pick", "in-session-declined",
                 "proceed in this session", "## Plan critique (gate)"):
        assert gone not in INTAKE, gone
    assert "`proceed in a new session` is never recommended" in STEP75
    assert "A bypass verdict, or `mcp__ccd_session__start_session` absent" in STEP75
    assert "never a fresh writer dispatch except **Resume**" in GATE_RARE
    assert "show this gate again with those lines (no new critique round), never exit" in STEP75
    assert "absent from this session's tools" in STEP75


def test_step_8_routes_only_in_session_or_spawn():
    assert "| Profile | Mode |" not in STEP8
    assert "`offer`" not in STEP8
    assert "`initiation` `user_asked`" in STEP8


def test_critique_passes_paths_and_fixes_through_the_writer():
    assert "The prompt carries paths, never content" in CRITIQUE
    assert "step 7's **Resume** carrying every `fix:` line" in CRITIQUE
    assert "re-read the plan file from disk" not in CRITIQUE


def test_grill_me_never_plans():
    output = (ROOT / "skills/grill-me/SKILL.md").read_text().split("## Output", 1)[1]
    assert "never invoke `simo-dev:plan-write` here" in output
    assert "`/simo-dev:task-intake`" in output
    assert "Hand off to `simo-dev:plan-write`" not in output
    assert "The ONLY next step is `simo-dev:plan-write`" not in output


def test_no_published_file_still_routes_to_a_standalone_plan_write():
    hits = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-n", "-E",
         r"gate: caller|Shape B|route back to `simo-dev:plan-write`|plan-write's own (gate|plan-critique)",
         "--", ".", ":!CHANGELOG.md", ":!lib/tests/"],
        capture_output=True, text=True,
    ).stdout
    assert hits == "", hits


def test_plan_write_replay_drives_intake_steps_7_and_7_5():
    import sys
    sys.path.insert(0, str(ROOT / "lib"))
    import step_replay as sr
    prompt = sr.STEPS["plan-write"].prompt("brief", "/repo")
    assert "`simo-dev:task-intake`" in prompt and "step 7.5" in prompt
    assert "never start a session" in prompt and "continues in this session" in prompt


def test_afk_answers_a_blocked_writer_and_replay_can_resume_it():
    afk = (ROOT / "skills/afk/SKILL.md").read_text()
    assert "**Plan writer `BLOCKED`** (task-intake step 7)" in afk
    import sys
    sys.path.insert(0, str(ROOT / "lib"))
    import step_replay as sr
    assert {"SendMessage", "ToolSearch", "Agent", "Skill"} <= set(sr.ALLOWED_TOOLS.split())


def test_step_7_5_rare_paths_keep_their_trigger_lines():
    # Each section moved to lib/plan-gate-rare.md is named, by its heading, from the place it used to sit.
    for heading, trigger in (
        ("## Loop-back answers", '`apply and re-present`, `edit` or `restart` → `lib/plan-gate-rare.md` "Loop-back answers"'),
        ("## Summary overflow", '→ `lib/plan-gate-rare.md` "Summary overflow"'),
        ("## Bare plan: escape hatch", '`lib/plan-gate-rare.md` "Bare plan: escape hatch"'),
        ("## Bypass conditions", '`lib/plan-gate-rare.md` "Bypass conditions"'),
        ("## Freshness check scope", '`lib/plan-gate-rare.md` "Freshness check scope"'),
    ):
        assert heading in GATE_RARE, heading
        assert trigger in STEP75, trigger
    for gone in ("On `restart` → discard the plan", "There is no runtime reviewer here", "1. **Follow-up on an existing branch.**"):
        assert gone not in STEP75, gone


def test_step_7_5_runs_the_precheck_script():
    assert '/lib/plan_gate_precheck.py" --repo "<repo>" --branch "<branch>" --row "<row>" --submodule "<submodule>"' in STEP75
    assert "`bypass: none`" in STEP75
