"""plan_summary.py: the plan gate's summary, built without the orchestrating session reading the plan."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "plan_summary.py"

PLAN = """# Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use simo-dev:plan-execute.

**Goal:** Add a widget to the dashboard.
**Architecture:** One new module renders the widget, e.g. `Widget`. The page imports it. Tests cover both.
**Tech Stack:** Python
**Critique rounds:** `2` — 2 tasks of code, no declared risk

## Global Constraints
- stdlib only
---

### Task 1: Widget module

**Files:**
- Create: `lib/widget.py`
- Test: `lib/tests/test_widget.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Run it**

### Task 2: Page wiring

**Files:**
- Modify: `lib/page.py:10-20`
- Modify: `lib/widget.py`
- Modify: `docs/page.md` at `## Widgets`

- [ ] **Step 1: Wire it**

```markdown
### Task 9: Example inside a fence
- Modify: `fenced/example.py`
- [ ] not a real step
```

## Risks
- The page cache may hide the widget until a restart.
"""


def run(tmp_path, text):
    plan = tmp_path / "plan.md"
    plan.write_text(text)
    return subprocess.run([sys.executable, str(SCRIPT), str(plan)], capture_output=True, text=True)


def test_full_plan(tmp_path):
    out = run(tmp_path, PLAN)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "Goal: Add a widget to the dashboard.",
        "Approach:",
        "- One new module renders the widget, e.g. `Widget`.",
        "- The page imports it.",
        "- Tests cover both.",
        "Tasks: 2",
        "- Task 1: Widget module",
        "- Task 2: Page wiring",
        "Files touched: 4",
        "- lib/widget.py",
        "- lib/tests/test_widget.py",
        "- lib/page.py",
        "- docs/page.md",
        "Risks:",
        "- The page cache may hide the widget until a restart.",
        "Critique rounds: `2` — 2 tasks of code, no declared risk",
    ]


def test_bare_plan(tmp_path):
    out = run(tmp_path, "**Goal:** bump the timeout\n\n### Task 1: bump it\n- Modify: `conf/app.yml`\n")
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "Goal: bump the timeout",
        "Tasks: 1",
        "- Task 1: bump it",
        "Files touched: 1",
        "- conf/app.yml",
    ]


def test_missing_file_exits_2(tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "none.md")], capture_output=True, text=True)
    assert out.returncode == 2 and "plan_summary:" in out.stderr


def test_label_risks_missing_goal_and_lookalike_headings(tmp_path):
    text = ("### Task 1: x\n- Modify: `a.py`\n\n#### Risk-scaled caps\nnot a risk\n\n"
            "**Risks:** a flaky network test\n")
    out = run(tmp_path, text)
    assert out.stdout.splitlines() == [
        "Goal: (no Goal line)", "Tasks: 1", "- Task 1: x", "Files touched: 1", "- a.py",
        "Risks:", "- a flaky network test",
    ]


def test_unclosed_fence_is_kept(tmp_path):
    out = run(tmp_path, "**Goal:** g\n```\n### Task 1: inside an unclosed fence\n- Modify: `b.py`\n")
    assert "- Task 1: inside an unclosed fence" in out.stdout and "- b.py" in out.stdout
