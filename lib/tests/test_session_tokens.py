#!/usr/bin/env python3
"""Tests for lib/session_tokens.py (per-line parsing lives in test_transcript_usage.py)."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "session_tokens.py"
spec = importlib.util.spec_from_file_location("session_tokens", SCRIPT)
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def _line(mid, **usage):
    return json.dumps({"type": "assistant", "message": {"id": mid, "usage": usage}})


def _session(tmp_path, sid="sid1", main=(), subs=None):
    proj = tmp_path / ".claude" / "projects" / "-slug"
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text("\n".join(main) + "\n")
    for name, lines in (subs or {}).items():
        d = proj / sid / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("\n".join(lines) + "\n")


def test_no_session_id_exits_4_with_a_line(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["session_tokens.py"])
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert st.cli() == 4
    assert capsys.readouterr().out.startswith("main=0 subagent=na total=0 (no session id)")


def test_missing_main_transcript_exits_4(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["session_tokens.py", "nope"])
    assert st.cli() == 4
    assert "main transcript not found" in capsys.readouterr().out


def test_internal_error_exits_3_and_still_prints(monkeypatch, capsys):
    def boom(_):
        raise RuntimeError("x")

    monkeypatch.setattr(st, "find_main_transcript", boom)
    monkeypatch.setattr(sys, "argv", ["session_tokens.py", "sid"])
    assert st.cli() == 3
    assert "internal error" in capsys.readouterr().out


def test_main_only_sums_four_categories_and_dedupes(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _session(tmp_path, main=[_line("m1", input_tokens=1, output_tokens=2),
                             _line("m1", input_tokens=1, output_tokens=5, cache_read_input_tokens=10)])
    monkeypatch.setattr(sys, "argv", ["session_tokens.py", "sid1"])
    assert st.cli() == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("main=16 subagent=na total=16 (no subagent transcripts")
    assert "in=1 cache_write=0 cache_read=10 out=5" in out


def test_subagents_in_the_projects_layout_are_summed(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _session(tmp_path, main=[_line("m1", output_tokens=3)],
             subs={"agent-a.jsonl": [_line("a1", output_tokens=4)],
                   "agent-b.jsonl": [_line("b1", input_tokens=2), "junk"]})
    monkeypatch.setattr(sys, "argv", ["session_tokens.py", "sid1"])
    assert st.cli() == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("main=3 subagent=6 total=9 (2 subagent transcripts")
    assert "1 lines skipped" in out


def test_process_exit_status_reaches_the_shell(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_CODE_SESSION_ID": "", "HOME": str(tmp_path)})
    assert r.returncode == 4 and r.stdout.startswith("main=0")
