"""Tests for lib/detect_independent_task_groups.py."""
import importlib.util
from pathlib import Path

LIB = Path(__file__).parent.parent / "detect_independent_task_groups.py"

spec = importlib.util.spec_from_file_location("detect_independent_task_groups", LIB)
digt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digt)


def _plan(tasks_md: str) -> str:
    return f"# Plan\n\n**Goal:** test\n\n---\n\n{tasks_md}"


def test_disjoint_tasks_form_one_batch():
    plan = _plan(
        "### Task 1: A\n\n**Files:**\n- Create: `a.py`\n\n"
        "### Task 2: B\n\n**Files:**\n- Create: `b.py`\n\n"
        "### Task 3: C\n\n**Files:**\n- Create: `c.py`\n"
    )
    tasks = digt.parse_task_files(plan)
    assert digt.compute_batches(tasks) == [[1, 2, 3]]


def test_overlapping_tasks_split_into_separate_batches():
    plan = _plan(
        "### Task 1: A\n\n**Files:**\n- Modify: `shared.py`\n\n"
        "### Task 2: B\n\n**Files:**\n- Modify: `shared.py`\n\n"
        "### Task 3: C\n\n**Files:**\n- Modify: `shared.py`\n"
    )
    tasks = digt.parse_task_files(plan)
    assert digt.compute_batches(tasks) == [[1], [2], [3]]


def test_mix_preserves_plan_order():
    plan = _plan(
        "### Task 1: A\n\n**Files:**\n- Create: `a.py`\n\n"
        "### Task 2: B\n\n**Files:**\n- Create: `b.py`\n\n"
        "### Task 3: C\n\n**Files:**\n- Modify: `a.py:10-20`\n\n"
        "### Task 4: D\n\n**Files:**\n- Create: `c.py`\n"
    )
    tasks = digt.parse_task_files(plan)
    assert digt.compute_batches(tasks) == [[1, 2], [3, 4]]


def test_line_range_suffix_stripped_for_comparison():
    plan = _plan(
        "### Task 1: A\n\n**Files:**\n- Modify: `x.py:1-5`\n\n"
        "### Task 2: B\n\n**Files:**\n- Modify: `x.py:50-60`\n"
    )
    tasks = digt.parse_task_files(plan)
    assert digt.compute_batches(tasks) == [[1], [2]]


def test_task_with_no_files_block_is_isolated():
    # An empty file set must never join a batch (it would look "disjoint" from
    # everything) and must never let a later task join *its* batch either.
    plan = _plan(
        "### Task 1: A\n\n**Files:**\n- Create: `a.py`\n\n"
        "### Task 2: B\n\nno Files block here\n\n"
        "### Task 3: C\n\n**Files:**\n- Create: `c.py`\n"
    )
    tasks = digt.parse_task_files(plan)
    assert digt.compute_batches(tasks) == [[1], [2], [3]]


def test_prose_task_anchor_is_not_a_phantom_path():
    # simo-dev:plan-write's own prose-task template puts a second backtick
    # span (a stable anchor, never a path) after the real path: "- Modify:
    # `doc.md` at `<anchor>`". Only the first span may enter the file set.
    plan = _plan(
        "### Task 1: A\n\n**Files:**\n- Modify: `docs/one.md` at `<anchor>`\n\n"
        "### Task 2: B\n\n**Files:**\n- Modify: `docs/two.md` at `<anchor>`\n"
    )
    tasks = digt.parse_task_files(plan)
    assert tasks[0][1] == {"docs/one.md"}
    assert tasks[1][1] == {"docs/two.md"}
    assert digt.compute_batches(tasks) == [[1, 2]]


def test_main_prints_one_line_per_batch(tmp_path, capsys):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        _plan(
            "### Task 1: A\n\n**Files:**\n- Create: `a.py`\n\n"
            "### Task 2: B\n\n**Files:**\n- Modify: `a.py`\n"
        )
    )
    rc = digt.main([str(plan_path)])
    assert rc == 0
    assert capsys.readouterr().out == "1\n2\n"


def test_main_missing_file_exits_2(tmp_path, capsys):
    rc = digt.main([str(tmp_path / "nope.md")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_main_no_tasks_exits_2(tmp_path, capsys):
    plan_path = tmp_path / "empty.md"
    plan_path.write_text("# Plan\n\nNo tasks here.\n")
    rc = digt.main([str(plan_path)])
    assert rc == 2
    assert "no '## Task N:' or '### Task N:' headings" in capsys.readouterr().err


def test_two_hash_task_headers_are_parsed():
    # A plan may use `## Task N:` instead of the template's `### Task N:`
    # (undocumented nesting before this fix, or a legacy plan) -- the parser
    # must tolerate both header levels equally.
    plan = _plan(
        "## Task 1: A\n\n**Files:**\n- Create: `a.py`\n\n"
        "## Task 2: B\n\n**Files:**\n- Create: `b.py`\n"
    )
    tasks = digt.parse_task_files(plan)
    assert [n for n, _ in tasks] == [1, 2]
    assert digt.compute_batches(tasks) == [[1, 2]]
