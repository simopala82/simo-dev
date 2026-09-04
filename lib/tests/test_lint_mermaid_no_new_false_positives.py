"""Guard: the new lint_mermaid label checks must not flag any ```mermaid block
already committed in this repo. A first-draft regex during the design of these
checks produced 59 false positives on exactly this corpus (mostly plain quoted
labels like A["Intake & planning"]) — this pins the zero-false-positive result
so a future edit to the regexes in lint_mermaid can't silently regress it.
Scans docs/, CONTEXT.md, and .claude/ under the repo root; skips if none found
(a shallow/sparse checkout), since an empty corpus is not a passing signal for
what this test claims to check."""
import re
import subprocess
import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "mr_presentation.py"
spec = importlib.util.spec_from_file_location("mr_presentation", LIB)
mr_presentation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr_presentation)
lint_mermaid = mr_presentation.lint_mermaid

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=Path(__file__).parent,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
)

MERMAID_FENCE_RE = re.compile(r"```mermaid\n(.*?)```", re.S)

# Only the three checks this task adds. Some committed ```mermaid fences are
# doc placeholders (e.g. `<per_finding_mermaid>` as literal illustrative text
# in output-templates.md / superpowers specs) that already fail the
# pre-existing header check when forced through diagram_type="flowchart" —
# that is not a regression this guard is about, so it is excluded here.
NEW_CHECK_PREFIXES = (
    "nested/unescaped quote in label",
    "quote inside unquoted label",
    "literal angle bracket in label",
)


def _committed_mermaid_blocks():
    files = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "grep", "-l", "```mermaid", "--", "docs", "CONTEXT.md", ".claude"],
        capture_output=True, text=True,
    ).stdout.split()
    blocks = []
    for rel in files:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for m in MERMAID_FENCE_RE.finditer(text):
            blocks.append((rel, m.group(1)))
    return blocks


def test_no_new_warnings_on_committed_mermaid_blocks():
    blocks = _committed_mermaid_blocks()
    if not blocks:
        pytest.skip("no committed ```mermaid blocks found under docs/CONTEXT.md/.claude — nothing to check")
    problems = []
    for rel, block in blocks:
        for p in lint_mermaid(block, "flowchart"):
            if p.startswith(NEW_CHECK_PREFIXES):
                problems.append(f"{rel}: {p}")
    assert not problems, "new lint_mermaid checks flagged real, already-shipped diagrams:\n" + "\n".join(problems)
