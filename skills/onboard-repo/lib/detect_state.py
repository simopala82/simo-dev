#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Detect existing Claude Code / simo-dev conventions in a target repo.

Read-only filesystem check used by the onboard-repo skill to build its
menu — reports what already exists so the skill can omit
already-satisfied choices instead of asking about them again.

Usage:
    detect_state.py <repo-root>

Prints a JSON object to stdout. Exit 0 on a successful scan (including a
repo where nothing is configured yet — that is not an error); exit 2 if
<repo-root> is missing, not a directory, or absent from argv.
"""
import json
import re
import sys
from pathlib import Path

AGENT_SKILLS_HEADING = re.compile(
    r"^#+\s*Agent skills\s*$", re.IGNORECASE | re.MULTILINE
)


def detect(repo_root: Path) -> dict:
    claude_md = repo_root / "CLAUDE.md"
    claude_md_exists = claude_md.is_file()

    agent_skills_section_present = None
    if claude_md_exists:
        text = claude_md.read_text(encoding="utf-8")
        agent_skills_section_present = bool(AGENT_SKILLS_HEADING.search(text))

    docs_agents = repo_root / "docs" / "agents"
    return {
        "claude_md": {
            "exists": claude_md_exists,
            "path": str(claude_md) if claude_md_exists else None,
        },
        "agent_skills_section_present": agent_skills_section_present,
        "docs_agents": {
            "issue_tracker": (docs_agents / "issue-tracker.md").is_file(),
            "triage_labels": (docs_agents / "triage-labels.md").is_file(),
            "domain": (docs_agents / "domain.md").is_file(),
        },
        "no_tracker_marker": (repo_root / ".claude" / "no-tracker").is_file(),
        "context_md": (repo_root / "CONTEXT.md").is_file(),
        "context_map_md": (repo_root / "CONTEXT-MAP.md").is_file(),
        "docs_adr": (repo_root / "docs" / "adr").is_dir(),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: detect_state.py <repo-root>", file=sys.stderr)
        return 2
    repo_root = Path(sys.argv[1])
    if not repo_root.is_dir():
        print(f"not a directory: {repo_root}", file=sys.stderr)
        return 2
    print(json.dumps(detect(repo_root), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
