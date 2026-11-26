#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read token usage out of Claude Code transcripts.

Main transcript: ~/.claude/projects/<enc>/<session-id>.jsonl.
Subagents:       ~/.claude/projects/<enc>/<session-id>/subagents/agent-*.jsonl.
A streamed reply is written as several records that share one `message.id`;
each carries the usage so far, so the last record per id wins (summing every
record overcounts). A record with no `message.id` falls back to its `uuid`.
Four categories are summed: input, cache creation, cache read and output.

The prices live here, one definition for the journal, `flow_telemetry.py attribute`
and the dashboard: a record is priced by its own `message.model`, its 1-hour cache-write
share at the 1h rate, an unknown model at FALLBACK_MODEL and named by `unpriced`. `agent_type` reads a subagent's `agentType` from the
`agent-*.meta.json` next to its transcript; `launch_ts` is a subagent's first
usage record, the moment the flow's step model ties it to.
"""
from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
FALLBACK_MODEL = "claude-opus-5-5"
# USD per million tokens: input, cache write 5m, cache write 1h, cache read, output.
# https://platform.claude.com/docs/en/about-claude/pricing, checked 2026-11-11.
_FABLE_5_2 = (8.0, 10.0, 16.0, 0.20, 40.0)
_FABLE_5_1 = (10.0, 12.5, 20.0, 0.25, 50.0)
_FABLE_5 = (10.0, 12.5, 20.0, 1.0, 50.0)
_OPUS_5 = (5.0, 6.25, 10.0, 0.50, 25.0)
_SONNET_4 = (3.0, 3.75, 6.0, 0.30, 15.0)
PRICES = {
    "claude-fable-5-2": _FABLE_5_2, "claude-mythos-5-2": _FABLE_5_2,
    "claude-fable-5-1": _FABLE_5_1, "claude-mythos-5-1": _FABLE_5_1,
    "claude-fable-5": _FABLE_5, "claude-mythos-5": _FABLE_5,
    "claude-opus-5-5": (4.0, 5.0, 8.0, 0.20, 20.0),
    "claude-opus-5": _OPUS_5, "claude-opus-4-8": _OPUS_5, "claude-opus-4-7": _OPUS_5,
    "claude-opus-4-6": _OPUS_5, "claude-opus-4-5": _OPUS_5,
    "claude-sonnet-5-5": (2.0, 2.5, 4.0, 0.10, 10.0),
    "claude-sonnet-5": (2.0, 2.5, 4.0, 0.20, 10.0),
    "claude-sonnet-4-6": _SONNET_4, "claude-sonnet-4-5": _SONNET_4,
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),
    "claude-haiku-4-5": (1.0, 1.25, 2.0, 0.10, 5.0),
}
# A request whose prompt (input + cache write + cache read) is over the threshold pays these rates.
LONG_PROMPT = {"claude-haiku-5-5": (100_000, (0.50, 0.625, 1.0, 0.05, 2.50))}
_SUFFIX = re.compile(r"(\[[^\]]*\]|-\d{8})$")


def model_id(model) -> str:
    """A model id without a context tag (`[1m]`) or a date snapshot suffix."""
    value = model.strip() if isinstance(model, str) else ""
    while (shorter := _SUFFIX.sub("", value)) != value:
        value = shorter
    return value


def zero_cost() -> dict[str, float]:
    return dict.fromkeys(KEYS, 0.0)


def _rates(model, counts: dict) -> tuple:
    mid = model_id(model)
    rates = PRICES.get(mid, PRICES[FALLBACK_MODEL])
    long = LONG_PROMPT.get(mid)
    prompt = counts["input_tokens"] + counts["cache_creation_input_tokens"] + counts["cache_read_input_tokens"]
    return long[1] if long and prompt > long[0] else rates


def cost(rec: dict) -> dict[str, float]:
    """One usage record in USD per category, at its own model's list price."""
    counts = {key: rec["usage"].get(key, 0) for key in KEYS}
    rates = _rates(rec.get("model"), counts)
    write = counts["cache_creation_input_tokens"]
    hour = min(max(rec.get("cache_1h", 0), 0), write)
    return {"input_tokens": counts["input_tokens"] * rates[0] / 1e6,
            "cache_creation_input_tokens": ((write - hour) * rates[1] + hour * rates[2]) / 1e6,
            "cache_read_input_tokens": counts["cache_read_input_tokens"] * rates[3] / 1e6,
            "output_tokens": counts["output_tokens"] * rates[4] / 1e6}


def estimate(counts: dict) -> dict[str, float]:
    """Counts with no model and no 1h share (an attribution event from before costs): FALLBACK_MODEL, 5m writes."""
    return cost({"usage": counts, "model": FALLBACK_MODEL})


def dollars(costs: dict) -> float:
    return sum(costs.get(key, 0.0) for key in KEYS)


def unpriced(records: Iterable[dict]) -> list[str]:
    """Records that spent tokens but are not priced exactly: a model with no PRICES entry (priced as
    FALLBACK_MODEL), or a `speed` other than standard such as fast mode (`<model> (fast)`, priced at
    standard rates). Either makes the figure an estimate below the bill, so it is never silent."""
    out = set()
    for r in records:
        if not any(r["usage"].values()):
            continue
        mid = model_id(r.get("model"))
        if mid not in PRICES:
            out.add(mid or "(none)")
        speed = r.get("speed") or "standard"
        if speed != "standard":
            out.add(f"{mid or '(none)'} ({speed})")
    return sorted(out)


def parse_ts(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _counts(usage: dict) -> dict | None:
    counts = {}
    for key in KEYS:
        value = usage.get(key)
        if value is None:
            value = 0
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        counts[key] = value
    return counts


def read_usage(path) -> tuple[dict[str, dict], int] | None:
    """Return ({message key: record}, skipped lines), or None if unreadable.

    Skipped: a line that is not JSON, JSON that is not an object, a usage block
    with a non-integer count (a bool included), or a usage record with no key.
    A record without a usage object is a normal record and is not counted.
    """
    records: dict[str, dict] = {}
    skipped = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if not isinstance(obj, dict):
                    skipped += 1
                    continue
                message = obj.get("message")
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                key = message.get("id") or obj.get("uuid")
                counts = _counts(usage)
                if not key or counts is None:
                    skipped += 1
                    continue
                retry = obj.get("retryAttempt")
                model = message.get("model")
                speed = usage.get("speed")
                split = usage.get("cache_creation")
                hour = split.get("ephemeral_1h_input_tokens") if isinstance(split, dict) else 0
                records[key] = {
                    "ts": obj.get("timestamp") or "",
                    "usage": counts,
                    "retry": isinstance(retry, int) and not isinstance(retry, bool) and retry > 0,
                    "model": model if isinstance(model, str) else "",
                    "speed": speed if isinstance(speed, str) else "",
                    "cache_1h": hour if isinstance(hour, int) and not isinstance(hour, bool) and hour > 0 else 0,
                }
    except OSError:
        return None
    return records, skipped


def totals(records: Iterable[dict]) -> dict[str, int]:
    out = {key: 0 for key in KEYS}
    for rec in records:
        for key in KEYS:
            out[key] += rec["usage"][key]
    return out


def find_main_transcript(session_id: str) -> str | None:
    home = os.path.expanduser("~")
    hits = sorted(glob.glob(f"{home}/.claude/projects/*/{glob.escape(session_id)}.jsonl"))
    return hits[0] if hits else None


def find_subagent_transcripts(main_path: str) -> list[str]:
    base = main_path[: -len(".jsonl")] if main_path.endswith(".jsonl") else main_path
    return sorted(glob.glob(f"{glob.escape(base)}/subagents/agent-*.jsonl"))


def agent_type(path) -> str:
    """A subagent's type, from the agent-*.meta.json next to its transcript; "agent" when unknown."""
    try:
        meta = json.loads(Path(str(path)[: -len(".jsonl")] + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "agent"
    value = meta.get("agentType") if isinstance(meta, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else "agent"


def launch_ts(records: dict) -> str:
    """When a subagent started spending: the earliest timestamp among its usage records."""
    stamps = [r["ts"] for r in records.values() if parse_ts(r.get("ts"))]
    return min(stamps, key=parse_ts) if stamps else ""
