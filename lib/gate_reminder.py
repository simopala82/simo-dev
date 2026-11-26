#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Remind the user of a gate that still waits when the prompt cache is about to expire.

The prompt cache lives 60 minutes from the session's last API call; an answer that lands later
rewrites the whole context at the cache-write rate. `arm` (task-intake step 7.5, right before the
plan gate) starts a detached watcher. About 15 minutes before the cache expires the watcher sends
one desktop notification, unless the user wrote to the session in the meantime. No model turn is
involved: the watcher only reads the session's main transcript.

    gate_reminder.py arm --label TEXT [--session S]
    gate_reminder.py watch --transcript P --armed ISO --label TEXT     (the detached watcher)

`arm` prints one `Gate reminder set: answer by ~HH:MM …` line (local time) when a watcher started,
and nothing when it did not: no session id, no transcript for it, no notifier on this machine
(`osascript`, else `notify-send`), a session whose latest cache write is not 1h-only, or
SIMO_DEV_GATE_REMINDER=off.

The watcher stops without a notification when the user answered, when the cache already expired,
when the session writes 5-minute cache entries (nothing to keep warm for an hour), or 3 hours
after `arm`. A user message is a main-chain `user` record whose `origin.kind` is `human` — the
shape observed in transcripts of 2026-11; a record of another shape never counts as an answer.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript_usage  # noqa: E402

TTL = timedelta(minutes=60)
REMIND_AFTER = timedelta(minutes=45)
MAX_LIFE = timedelta(hours=3)


def scan(path, armed: datetime) -> dict:
    """What the transcript says now: the last API call, its prompt size and model, whether the
    session's latest cache write was a 1h one, and whether the user wrote after `armed`."""
    first_ts: dict[str, datetime] = {}
    snap = {"last_call": None, "prompt": 0, "model": "", "one_hour": True, "answered": False}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return snap
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("isSidechain"):
            continue
        ts = transcript_usage.parse_ts(rec.get("timestamp"))
        if ts is None:
            continue
        message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        usage = message.get("usage")
        if rec.get("type") == "assistant" and isinstance(usage, dict):
            written = usage.get("cache_creation_input_tokens") or 0
            prompt = (usage.get("input_tokens") or 0) + written + (usage.get("cache_read_input_tokens") or 0)
            if not prompt:
                continue  # a synthetic record (an error or an interrupt): no request reached the cache
            # A streamed reply is several records sharing one message id: the first one is the
            # closest to the request, which is what refreshed the cache.
            key = message.get("id") or rec.get("uuid") or ""
            snap["last_call"] = first_ts.setdefault(key, ts)
            snap["prompt"] = prompt
            snap["model"] = message.get("model") or ""
            split = usage.get("cache_creation")
            if written and isinstance(split, dict):
                snap["one_hour"] = bool(split.get("ephemeral_1h_input_tokens")) and not split.get("ephemeral_5m_input_tokens")
        elif rec.get("type") == "user" and ts > armed:
            origin = rec.get("origin")
            if isinstance(origin, dict) and origin.get("kind") == "human":
                snap["answered"] = True
    return snap


def decide(now: datetime, armed: datetime, snap: dict) -> tuple[str, object]:
    """("exit", reason) | ("sleep", seconds) | ("notify", the time the cache expires)."""
    if snap["answered"]:
        return "exit", "answered"
    if now - armed > MAX_LIFE:
        return "exit", "stale"
    if not snap["one_hour"]:
        return "exit", "no 1h cache"
    last = snap["last_call"] or armed
    if now >= last + TTL:
        return "exit", "expired"
    due = last + REMIND_AFTER
    if now < due:
        return "sleep", (due - now).total_seconds()
    return "notify", last + TTL


def rewrite_cost(snap: dict) -> float:
    """USD to write the last call's whole prompt to the 1h cache, at its model's list price."""
    rec = {"usage": {"cache_creation_input_tokens": snap["prompt"]}, "cache_1h": snap["prompt"], "model": snap["model"]}
    return transcript_usage.cost(rec)["cache_creation_input_tokens"]


def message(label: str, expires: datetime, snap: dict) -> str:
    return (f"{label}: still waiting. Answer by {expires.astimezone():%H:%M} to keep the prompt cache "
            f"(a later answer rewrites up to ${rewrite_cost(snap):.2f}).")


def notifier(title: str, body: str) -> list[str] | None:
    """The command that shows a desktop notification here, or None when there is none."""
    if shutil.which("osascript"):
        return ["osascript", "-e", "on run argv", "-e",
                "display notification (item 1 of argv) with title (item 2 of argv)", "-e", "end run", body, title]
    if shutil.which("notify-send"):
        return ["notify-send", "--", title, body]
    return None


def watch(transcript: str, armed: datetime, label: str, now=lambda: datetime.now(timezone.utc),
          sleep=time.sleep, run=subprocess.run) -> str:
    while True:
        snap = scan(transcript, armed)
        action, value = decide(now(), armed, snap)
        if action == "sleep":
            sleep(max(value, 1.0))
            continue
        if action == "notify":
            cmd = notifier("simo-dev", message(label, value, snap))
            if cmd:
                run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return "notified"
        return value


def arm(session: str, label: str, spawn=subprocess.Popen) -> str:
    """Start the watcher; return the line to print, empty when no watcher started."""
    if os.environ.get("SIMO_DEV_GATE_REMINDER", "").strip().lower() == "off" or not session:
        return ""
    transcript = transcript_usage.find_main_transcript(session)
    armed = datetime.now(timezone.utc)
    if not transcript or notifier("", "") is None or not scan(transcript, armed)["one_hour"]:
        return ""
    spawn([sys.executable, str(Path(__file__).resolve()), "watch", "--transcript", transcript,
           "--armed", armed.isoformat(), "--label", label],
          start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return (f"Gate reminder set: answer by ~{(armed + TTL).astimezone():%H:%M} to keep the prompt cache "
            f"(desktop notification at ~{(armed + REMIND_AFTER).astimezone():%H:%M} if still waiting).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("arm")
    p.add_argument("--label", required=True)
    p.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    p = sub.add_parser("watch")
    p.add_argument("--transcript", required=True)
    p.add_argument("--armed", required=True)
    p.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "arm":
        line = arm(args.session, args.label)
        if line:
            print(line)
        return 0
    armed = transcript_usage.parse_ts(args.armed)
    if armed is None:
        return 2
    watch(args.transcript, armed, args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
