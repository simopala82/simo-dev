"""Pins the trailing flags on every `transition_cmd "` call site in the skill bodies.

The Jira binding (`jira_forward_transition.py`, not shipped in the published plugin) lifts a
Blocked flag only with `--clear-blocked` and otherwise exits 8. The post-push CodeReview calls
(commit's lives in `lib/mr_create.py`, pinned by `test_commit_codereview_site_is_the_script` through its
`CODEREVIEW_FLAGS` constant; mr-followup's stays an md site) and mr-followup's post-merge call (step 8, in `lib/post-merge.md`) must pass it; task-intake's step 5.6
"In Progress" call must not, and must branch on rc 8 (BLOCKED) with an `if` whose `elif` is
the generic non-zero WARNING, or a Blocked ticket also reads as a failed transition. The
post-merge parent close (mr-followup `lib/post-merge.md`, step 8.6) moves the umbrella to
"Done" with `--clear-blocked` alone: no `--leaf-only` (the key IS the umbrella) and no
`--sync-parent` (it has no parent to sync). Only a grep in a plan checked this before, never a
test.

Scans every `*.md` under `skills/` for the literal `transition_cmd "`, so a new call site
spelled that way there changes the count and fails loudly. A call site elsewhere (`agents/`,
`private/skills/`) or spelled differently is not seen. The GitHub binding side of the same
contract is `test_github_transition_call_site_flags.py`, not shipped in the published plugin.
"""
import importlib.util
import re
from collections import Counter
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
CALL = 'transition_cmd "'
CODEREVIEW_FLAGS = "--leaf-only --sync-parent --clear-blocked ||"
INTAKE_TAIL = '"In Progress" --sync-parent; rc=$?'
INTAKE = "skills/task-intake/SKILL.md"
POST_MERGE = "skills/mr-followup/lib/post-merge.md"


def call_sites():
    sites = []
    for path in sorted((PLUGIN_ROOT / "skills").rglob("*.md")):
        for line in path.read_text().splitlines():
            if CALL in line:
                sites.append((path.relative_to(PLUGIN_ROOT).as_posix(), line))
    return sites


def _is_parent_close(path, line):
    return path == POST_MERGE and '"Done"' in line


def _codereview_sites():
    return [(f"{p}#{n}", line) for n, (p, line) in enumerate(call_sites(), 1) if p != INTAKE and not _is_parent_close(p, line)]


def test_exactly_three_md_call_sites():
    assert Counter(p for p, _ in call_sites()) == {
        INTAKE: 1,
        POST_MERGE: 2,
    }


def test_commit_codereview_site_is_the_script():
    """commit step 9's CodeReview call lives in lib/mr_create.py, outside the `skills/**/*.md` scan."""
    script = PLUGIN_ROOT / "lib" / "mr_create.py"
    spec = importlib.util.spec_from_file_location("mr_create", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CODEREVIEW_FLAGS == ("--leaf-only", "--sync-parent", "--clear-blocked")
    assert script.read_text().count('"transition_cmd"') == 1


def test_mr_followup_codereview_site_is_the_shared_function():
    """mr-followup step 1's CodeReview call lives in lib/mr_followup.py and goes through
    mr_create.codereview, so the flags above cover it too."""
    text = (PLUGIN_ROOT / "lib" / "mr_followup.py").read_text()
    assert "mc.codereview(" in text
    assert "transition_cmd" not in text


@pytest.mark.parametrize("site,line", _codereview_sites(), ids=[s for s, _ in _codereview_sites()])
def test_codereview_site_clears_blocked(site, line):
    assert CODEREVIEW_FLAGS in line, site


def _intake_line():
    [line] = [line for p, line in call_sites() if p == INTAKE]
    return line


def test_intake_site_keeps_sync_parent_tail():
    assert INTAKE_TAIL in _intake_line()


def test_intake_site_omits_clear_blocked():
    assert "--clear-blocked" not in _intake_line()


def test_intake_blocked_branch_precedes_warning():
    text = (PLUGIN_ROOT / INTAKE).read_text()
    start = text.index("### 5.6.")
    section = text[start:text.index("### 6.", start)]
    after_call = section[section.index(CALL):]
    blocked = re.search(r'^if \[ "\$rc" -eq 8 \]', after_call, re.M)
    warning = re.search(r'^elif \[ "\$rc" -ne 0 \]', after_call, re.M)
    assert blocked and warning and blocked.start() < warning.start()


def test_parent_close_site_is_gated_done_without_leaf_only():
    [line] = [line for p, line in call_sites() if _is_parent_close(p, line)]
    assert '"Done" --clear-blocked ||' in line
    assert "--leaf-only" not in line
    text = (PLUGIN_ROOT / POST_MERGE).read_text()
    assert "next_subtask_cmd" in text


def test_post_merge_step_prints_literals_for_step_8_6():
    """Step 8.6 runs after the worktree is gone and skips without step 8's `post-merge:` line."""
    text = (PLUGIN_ROOT / POST_MERGE).read_text()
    assert 'echo "post-merge: KEY=$KEY PREFIX=$PREFIX TARGET=$TARGET"' in text
