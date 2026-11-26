#!/usr/bin/env python3
"""Fake `claude` for step_replay tests: writes a plan (or, for plan-execute, edits code and adds a test),
a transcript with one critic subagent, prints CLI JSON."""
import json
import os
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
args = sys.argv[1:]
sid = args[args.index("--session-id") + 1]
stdin = sys.stdin.read()
Path(os.environ["FAKE_CLAUDE_LOG"]).write_text(json.dumps({
    "argv": args, "stdin": stdin, "cwd": os.getcwd(), "acceptance_visible": Path(".replay-acceptance").exists(),
    "env": {k: v for k, v in os.environ.items() if k.startswith(("SIMO_DEV_", "CLAUDE", "ANTHROPIC_")) or k == "TMPDIR"},
    "settings": json.loads(Path(args[args.index("--settings") + 1]).read_text()),
    "strategy": json.loads(Path(os.environ["SIMO_DEV_MODEL_STRATEGY_CONFIG"]).read_text()),
}))
if os.environ.get("FAKE_CLAUDE_CHILD"):
    import subprocess
    child = subprocess.Popen(["sleep", "60"])
    Path(os.environ["FAKE_CLAUDE_CHILD"]).write_text(str(child.pid))
if os.environ.get("FAKE_CLAUDE_SLEEP"):
    time.sleep(float(os.environ["FAKE_CLAUDE_SLEEP"]))
if os.environ.get("FAKE_CLAUDE_RC"):
    print(json.dumps({"is_error": True, "result": "boom"}))
    sys.exit(int(os.environ["FAKE_CLAUDE_RC"]))
reviewer_text = None
if "simo-dev:review-loop" in stdin:
    # A reviewer that flags the seeded line and one unrelated nit, then (optionally) the loop fixes the bug.
    reviewer_text = os.environ.get("FAKE_CLAUDE_FINDINGS", "- [high] `src/app.py:3` — subtracts instead of adding\n"
                                   "[low] src/other.py:9 — nit\nverdict: mergeable-with-fixes — one bug")
    if os.environ.get("FAKE_CLAUDE_FIX"):
        Path("src/app.py").write_text("x = 1\n\ndef add(a, b):\n    return a + b\n")
    result = os.environ.get("FAKE_CLAUDE_RESULT", "Review loop report.\nDONE")
elif "simo-dev:mr-followup" in stdin:
    import subprocess
    gh = {name: subprocess.run(["gh", *cmd], capture_output=True, text=True) for name, cmd in (
        ("version", ["--version"]), ("view", ["pr", "view", "--json", "number,headRefName,headRefOid", "-q", ".headRefName"]),
        ("threads", ["api", "graphql", "-f", "query=query { reviewThreads }", "-F", "num=7"]),
        ("checks", ["pr", "checks", "7", "--json", "name,state,bucket"]), ("log", ["run", "view", "1", "--log-failed"]))}
    Path(os.environ["FAKE_CLAUDE_GH_LOG"]).write_text(json.dumps({k: [r.returncode, r.stdout] for k, r in gh.items()}))
    reviewer_text = "[medium] src/app.py:3 — wrong\nverdict: not-mergeable — bug"
    if not os.environ.get("FAKE_CLAUDE_NO_FIX"):
        Path("src/app.py").write_text("x = 1\n\ndef add(a, b):\n    return a + b\n")
        ident = ["-c", "user.name=f", "-c", "user.email=f@example.invalid"]
        subprocess.run(["git", *ident, "commit", "-qam", "fix: add"], check=True)
        if not os.environ.get("FAKE_CLAUDE_NO_PUSH"):
            subprocess.run(["git", "push", "-q", "origin", "HEAD"], check=True)
        subprocess.run(["gh", "api", "repos/replay/x/pulls/comments/1/replies", "-f", "body=Fixed"], check=True, capture_output=True)
    result = os.environ.get("FAKE_CLAUDE_RESULT", "Final report.\nDONE")
elif "simo-dev:plan-execute" in stdin:
    Path("src/app.py").write_text(os.environ.get("FAKE_CLAUDE_APP", "x = 2\n"))
    Path("tests/test_new.py").write_text("def test_new():\n    assert True\n")
    if os.environ.get("FAKE_CLAUDE_BREAK_SUITE"):
        Path("tests/test_app.py").write_text("def test_x():\n    assert False\n")
    result = os.environ.get("FAKE_CLAUDE_RESULT", "Report: all tasks done.\nDONE")
else:
    plan = Path("docs/plans/2026-01-01-sample.md")
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# Plan\n\n### Task 1: x\n\n**Files:**\n- Modify: `src/app.py`\n")
    result = "done\nPLAN: docs/plans/2026-01-01-sample.md"
main = Path(os.environ["HOME"]) / ".claude" / "projects" / "-fake" / f"{sid}.jsonl"
(main.with_suffix("") / "subagents").mkdir(parents=True, exist_ok=True)
usage = {"input_tokens": int(os.environ.get("FAKE_CLAUDE_INPUT", "1000")), "output_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
main.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}}) + "\n")
sub = main.with_suffix("") / "subagents" / "agent-a1.jsonl"
sub.write_text(json.dumps({"type": "assistant", "message": {"id": "s1", "model": "claude-opus-5-5", "usage": usage}}) + "\n")
sub.with_suffix(".meta.json").write_text(json.dumps({"agentType": "simo-dev:plan-reviewer"}))
if reviewer_text is not None:
    rev = main.with_suffix("") / "subagents" / "agent-r1.jsonl"
    rev.write_text(json.dumps({"type": "assistant", "message": {"id": "r1", "role": "assistant", "model": "claude-opus-5-5",
                                                                "usage": usage, "content": [{"type": "text", "text": reviewer_text}]}}) + "\n")
    rev.with_suffix(".meta.json").write_text(json.dumps({"agentType": "simo-dev:code-reviewer"}))
denials = [{"tool_name": "Bash"}] * int(os.environ.get("FAKE_CLAUDE_DENIALS", "0"))
print(json.dumps({"total_cost_usd": 0.01, "modelUsage": {"claude-opus-5-5": {"costUSD": 0.01}}, "permission_denials": denials,
                  "num_turns": 3, "is_error": False, "result": result}))
