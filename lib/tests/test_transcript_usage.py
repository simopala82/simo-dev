#!/usr/bin/env python3
"""Tests for lib/transcript_usage.py."""
import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import transcript_usage as tu  # noqa: E402


def _line(mid, skill="", ts="2026-11-09T10:00:00.000Z", retry=None, **usage):
    rec = {"type": "assistant", "timestamp": ts, "message": {"id": mid, "usage": usage}}
    if skill:
        rec["attributionSkill"] = skill
    if retry is not None:
        rec["retryAttempt"] = retry
    return json.dumps(rec)


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def test_duplicate_message_ids_keep_the_last_usage(tmp_path):
    f = _write(tmp_path / "t.jsonl", [
        _line("m1", output_tokens=5, input_tokens=3),
        _line("m1", output_tokens=9, input_tokens=3),
        _line("m2", output_tokens=1),
    ])
    records, skipped = tu.read_usage(f)
    assert skipped == 0
    assert set(records) == {"m1", "m2"}
    assert tu.totals(records.values())["output_tokens"] == 10
    assert tu.totals(records.values())["input_tokens"] == 3


def test_all_four_categories_are_summed(tmp_path):
    f = _write(tmp_path / "t.jsonl", [_line("m1", input_tokens=1, cache_creation_input_tokens=2,
                                            cache_read_input_tokens=3, output_tokens=4)])
    records, _ = tu.read_usage(f)
    assert tu.totals(records.values()) == {"input_tokens": 1, "cache_creation_input_tokens": 2,
                                           "cache_read_input_tokens": 3, "output_tokens": 4}


def test_timestamp_and_retry_are_kept(tmp_path):
    f = _write(tmp_path / "t.jsonl", [_line("m1", skill="simo-dev:commit", retry=1, output_tokens=1)])
    rec = tu.read_usage(f)[0]["m1"]
    assert "skill" not in rec and rec["retry"] is True
    assert rec["ts"] == "2026-11-09T10:00:00.000Z"


def test_bad_lines_are_counted_as_skipped(tmp_path):
    f = _write(tmp_path / "t.jsonl", [
        "not json", "572", "[1, 2]",
        _line("m1", output_tokens=1.5),
        _line("m2", output_tokens=True),
        json.dumps({"message": {"usage": {"output_tokens": 1}}}),
        json.dumps({"message": "text"}),
        "",
    ])
    records, skipped = tu.read_usage(f)
    assert records == {}
    assert skipped == 6


def test_missing_usage_keys_count_as_zero(tmp_path):
    f = _write(tmp_path / "t.jsonl", [_line("m1", output_tokens=2)])
    assert tu.totals(tu.read_usage(f)[0].values())["cache_read_input_tokens"] == 0


def test_unreadable_path_returns_none(tmp_path):
    assert tu.read_usage(tmp_path / "missing.jsonl") is None


def test_transcripts_are_found_in_the_projects_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / ".claude" / "projects" / "-x"
    main = _write(proj / "sid1.jsonl", [_line("m1", output_tokens=1)])
    _write(proj / "sid1" / "subagents" / "agent-a.jsonl", [_line("s1", output_tokens=1)])
    _write(proj / "sid1" / "subagents" / "agent-b.jsonl", [_line("s2", output_tokens=1)])
    (proj / "sid1" / "subagents" / "agent-a.meta.json").write_text("{}")
    assert tu.find_main_transcript("sid1") == str(main)
    subs = tu.find_subagent_transcripts(str(main))
    assert [Path(p).name for p in subs] == ["agent-a.jsonl", "agent-b.jsonl"]
    assert tu.find_main_transcript("nope") is None


def _model_line(mid, model, cache_creation=None, **usage):
    if cache_creation is not None:
        usage["cache_creation"] = cache_creation
    return json.dumps({"type": "assistant", "timestamp": "2026-11-09T10:00:00Z",
                       "message": {"id": mid, "model": model, "usage": usage}})


def test_model_and_one_hour_share_are_kept(tmp_path):
    f = _write(tmp_path / "t.jsonl", [
        _model_line("m1", "claude-sonnet-5-5", cache_creation={"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 8},
                    cache_creation_input_tokens=10),
        _model_line("m2", 7, cache_creation={"ephemeral_1h_input_tokens": True}, output_tokens=1),
    ])
    records, _ = tu.read_usage(f)
    assert records["m1"]["model"] == "claude-sonnet-5-5" and records["m1"]["cache_1h"] == 8
    assert records["m2"]["model"] == "" and records["m2"]["cache_1h"] == 0


def _usage(i=0, cw=0, cr=0, out=0):
    return {"input_tokens": i, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": out}


def test_cost_prices_each_category_at_the_model_rate():
    got = tu.cost({"usage": _usage(1_000_000, 1_000_000, 1_000_000, 1_000_000), "model": "claude-opus-5-5", "cache_1h": 400_000})
    assert got == pytest.approx({"input_tokens": 4.0, "cache_creation_input_tokens": 0.6 * 5 + 0.4 * 8,
                                 "cache_read_input_tokens": 0.20, "output_tokens": 20.0})
    assert tu.dollars(got) == pytest.approx(4 + 6.2 + 0.2 + 20)


def test_cost_without_one_hour_share_uses_the_five_minute_rate():
    got = tu.cost({"usage": _usage(cw=1_000_000), "model": "claude-sonnet-5"})
    assert got["cache_creation_input_tokens"] == pytest.approx(2.50)


def test_one_hour_share_is_capped_at_the_cache_write_count():
    got = tu.cost({"usage": _usage(cw=10), "model": "claude-opus-5-5", "cache_1h": 50})
    assert got["cache_creation_input_tokens"] == pytest.approx(10 * 8 / 1e6)


def test_long_prompt_tier_counts_every_input_category():
    short = tu.cost({"usage": _usage(i=50_000, cr=50_000, out=1_000_000), "model": "claude-haiku-5-5"})
    long = tu.cost({"usage": _usage(i=50_000, cr=50_001, out=1_000_000), "model": "claude-haiku-5-5"})
    assert short["output_tokens"] == pytest.approx(0.50) and long["output_tokens"] == pytest.approx(2.50)


def test_model_id_strips_context_and_date_suffixes():
    assert tu.model_id("claude-opus-5-5[1m]") == "claude-opus-5-5"
    assert tu.model_id("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert tu.model_id(None) == ""


def test_unknown_model_is_priced_as_the_fallback_and_listed():
    rec = {"usage": _usage(out=1_000_000), "model": "some-new-model"}
    assert tu.cost(rec) == pytest.approx(tu.cost({**rec, "model": tu.FALLBACK_MODEL}))
    zero = {"usage": _usage(), "model": "<synthetic>"}
    none = {"usage": _usage(out=1), "model": ""}
    assert tu.unpriced([rec, zero, none, {"usage": _usage(out=1), "model": "claude-opus-5"}]) == ["(none)", "some-new-model"]


def test_fast_mode_is_kept_and_listed_as_not_priced_exactly(tmp_path):
    f = _write(tmp_path / "t.jsonl", [_model_line("m1", "claude-opus-5-5", output_tokens=1, speed="fast"),
                                      _model_line("m2", "claude-opus-5-5", output_tokens=1, speed="standard")])
    records, _ = tu.read_usage(f)
    assert records["m1"]["speed"] == "fast" and records["m2"]["speed"] == "standard"
    assert tu.unpriced(records.values()) == ["claude-opus-5-5 (fast)"]


def test_estimate_prices_bare_counts_at_the_fallback():
    assert tu.estimate({"output_tokens": 1_000_000}) == pytest.approx(
        {"input_tokens": 0.0, "cache_creation_input_tokens": 0.0, "cache_read_input_tokens": 0.0, "output_tokens": 20.0})
    assert tu.dollars(tu.zero_cost()) == 0


def test_parse_ts_accepts_z_and_naive_and_refuses_junk():
    assert tu.parse_ts("2026-11-09T10:00:00Z").isoformat() == "2026-11-09T10:00:00+00:00"
    assert tu.parse_ts("2026-11-09T10:00:00").isoformat() == "2026-11-09T10:00:00+00:00"
    assert tu.parse_ts("nope") is None and tu.parse_ts(5) is None and tu.parse_ts("") is None


def test_agent_type_reads_the_meta_file_next_to_the_transcript(tmp_path):
    t = _write(tmp_path / "agent-a.jsonl", [_line("m1", output_tokens=1)])
    assert tu.agent_type(t) == "agent"
    (tmp_path / "agent-a.meta.json").write_text(json.dumps({"agentType": "example:reviewer"}))
    assert tu.agent_type(t) == "example:reviewer"
    (tmp_path / "agent-a.meta.json").write_text("[1]")
    assert tu.agent_type(t) == "agent"


def test_launch_ts_is_the_earliest_parsable_record(tmp_path):
    t = _write(tmp_path / "agent-a.jsonl", [
        _line("m2", ts="2026-11-09T10:05:00.000Z", output_tokens=1),
        _line("m1", ts="2026-11-09T10:01:00.000Z", output_tokens=1),
        _line("m3", ts="junk", output_tokens=1),
    ])
    records, _ = tu.read_usage(t)
    assert tu.launch_ts(records) == "2026-11-09T10:01:00.000Z"
    assert tu.launch_ts({}) == ""
