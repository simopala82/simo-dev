#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""The `REPO:` marker of an agent transcript — line 1 of its FIRST user message.

Same contract as the prompt-side extraction it replaces (ADR 0003): the marker must be
the whole of line 1, the path must be absolute, and nothing below line 1 is read. The
source moved from the `Agent` tool_input to the transcript because the record is now
written on `SubagentStop`, which fires when the reviewer concludes and carries only
`agent_transcript_path`.

FIRST user message, not any user message, and that is the security boundary: a
`SendMessage` resume appends further user messages, so honouring a later one would let
the text of a re-review re-attribute a record that a fresh dispatch had anchored. The
2.1.222 harness happens to prefix a resume with its own "The coordinator sent a message
while you were working:" line, which pushes an injected marker off line 1 by itself
(measured 2026-08-09) — that is a harness detail, not the defence.

Exit 0 + the path on stdout when a marker is found, exit 1 with empty stdout otherwise.
Every failure mode collapses to the same answer — no marker — because the caller
(hooks/review_record.py) treats "no marker" as "no coverage", never as an error.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MARKER = re.compile(r"^REPO: (/.+)$")


def _text(content: object) -> str:
    """Content is either a plain str or a list of blocks; both shapes occur.

    Measured over a snapshot of 1088 subagent transcripts on this machine (2026-08-11):
    the FIRST user message — the only one this module reads — is a `str` in 1088 of 1088.
    Later user messages carry lists of tool_result blocks (18,036) and assistant messages
    are lists in all 30,621 observed. So the list branch is not what this reader relies on;
    it is there because the shape is not guaranteed by anything. Any total here is a
    snapshot that moves in BOTH directions — transcripts are pruned as well as added — so
    re-measure rather than quoting it (method: docs/adr/0005-*.md).

    Blocks are joined with a NEWLINE, not empty: only line 1 may name a repo (ADR 0003),
    so a leading block carrying no `text` — a tool_result or an image — must not let the
    next block's text slide up into line 1. Every observed first user message is a plain
    str, so this costs nothing today and fails closed if that ever changes.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def repo_from_transcript(path: str) -> str | None:
    try:
        raw = Path(path).read_text(errors="replace")
    except OSError:
        return None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        message = obj.get("message")
        # `type: "attachment"` entries have no `message` at all (1823 such lines in the
        # 1088-transcript snapshot measured 2026-08-11).
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        body = _text(message.get("content"))
        first = body.splitlines()[0] if body else ""
        m = MARKER.match(first)
        return m.group(1) if m else None
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 1
    repo = repo_from_transcript(argv[1])
    if not repo:
        return 1
    print(repo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
