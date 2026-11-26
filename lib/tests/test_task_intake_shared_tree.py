"""task-intake must never adopt a desktop-app tree another live session also works in.

Pins step 6's row order (row 3s, the shared-tree stop, is matched before row 3a's adopt in place) in
the body table and in lib/worktree_resolve.py's docstring, that row 3s runs no rename/rebase command,
that the shared-tree check names its two reads and the directory compare, and that step 3 and step
7.5's pre-gate condition 2 route through row 3s. The classification itself: test_worktree_resolve.py.
"""
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent / "skills" / "task-intake" / "SKILL.md"
SCRIPT = Path(__file__).resolve().parent.parent / "worktree_resolve.py"


def section(start, end):
    text = SKILL.read_text()
    i = text.index(start)
    return text[i:text.index(end, i)]


def step6_rows():
    order, rows = [], {}
    for line in section("### 6. Branch and worktree", "### 7. Detailed plan").splitlines():
        m = re.match(r"^\| (\d[a-z]?) \|(.*)$", line)
        if m:
            order.append(m.group(1))
            rows[m.group(1)] = m.group(2)
    return order, rows


def test_shared_row_is_matched_before_adopt_in_place():
    order, _ = step6_rows()
    assert order == ["1", "2", "3s", "3a", "3b", "4"]


def test_script_docstring_lists_the_same_rows_in_the_same_order():
    doc = SCRIPT.read_text().split('"""')[1]
    order = [m.group(1) for m in re.finditer(r"^    (\d[a-z]?) +\S", doc, re.M)]
    assert order == ["1", "2", "3s", "3a", "3b", "4"]


def test_shared_row_stops_and_runs_no_rename_or_rebase():
    _, rows = step6_rows()
    row = rows["3s"]
    assert "shared tree" in row and "desktop-app tree" in row
    assert "STOP" in row
    for command in ("branch -m", "switch -c", "park_and_rebase", "worktree move"):
        assert command not in row, command


def test_shared_tree_check_names_both_reads_and_the_dir_compare():
    step6 = section("### 6. Branch and worktree", "### 7. Detailed plan")
    start = step6.index("\n**Shared-tree check**")
    definition = step6[start:step6.index("\n", start + 1)]
    for token in ("get_session", "list_sessions", "linked", "limit: 200", "worktreePath", "-ef"):
        assert token in definition, token


def test_row3s_reads_the_shared_tree_stop():
    _, rows = step6_rows()
    trigger = rows["3s"].lower()
    assert "lib/shared-tree-stop.md" in trigger and "nothing is written, renamed or parked" in trigger
    stop = (SKILL.parent / "lib" / "shared-tree-stop.md").read_text()
    for token in ("Re-run the **shared tree** check", "use_worktree", "adopt anyway", "user's go", "**Under `simo-dev:afk`**"):
        assert token in stop, token


def test_resolve_reruns_the_shared_tree_check_before_matching():
    step6 = section("### 6. Branch and worktree", "### 7. Detailed plan")
    start = step6.index("**Resolve.**")
    lead_in = step6[start:step6.index("```bash", start)]
    assert "**Shared-tree check**" in lead_in and "again" in lead_in


def test_row_lists_elsewhere_name_the_shared_row():
    step8 = section("### 8. Hand-off", "## Notes")
    in_session = step8[step8.index("- **In-session worktree**"):]
    assert "3s" in in_session[:in_session.index("\n")]


def test_step3_runs_the_check_before_anything_is_written():
    step3 = section("### 3. High-level system-impact analysis", "### 4. Direction confirmation gate")
    assert "shared tree" in step3 and "3s" in step3
    assert step3.index("3s") < step3.index("Dispatch **up to 2"), "the check must come before Explore"


def test_pre_gate_condition_2_names_the_shared_row():
    rare = (SKILL.parent / "lib" / "plan-gate-rare.md").read_text()
    cond2 = rare[rare.index("2. **This session is itself"):rare.index("3. **This session is a submodule")]
    assert "3s" in cond2


def test_step3_and_step6_call_the_script():
    step3 = section("### 3. High-level system-impact analysis", "### 4. Direction confirmation gate")
    assert 'worktree_resolve.py" resolve --dir .' in step3
    step6 = section("### 6. Branch and worktree", "### 7. Detailed plan")
    assert 'worktree_resolve.py" resolve --dir "<TOP>" --branch "<branch>" --base "<base>"' in step6
    assert 'worktree_resolve.py" adopt --wt "<WT>"' in step6
    for flag in ("--self-worktree", "--linked-cwd", "--no-session-tools", "--adopt-anyway", "--foreign-ok", "--old-remote-ok"):
        assert flag in step6 and flag in SCRIPT.read_text(), flag
