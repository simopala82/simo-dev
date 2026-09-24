"""Pins the trailing flags on every `transition_cmd "` call site in the skill bodies.

The Jira binding (`jira_forward_transition.py`, not shipped in the published plugin) lifts a
Blocked flag only with `--clear-blocked` and otherwise exits 8. The post-push CodeReview calls
(commit, mr-followup) and mr-followup's post-merge call must pass it; task-intake's step 5.6
"In Progress" call must not, and must branch on rc 8 (BLOCKED) with an `if` whose `elif` is
the generic non-zero WARNING, or a Blocked ticket also reads as a failed transition. Only a
grep in a plan checked this before, never a test.

Scans every `*.md` under `skills/` for the literal `transition_cmd "`, so a new call site
spelled that way there changes the count and fails loudly. A call site elsewhere (`agents/`,
`private/skills/`) or spelled differently is not seen. The GitHub binding side of the same
contract is `test_github_transition_call_site_flags.py`, not shipped in the published plugin.
"""
import re
from collections import Counter
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
CALL = 'transition_cmd "'
CODEREVIEW_FLAGS = "--leaf-only --sync-parent --clear-blocked ||"
INTAKE_TAIL = '"In Progress" --sync-parent; rc=$?'
INTAKE = "skills/task-intake/SKILL.md"


def call_sites():
    sites = []
    for path in sorted((PLUGIN_ROOT / "skills").rglob("*.md")):
        for line in path.read_text().splitlines():
            if CALL in line:
                sites.append((path.relative_to(PLUGIN_ROOT).as_posix(), line))
    return sites


def _codereview_sites():
    return [(f"{p}#{n}", line) for n, (p, line) in enumerate(call_sites(), 1) if p != INTAKE]


def test_exactly_four_call_sites():
    assert Counter(p for p, _ in call_sites()) == {
        "skills/commit/SKILL.md": 1,
        "skills/mr-followup/SKILL.md": 2,
        INTAKE: 1,
    }


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
