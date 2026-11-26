#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Local HTML dashboard of the simo-dev flow events.

Reads the task files of lib/flow_telemetry.py, the sessions' transcripts
(lib/transcript_usage.py) and the tracker config's browse_url (lib/tracker_config.py)
read-only, and writes one self-contained HTML file in views (tabs with the script: Overview,
Tasks): summary cards, the cost of every
step summed by skill across the tasks (step_totals), an overview row
per task with its estimated $ cost as a bar stacked by token category (transcript_usage.PRICES,
each record at its own model's price) and its status (running, stale, or done with the
outcome's result; set_status) and running tasks first, then stale, then done (sortable,
filterable by text, status and period, paged PAGE_SIZE tasks at a time, reloaded on an
auto-refresh the viewer picks (keeping that view), and groupable by status, by repo, by session, or by task
tree with a small inline script the page
does not need), then per task a collapsible section with a timeline (steps, subagents,
human waits, gates, retries), the subagents and gates listed in clear, the tokens of
each step, and links to its parent task and follow-ups. The task tree comes from the
`task_link` events (`flow_telemetry.py start --parent-task`).

What no event records is derived from the transcripts at render time:
- a step (flow_telemetry.step_marks: a flow step's step_start, a retry of another flow
  step, or commit reopened after its pre-flight review-loop) ends at
  the next step's start in any session, else the outcome, else the last activity;
- subagents: subagents/agent-*.jsonl (first/last timestamp) + agent-*.meta.json;
- human waits: a gap over WAIT_MIN_S between the last main-thread assistant
  record and the next real user turn (not a tool_result unless it answers an
  AskUserQuestion; tool-permission prompts are not seen; not isMeta, not a
  background-task notification: `origin.kind` other than human, not a side-session
  note: no origin and text starting with a system-reminder tag, not a compaction
  summary, not a turn whose `turnOrigin` is set to anything but human) — an estimate;
- tokens per step: usage records (deduped by message.id) split by
  flow_telemetry.split_by_step (a subagent on its launch step, `(none)` before the
  task's first step); without any transcript, the last `attribution` event's
  `by_step`, else its old per-skill `by_skill`, is shown;
- the task name: the last `custom-title` record of the session that joined the
  task last (on a hand-off, the child session), else of an earlier session.
Parsed sessions are cached across runs in ~/.claude/state/dev-flow-dashboard-cache.json
($SIMO_DEV_DASHBOARD_CACHE), keyed by the size and mtime of every transcript file read: a
finished task is parsed once, so frequent regeneration only re-reads the live sessions.
session-*.jsonl lines (minus the task-intake line the Skill hook writes seconds before
`start` mints the task) make each of their sessions a task-like `untracked` run (untracked_runs).

    flow_dashboard.py [--out PATH] [--task TASK_ID] [--no-reconcile]
Before a full build (not with --task, nor --no-reconcile), `flow_telemetry.reconcile` closes the
open tasks whose MR/PR was merged outside mr-followup's post-merge step (one forge query per open
task every RECONCILE_EVERY_S) and the journal is re-rendered when it closed any: the only writes
to the task files and the journal, besides reconcile's own reconcile-checked.json and its .lock.
rc 0 written, 2 refused (unknown task, unwritable output).
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_telemetry as ft  # noqa: E402
import tracker_config as tc  # noqa: E402
import transcript_usage as tu  # noqa: E402

_usd = ft._usd
WAIT_MIN_S = 60
CACHE_VERSION = 5  # bump whenever marks(), _to_disk() or the read_usage record shape changes: a matching fingerprint alone would reuse stale entries
INTAKE_DEDUP_S = 60
INTAKE = "simo-dev:task-intake"
ASK_TOOLS = {"AskUserQuestion"}


def cache_pct(counts: dict) -> float | None:
    seen = sum(counts.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return 100.0 * counts.get("cache_read_input_tokens", 0) / seen if seen else None


def ticket_url(key: str, template: str) -> str:
    """The tracker config's browse_url template for one ticket; {repo_url} is not known here."""
    if not key or not template or "{repo_url}" in template:
        return ""
    tail = key.rsplit("-", 1)[-1]
    number = tail if tail.isdigit() else ""
    if "{number}" in template and not number:
        return ""
    url = template.replace("{key}", quote(key, safe="-_.")).replace("{number}", number)
    return url if url.startswith(("https://", "http://")) else ""


def browse_template(key: str, memo: dict | None = None) -> str:
    """Read once per prefix per run (`memo`), so a broken config warns once per prefix, not once per task."""
    if not key:
        return ""
    prefix = key.rsplit("-", 1)[0]
    memo = {} if memo is None else memo
    if prefix not in memo:
        try:
            memo[prefix] = tc.get_field(prefix, "browse_url", tc.config_path())
        except (OSError, ValueError, AttributeError, SystemExit):
            memo[prefix] = ""
    return memo[prefix]


def _zero() -> dict:
    return dict.fromkeys(tu.KEYS, 0)


def _add(a: dict, b: dict) -> dict:
    return {key: a.get(key, 0) + b.get(key, 0) for key in tu.KEYS}


def _ts(value) -> datetime | None:
    return ft._parse_ts(value) if isinstance(value, str) else None


def load_tasks(directory: Path) -> list[tuple[str, list[dict]]]:
    tasks = []
    for path in directory.glob("*.jsonl"):
        if path.name.startswith("session-"):
            continue
        events = sorted(ft.read_events(path), key=lambda e: str(e.get("ts", "")))
        if events:
            tasks.append((path.stem, events))
    return sorted(tasks, key=lambda t: (str(t[1][0].get("ts", "")), t[0]))


def load_orphans(directory: Path, tasks: list[tuple[str, list[dict]]]) -> list[dict]:
    starts: dict[str, list[datetime]] = {}
    for _, events in tasks:
        for e in events:
            ts = _ts(e.get("ts"))
            if e.get("type") == "step_start" and e.get("skill") == INTAKE and ts:
                starts.setdefault(e.get("session", ""), []).append(ts)
    out = []
    for path in directory.glob("session-*.jsonl"):
        for e in ft.read_events(path):
            if e.get("type") != "step_start":
                continue
            ts = _ts(e.get("ts"))
            absorbed = e.get("skill") == INTAKE and ts and any(
                timedelta(0) <= t - ts <= timedelta(seconds=INTAKE_DEDUP_S)
                for t in starts.get(e.get("session", ""), []))
            if not absorbed:
                out.append(e)
    return sorted(out, key=lambda e: str(e.get("ts", "")))


def _lines(path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        pass
    return out


def _human_turn(obj: dict) -> bool:
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain") or obj.get("isCompactSummary"):
        return False
    origin = obj.get("origin")
    if isinstance(origin, dict) and origin.get("kind") != "human":
        return False
    if obj.get("turnOrigin") not in (None, "human"):
        return False
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if origin is None and isinstance(content, str) and content.lstrip().startswith("<system-reminder>"):
        return False
    if isinstance(content, list):
        return not any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)
    return isinstance(content, str)


def _content(obj: dict) -> list:
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []


def _answers_question(obj: dict, asks: set) -> bool:
    """A tool_result that answers a question tool the user replies to (a gate)."""
    if obj.get("type") != "user" or obj.get("isSidechain"):
        return False
    return any(c.get("type") == "tool_result" and c.get("tool_use_id") in asks for c in _content(obj))


def marks(objs: list[dict]) -> list[list[str]]:
    """One [timestamp, kind] per timed main-transcript record — all waits() and the last
    activity need, and what the disk cache keeps of the records besides the session title
    (never message text): kind `a` = a main-thread reply, `h` = a human turn or the answer
    to a question tool, `o` = anything else."""
    out, asks = [], set()
    for obj in objs:
        stamp = obj.get("timestamp")
        if not _ts(stamp):
            continue
        if obj.get("type") == "assistant" and not obj.get("isSidechain"):
            asks |= {c.get("id") for c in _content(obj) if c.get("type") == "tool_use" and c.get("name") in ASK_TOOLS}
            out.append([stamp, "a"])
        elif _human_turn(obj) or _answers_question(obj, asks):
            out.append([stamp, "h"])
        else:
            out.append([stamp, "o"])
    return out


def session_title(objs: list[dict]) -> str:
    """The session's name as the app shows it: the last `custom-title` record (no timestamp,
    so a session that hosted several tasks gives each of them its latest name)."""
    title = ""
    for obj in objs:
        if obj.get("type") == "custom-title" and isinstance(obj.get("customTitle"), str) and obj["customTitle"].strip():
            title = obj["customTitle"].strip()
    return title


def waits(rows: list[list[str]]) -> list[tuple[datetime, datetime]]:
    out, last_assistant = [], None
    for stamp, kind in rows:
        ts = _ts(stamp)
        if not ts:
            continue
        if kind == "a":
            last_assistant = ts
        elif kind == "h":
            if last_assistant and (ts - last_assistant).total_seconds() > WAIT_MIN_S:
                out.append((last_assistant, ts))
            last_assistant = None
    return out


def subagent(path: str) -> dict | None:
    times = [t for t in (_ts(o.get("timestamp")) for o in _lines(path)) if t]
    if not times:
        return None
    try:
        meta = json.loads(Path(path[: -len(".jsonl")] + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return {"type": tu.agent_type(path), "description": str(meta.get("description") or ""),
            "start": min(times), "end": max(times)}


def _fingerprint(main: str, agents: list[str]) -> list:
    """Size and mtime of every file a session's parse reads: unchanged files, same parse."""
    out = []
    for path in [main, *agents, *(p[: -len(".jsonl")] + ".meta.json" for p in agents)]:
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append([path, st.st_size, st.st_mtime_ns])
    return out


def _to_disk(raw: dict) -> dict:
    return {"marks": raw["marks"], "title": raw.get("title", ""), "records": raw["records"],
            "subagents": [{**s, "start": s["start"].isoformat(), "end": s["end"].isoformat()} for s in raw["subagents"]]}


def _from_disk(data: dict) -> dict:
    rows, records, title = data["marks"], data["records"], data.get("title", "")
    if (not isinstance(rows, list)
            or not isinstance(title, str)
            or not all(isinstance(m, list) and len(m) == 2 and isinstance(m[0], str) and m[1] in ("a", "h", "o") for m in rows)
            or not isinstance(records, dict)
            or not all(isinstance(r, dict) and isinstance(r.get("usage"), dict) and isinstance(r.get("agent", ""), str)
                       and isinstance(r.get("launch", ""), str) and isinstance(r.get("model", ""), str)
                       and isinstance(r.get("speed", ""), str)
                       and isinstance(r.get("cache_1h", 0), int) and not isinstance(r.get("cache_1h", 0), bool)
                       for r in records.values())):
        raise ValueError("bad cache entry")
    subs = []
    for s in data["subagents"]:
        start, end = _ts(s["start"]), _ts(s["end"])
        if not start or not end:
            raise ValueError("bad subagent span")
        subs.append({"type": str(s["type"]), "description": str(s["description"]), "start": start, "end": end})
    return {"marks": rows, "title": title, "records": records, "subagents": subs}


def read_session(sid: str, disk: dict | None = None) -> dict | None:
    """One session's transcripts, parsed once per run (build() shares it across tasks) and,
    with `disk`, once across runs: an entry whose fingerprint still matches is reused, and
    one whose transcript Claude Code has since pruned is the only copy left, so it is used."""
    hit = (disk or {}).get(sid)
    main = tu.find_main_transcript(sid)
    if not main:
        try:
            return _from_disk(hit["data"]) if isinstance(hit, dict) else None
        except (KeyError, TypeError, ValueError):
            return None
    agents = tu.find_subagent_transcripts(main)
    key = _fingerprint(main, agents)
    if isinstance(hit, dict) and hit.get("key") == key:
        try:
            return _from_disk(hit["data"])
        except (KeyError, TypeError, ValueError):
            pass
    records: dict[str, dict] = {}
    for path in [main, *agents]:
        got = tu.read_usage(path)
        if not got:
            continue
        agent = "" if path == main else tu.agent_type(path)
        launch = tu.launch_ts(got[0]) if agent else ""
        records.update({k: {"ts": r["ts"], "usage": r["usage"], "agent": agent, "launch": launch,  # all the page reads
                            "model": r["model"], "cache_1h": r["cache_1h"], "speed": r["speed"]}
                        for k, r in got[0].items()})
    objs = _lines(main)
    raw = {"marks": marks(objs), "title": session_title(objs), "records": records,
           "subagents": [s for s in (subagent(p) for p in agents) if s]}
    if disk is not None:
        disk[sid] = {"key": key, "data": _to_disk(raw)}
    return raw


class SessionStore(dict):
    """Parsed sessions of one run, keyed by session id; `disk` is the cross-run cache."""

    def __init__(self, disk: dict | None = None):
        super().__init__()
        self.disk = disk


def load_disk_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION or not isinstance(data.get("sessions"), dict):
        return {}
    return data["sessions"]


def save_disk_cache(path: Path, sessions: dict) -> None:
    """Best-effort: a cache that cannot be written only costs a full parse next time."""
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"version": CACHE_VERSION, "sessions": sessions}), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"flow_dashboard: cache not written: {exc}", file=sys.stderr)


def session_activity(sessions: list[str], until: dict | None = None, cache: dict | None = None,
                     owned: dict | None = None) -> dict:
    """Keep only what the task owns in each session. `owned` maps a session to the spans
    the task owns there (see owned_spans); `until` caps a closed task's session at its own
    last event, as flow_telemetry.attribute does. Waits are found on the whole session,
    then clipped to the owned spans, so a gap is never bridged across another task."""
    until = until or {}
    owned = owned or {}
    cache = {} if cache is None else cache
    records: dict[str, dict] = {}
    subs, spans, times = [], [], []
    titles: dict[str, str] = {}
    for sid in sessions:
        if sid not in cache:
            cache[sid] = read_session(sid, getattr(cache, "disk", None))
        raw = cache[sid]
        if not raw:
            continue
        titles[sid] = raw.get("title", "")
        hi = until.get(sid)
        mine = owned.get(sid, [(datetime.min.replace(tzinfo=timezone.utc), None)])

        def keep(ts) -> bool:
            if ts is None or (hi is not None and ts > hi):
                return False
            return any(lo <= ts and (end is None or ts < end) for lo, end in mine)

        times += [t for t in (_ts(m[0]) for m in raw["marks"]) if keep(t)]
        for a, b in waits(raw["marks"]):
            for lo, end in mine:
                cb = min(x for x in (b, end, hi) if x is not None)
                ca = max(a, lo)
                if cb > ca:
                    spans.append((ca, cb))
        records.update({k: {**r, "sid": sid} for k, r in raw["records"].items() if keep(_ts(r.get("ts")))})
        subs += [s for s in raw["subagents"] if keep(s["start"])]
    return {"records": records, "subagents": subs, "waits": spans, "last": max(times) if times else None,
            "titles": titles}


def owned_spans(tasks: list[tuple[str, list[dict]]]) -> dict[str, dict[str, list[tuple]]]:
    """Per task and session: the spans of time that task owns in that session. A span
    opens at a task's event when the session was not already on that task, and closes at
    the next event of another task in the session, or at the task's own handoff gate
    (the session released it); an event after that reopens one (a rebind)."""
    by_session: dict[str, list[tuple]] = {}
    for task_id, events in tasks:
        for e in events:
            sid, t = e.get("session"), _ts(e.get("ts"))
            if sid and t:
                released = e.get("type") == "gate" and e.get("gate") == "handoff"
                by_session.setdefault(sid, []).append((t, task_id, released))
    out: dict[str, dict[str, list[tuple]]] = {task_id: {} for task_id, _ in tasks}
    for sid, rows in by_session.items():
        owner, lo = None, None
        for t, task_id, released in sorted(rows, key=lambda r: (r[0], r[1])):
            if task_id != owner:
                if owner is not None:
                    out[owner].setdefault(sid, []).append((lo, t))
                owner, lo = task_id, t
            if released:
                out[owner].setdefault(sid, []).append((lo, t))
                owner, lo = None, None
        if owner is not None:
            out[owner].setdefault(sid, []).append((lo, None))
    return out


def _rounds(events: list[dict], skill: str) -> int:
    runs = sum(1 for e in events if e.get("type") == "step_start" and e.get("skill") == skill)
    return runs + sum(1 for e in events if e.get("type") == "retry" and e.get("skill") == skill)


def _overlaps(start, end, lo, hi) -> bool:
    return start <= hi and end >= lo


def _union_s(spans: list[tuple[datetime, datetime]]) -> float:
    """Seconds covered by the spans, overlaps counted once (two sessions idle together)."""
    total, cur_a, cur_b = 0.0, None, None
    for a, b in sorted(spans):
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return total


def _parent(events: list[dict]) -> str:
    links = [e for e in events if e.get("type") == "task_link" and e.get("parent")]
    return str(links[-1]["parent"]) if links else ""


def task_tree(parents: dict[str, str], order: list[str]) -> dict[str, dict]:
    """Per task: tree root, depth, DFS position, children (in `order`) and resolved parent.
    A parent that is no known task, or a parent chain that loops, leaves the task a root."""
    known = set(order)

    def cyclic(tid: str) -> bool:
        seen, cur = set(), tid
        while cur in known:
            if cur in seen:
                return True
            seen.add(cur)
            cur = parents.get(cur, "")
        return False

    parent = {t: parents[t] for t in order if parents.get(t) in known and not cyclic(t)}
    children: dict[str, list[str]] = {}
    for t in order:
        if t in parent:
            children.setdefault(parent[t], []).append(t)
    out: dict[str, dict] = {}

    def walk(t: str, root: str, depth: int) -> None:
        out[t] = {"root": root, "depth": depth, "order": len(out), "children": children.get(t, []),
                  "parent": parent.get(t, "")}
        for c in children.get(t, []):
            walk(c, root, depth + 1)

    for t in order:
        if t not in parent:
            walk(t, t, 0)
    return out


def _counts_ok(counts) -> bool:
    return isinstance(counts, dict) and all(isinstance(counts.get(k, 0), int) and not isinstance(counts.get(k, 0), bool)
                                            for k in tu.KEYS)


def _costs_ok(costs) -> bool:
    return isinstance(costs, dict) and all(k in costs and isinstance(costs[k], (int, float)) and not isinstance(costs[k], bool)
                                           for k in tu.KEYS)


def _steps_from(rows, end) -> tuple[list[dict], dict, dict, bool]:
    """by_step rows (split_by_step, or an attribution event's) as timed steps: a step ends at the next
    step's start in any session, the last at `end`; `(none)` rows sum into the second and third
    values (tokens, cost). A row with no `cost` (an event from before costs) is priced at the
    fallback and makes the fourth value True; an agent's missing cost does not, it is a breakdown."""
    unattributed, unattributed_cost, estimated, steps = _zero(), tu.zero_cost(), False, []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not _counts_ok(row.get("tokens")):
            continue
        tokens = _add(_zero(), row["tokens"])
        if _costs_ok(row.get("cost")):
            row_cost = _add(tu.zero_cost(), row["cost"])
        else:
            row_cost, estimated = tu.estimate(tokens), True
        start = _ts(row.get("start"))
        if row.get("skill") == ft.NONE or not start:
            unattributed = _add(unattributed, tokens)
            unattributed_cost = _add(unattributed_cost, row_cost)
            continue
        agents = row.get("agents") if isinstance(row.get("agents"), dict) else {}
        agent_costs = row.get("agent_costs") if isinstance(row.get("agent_costs"), dict) else {}
        agents = {str(k): _add(_zero(), v) for k, v in agents.items() if _counts_ok(v)}
        steps.append({"skill": str(row.get("skill") or "?"), "session": str(row.get("session") or ""), "start": start,
                      "tokens": tokens, "agents": agents, "cost_split": row_cost, "cost": tu.dollars(row_cost),
                      "agent_costs": {k: _add(tu.zero_cost(), agent_costs[k]) if _costs_ok(agent_costs.get(k)) else tu.estimate(v)
                                      for k, v in agents.items()}})
    steps.sort(key=lambda s: s["start"])
    for i, step in enumerate(steps):
        nxt = steps[i + 1]["start"] if i + 1 < len(steps) else end
        step["end"] = max(nxt, step["start"]) if nxt else step["start"]
    return steps, unattributed, unattributed_cost, estimated


def _task_until(timed: list[tuple], outcomes: list[tuple]) -> dict[str, datetime]:
    """A closed task's session stops at the task's own last event there, never past the outcome
    (as flow_telemetry.attribute does); an open task has no cap."""
    if not outcomes:
        return {}
    last_event: dict[str, datetime] = {}
    for t, e in timed:
        sid = e.get("session")
        if sid and (sid not in last_event or t > last_event[sid]):
            last_event[sid] = t
    return {sid: min(t, outcomes[-1][0]) for sid, t in last_event.items()}


def task_spans(tasks: list[tuple[str, list[dict]]], bounds: dict) -> dict[str, list[tuple]]:
    """Per session, the (start, end, cap) of every span a task owns there: owned_spans, a closed task
    capped by _task_until (end exclusive, cap inclusive, None = open)."""
    taken: dict[str, list[tuple]] = {}
    for task_id, events in tasks:
        timed = [(t, e) for t, e in ((_ts(e.get("ts")), e) for e in events) if t]
        until = _task_until(timed, [(t, e) for t, e in timed if e.get("type") == "outcome"])
        for sid, spans in bounds.get(task_id, {}).items():
            taken.setdefault(sid, []).extend((lo, end, until.get(sid)) for lo, end in spans)
    return taken


def free_spans(taken: list[tuple]) -> list[tuple]:
    """The complement of a session's task spans, as session_activity's owned spans (end exclusive)."""
    lo_all = datetime.min.replace(tzinfo=timezone.utc)
    busy = sorted((lo, min((x for x in (end, cap) if x is not None), default=None)) for lo, end, cap in taken)
    free, cursor = [], lo_all
    for lo, end in busy:
        if cursor is None:
            break
        if lo > cursor:
            free.append((cursor, lo))
        cursor = None if end is None else max(cursor, end)
    if cursor is not None:
        free.append((cursor, None))
    return free


def untracked_runs(tasks: list[tuple[str, list[dict]]], bounds: dict, orphans: list[dict], cache: dict) -> list[dict]:
    """A session that ran simo-dev skills with no task ID (no work step to adopt it, skills after its task
    closed, or a session from before adoption) as a task model of its own, status `untracked`: its steps are its session-*.jsonl
    step_start events and its tokens those outside every task's spans, so nothing is counted twice."""
    taken = task_spans(tasks, bounds)
    by_sid: dict[str, list[dict]] = {}
    for o in orphans:
        if o.get("session"):
            by_sid.setdefault(str(o["session"]), []).append(o)
    out = []
    for sid, events in by_sid.items():
        m = build_task(f"session-{sid}", events, {sid: free_spans(taken.get(sid, []))}, cache)
        m.update(untracked=True, closed=False, result="untracked")
        out.append(m)
    return out


def build_task(task_id: str, events: list[dict], owned: dict | None = None, cache: dict | None = None) -> dict:
    timed = [(t, e) for t, e in ((_ts(e.get("ts")), e) for e in events) if t]
    sessions = sorted({e["session"] for _, e in timed if e.get("session")})
    outcomes = [(t, e) for t, e in timed if e.get("type") == "outcome"]
    until = _task_until(timed, outcomes)
    if owned is None:
        owned = owned_spans([(task_id, events)])[task_id]
    act = session_activity(sessions, until, cache, owned)
    bound: dict = {}
    for _, e in timed:
        if e.get("type") == "task_bound":
            bound.update({k: str(e[k]) for k in ("ticket", "branch", "repo") if e.get(k) is not None})
    joined: dict[str, datetime] = {}
    for t, e in timed:
        if e.get("session"):
            joined[e["session"]] = min(joined.get(e["session"], t), t)  # load_tasks sorts by ts string, not datetime
    by_join = sorted(joined, key=lambda sid: joined[sid], reverse=True)
    title = next((act["titles"][sid] for sid in by_join if act["titles"].get(sid)), "")
    start = timed[0][0] if timed else None
    if outcomes:
        end = outcomes[-1][0]
    else:
        end = max([t for t, _ in timed] + ([act["last"]] if act["last"] else [])) if timed else None
    steps, unattributed, unattributed_cost, estimated = _steps_from(
        ft.split_by_step(ft.step_marks(events), act["records"].values()), end)
    per_session: dict[str, dict] = {}
    per_session_cost: dict[str, dict] = {}
    for rec in act["records"].values():
        sid = rec.get("sid", "")
        per_session[sid] = _add(per_session.get(sid, _zero()), rec["usage"])
        per_session_cost[sid] = _add(per_session_cost.get(sid, tu.zero_cost()), tu.cost(rec))
    order = sorted(joined, key=lambda sid: joined[sid])
    session_rows = [{"id": sid, "title": act["titles"].get(sid, ""), "joined": joined[sid],
                     "tokens": per_session.get(sid, _zero()),
                     "cost": tu.dollars(per_session_cost.get(sid, tu.zero_cost())),
                     "cost_split": per_session_cost.get(sid, tu.zero_cost())} for sid in order]
    total = _add(unattributed, _zero())
    cost_split = _add(unattributed_cost, tu.zero_cost())
    for step in steps:
        total = _add(total, step["tokens"])
        cost_split = _add(cost_split, step["cost_split"])
    unpriced = tu.unpriced(act["records"].values())
    attrs = [e for _, e in timed if e.get("type") == "attribution"]
    by_skill: dict = {}
    if sum(total.values()):
        source = "transcripts"
    elif attrs and isinstance(attrs[-1].get("total"), dict):
        source, total = "attribution", _add(_zero(), attrs[-1]["total"])
        raw_unpriced = attrs[-1].get("unpriced")
        unpriced = [m for m in raw_unpriced if isinstance(m, str)] if isinstance(raw_unpriced, list) else []
        from_by_step = isinstance(attrs[-1].get("by_step"), list)
        if from_by_step:
            steps, unattributed, unattributed_cost, estimated = _steps_from(attrs[-1]["by_step"], end)
        else:
            raw_by_skill = attrs[-1].get("by_skill")
            by_skill = {k: v for k, v in raw_by_skill.items() if isinstance(v, dict)} if isinstance(raw_by_skill, dict) else {}
        if _costs_ok(attrs[-1].get("cost")):
            cost_split, estimated = _add(tu.zero_cost(), attrs[-1]["cost"]), False
        elif from_by_step and (steps or sum(unattributed.values())):  # never the transcript pass's zero-token steps
            cost_split = _add(unattributed_cost, tu.zero_cost())
            for step in steps:
                cost_split = _add(cost_split, step["cost_split"])
        else:
            cost_split, estimated = tu.estimate(total), True
    else:
        source = "none"
    markers = [{"ts": t, "kind": e["type"],
                "label": (f"{e.get('skill', '?')} iteration {e.get('iteration', '?')}" if e["type"] == "retry"
                          else f"{e.get('gate', '?')}={e.get('answer', '?')}")}
               for t, e in timed if e.get("type") in ("gate", "retry") and e.get("gate") != "handoff"]
    in_window = (lambda a, b: start and end and _overlaps(a, b, start, end))
    spans = [(max(a, start), min(b, end)) for a, b in act["waits"] if in_window(a, b)]
    outcome = outcomes[-1][1] if outcomes else {}
    intake = next((str(e.get("answer", "")) for _, e in reversed(timed)
                   if e.get("type") == "gate" and e.get("gate") == "intake_path"), "")
    return {
        "id": task_id, "title": title, "ticket": bound.get("ticket", ""), "branch": bound.get("branch", ""),
        "repo": bound.get("repo", ""), "result": outcome.get("result", "in progress"), "mr": outcome.get("mr", ""),
        "closed": bool(outcomes), "start": start, "end": end, "steps": steps,
        "subagents": sorted((s for s in act["subagents"] if in_window(s["start"], s["end"])), key=lambda s: s["start"]),
        "waits": spans, "wait_s": _union_s(spans), "markers": markers,
        "total": total, "cost": tu.dollars(cost_split), "cost_split": cost_split, "estimated": estimated,
        "unpriced": unpriced, "source": source, "by_skill": by_skill, "unattributed": unattributed,
        "unattributed_cost": unattributed_cost,
        "intake": intake, "review_rounds": _rounds(events, "simo-dev:review-loop"),
        "followup_rounds": _rounds(events, "simo-dev:mr-followup"),
        "sessions": session_rows, "root": order[0] if order else "",
    }


STALE_AFTER_H = 24
STATUS_RANK = {"running": 0, "stale": 1, "done": 2, "untracked": 3}


def set_status(m: dict, now: datetime) -> None:
    """done: the task has an outcome; else running, or stale once its last activity is over STALE_AFTER_H old.

    An open task's `end` is its last activity: its last event or its sessions' last transcript record."""
    m["idle_s"] = max((now - m["end"]).total_seconds(), 0.0) if m["end"] else None
    if m.get("untracked"):
        m["status"] = "untracked"
    elif m["closed"]:
        m["status"] = "done"
    elif m["idle_s"] is not None and m["idle_s"] <= STALE_AFTER_H * 3600:
        m["status"] = "running"
    else:
        m["status"] = "stale"


WIDTH = 960
LABEL_W = 96
LANE_H = 24
LANES = ("steps", "subagents", "waits", "gates")
TOKEN_CLASS = {"input_tokens": "k-in", "cache_creation_input_tokens": "k-cw",
               "cache_read_input_tokens": "k-cr", "output_tokens": "k-out"}
TOKEN_LABEL = {"input_tokens": "input", "cache_creation_input_tokens": "cache write",
               "cache_read_input_tokens": "cache read", "output_tokens": "output"}

CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#d9d9de;--panel:#f5f5f7;--step0:#4c78a8;--step1:#72a0cf;
--sub:#9c6ade;--wait:#e3a33b;--gate:#2f9e6e;--retry:#d0453a;--top:#fff3d6;
--k-in:#4c78a8;--k-cw:#e45756;--k-cr:#b9d3ee;--k-out:#54a24b}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--fg:#ececf0;--muted:#9a9aa2;--line:#33333a;--panel:#1d1d21;
--step0:#5b8bc0;--step1:#3f6a99;--sub:#a986e6;--wait:#c98d2c;--gate:#3fb983;--retry:#e3625a;--top:#3a3020;
--k-in:#6b9bd1;--k-cw:#f07a76;--k-cr:#3d5a78;--k-out:#6cc35f}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;padding:16px;max-width:1200px}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}.muted{color:var(--muted)}a{color:var(--k-in)}
table{border-collapse:collapse;width:100%;margin:8px 0;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:4px 6px;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}tr.top td{background:var(--top);font-weight:600}
table.steps td:nth-child(2),table.steps th:nth-child(2){text-align:left}td small{font-size:11px}
.wrap{overflow-x:auto}svg{max-width:100%;height:auto}svg.tl{min-width:880px}
svg text{fill:var(--fg);font-size:11px}svg .lane{fill:var(--muted)}
.s0{fill:var(--step0)}.s1{fill:var(--step1)}.sub{fill:var(--sub);opacity:.75}.wait{fill:var(--wait);opacity:.8}
.gate{fill:var(--gate)}.retry{fill:var(--retry)}.axis{stroke:var(--line)}
.k-in{fill:var(--k-in)}.k-cw{fill:var(--k-cw)}.k-cr{fill:var(--k-cr)}.k-out{fill:var(--k-out)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:12px 0}
.card{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--panel)}
.card b{display:block;font-size:20px}.card span{color:var(--muted);font-size:12px}
.legend{list-style:none;padding:0;margin:6px 0;display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted)}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.sw.k-in{background:var(--k-in)}.sw.k-cw{background:var(--k-cw)}.sw.k-cr{background:var(--k-cr)}.sw.k-out{background:var(--k-out)}
.sw.s0{background:var(--step0)}.sw.sub{background:var(--sub)}.sw.wait{background:var(--wait)}.sw.gate{background:var(--gate)}.sw.retry{background:var(--retry)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
#filter{flex:1 1 240px;max-width:420px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
#group,#status{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
th.sortable{cursor:pointer}th[data-dir=asc]::after{content:" \\25B2"}th[data-dir=desc]::after{content:" \\25BC"}
tr.group td{background:var(--panel);font-weight:600;text-align:left}
tr.sess td{font-size:12px;color:var(--muted)}tr.sess td:first-child{padding-left:20px}
details.task{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:12px 0}
details.task>summary{cursor:pointer}.name{font-weight:600;font-size:15px}
.events{columns:2 320px;font-size:12px;color:var(--muted);padding-left:18px}
#by-step th{white-space:normal}#by-step td.wrapit{white-space:normal;text-align:left}
[hidden]{display:none!important}
nav.tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid var(--line);margin:12px 0 4px;position:sticky;top:0;background:var(--bg);z-index:2}
nav.tabs a{padding:6px 12px;border:1px solid transparent;border-bottom:none;border-radius:6px 6px 0 0;text-decoration:none;color:var(--muted)}
nav.tabs a.on{border-color:var(--line);background:var(--panel);color:var(--fg);font-weight:600}
#overview th,#overview td{padding:4px 5px}#overview th,#overview td.wrapit,#overview tr.sess td:first-child{white-space:normal}#overview tr.sess td:first-child{overflow-wrap:anywhere}#overview td.wrapit{overflow-wrap:anywhere;min-width:9em;text-align:left}
.badge{white-space:nowrap;display:inline-block;padding:0 7px;border:1px solid;border-radius:10px;font-size:12px;font-weight:600}
.st-untracked{color:var(--muted);border-style:dashed}.st-running{color:var(--gate)}.st-stale{color:var(--wait)}.st-done{color:var(--muted);border-color:var(--line)}.st-closed{color:var(--retry)}
.cbar{display:flex;width:70px;height:9px;margin:0 0 2px auto;background:var(--panel)}.cbar.thin{height:5px}.cbar i{display:block;height:100%}
.cbar .k-in{background:var(--k-in)}.cbar .k-cw{background:var(--k-cw)}.cbar .k-cr{background:var(--k-cr)}.cbar .k-out{background:var(--k-out)}
#period,#chart-period,#refresh,#pager button{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
.pager{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:4px 0 8px;font-size:13px;color:var(--muted)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:12px;margin:12px 0}
figure.chart{margin:0;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
figure.chart figcaption{font-size:13px}figure.chart svg{width:100%;display:block}
svg.ch text{fill:var(--muted);font-size:10px}svg.ch .grid{stroke:var(--line);stroke-width:1}
svg.ch rect.seg,svg.ch circle{stroke:var(--bg);stroke-width:1}svg.ch .hit{fill:transparent}svg.ch .hit:hover{fill:var(--fg);opacity:.06}
"""


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _n(value: float) -> str:
    return f"{value:,.0f}"


def _hm(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if ts else "n/a"


def _dur(start, end) -> str:
    return ft._duration((end - start).total_seconds()) if start and end else "n/a"


def _short(skill: str) -> str:
    return skill.removeprefix("simo-dev:")


def _name(m: dict) -> str:
    return m.get("title") or m.get("branch") or m["id"]


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


def _legend(items: list[tuple[str, str]]) -> str:
    return '<ul class="legend">' + "".join(f'<li><i class="sw {c}"></i>{_e(t)}</li>' for c, t in items) + "</ul>"


def _link(url: str, text: str) -> str:
    return f'<a href="{_e(url)}">{_e(text)}</a>' if url.startswith(("https://", "http://")) else ""


def _text_key(m: dict) -> str:
    return " ".join(str(v) for v in (_name(m), m["id"], m["repo"], m["branch"], m["ticket"], m["result"], m["status"]) if v).lower()


def _secs(start, end) -> float:
    return (end - start).total_seconds() if start and end else -1


def _by_start(m: dict) -> tuple:
    return (m["start"] is not None, m["start"] or datetime.min.replace(tzinfo=timezone.utc))


def _newest_first(models: list[dict]) -> list[dict]:
    return sorted(models, key=_by_start, reverse=True)


def _by_status(models: list[dict]) -> list[dict]:
    """Running, then stale, then done; newest first inside each (a stable sort)."""
    return sorted(_newest_first(models), key=lambda m: STATUS_RANK[m["status"]])


def _ago(seconds: float) -> str:
    return f"{int(seconds // 86400)}d" if seconds >= 48 * 3600 else ft._duration(seconds)


def _status_label(m: dict) -> str:
    if m["status"] in ("done", "untracked"):
        return m["result"]
    return f'{m["status"]} · {_ago(m["idle_s"])} ago' if m["idle_s"] is not None else m["status"]


def _status_class(m: dict) -> str:
    """running, stale, done (merged) or closed (an outcome with any other result): the colour of the status."""
    return ("done" if m["result"] == "merged" else "closed") if m["status"] == "done" else m["status"]


def _status_title(m: dict) -> str:
    if m["status"] == "untracked":
        return f"flow steps with no task ID (not adopted as a task, see the docs): last activity {_hm(m['end'])} UTC"
    return f"done: outcome {m['result']}" if m["status"] == "done" else f"open: last activity {_hm(m['end'])} UTC"


def _badge(m: dict) -> str:
    return f'<span class="badge st-{_status_class(m)}" title="{_e(_status_title(m))}">{_e(_status_label(m))}</span>'


def _mr_text(url: str) -> str:
    """`MR !12` for a GitLab merge request URL, `PR #12` for a GitHub pull request, else `MR`."""
    hit = re.search(r"/(merge_requests|pull)/(\d+)", url)
    if not hit:
        return "MR"
    return f"MR !{hit[2]}" if hit[1] == "merge_requests" else f"PR #{hit[2]}"


def _cost_label(m: dict) -> str:
    return ("≈" if _rough_flag(m) else "") + _usd(m["cost"])


def _rough_flag(m: dict) -> bool:
    return bool(m["estimated"] or m["unpriced"])


def summary_cards(models: list[dict]) -> str:
    runs = [m for m in models if m.get("untracked")]
    models = [m for m in models if not m.get("untracked")]
    costs = sorted(m["cost"] for m in models if m["source"] != "none")
    approx = "≈" if any(_rough_flag(m) for m in models if m["source"] != "none") else ""
    total = _zero()
    for m in models:
        total = _add(total, m["total"])
    merged = sum(1 for m in models if m["result"] == "merged")
    wait = sum(m["wait_s"] for m in models if m["source"] == "transcripts")
    mean = sum(costs) / len(costs) if costs else None
    half = len(costs) // 2
    median = (costs[half] if len(costs) % 2 else (costs[half - 1] + costs[half]) / 2) if costs else None
    running = sum(1 for m in models if m["status"] == "running")
    stale = sum(1 for m in models if m["status"] == "stale")
    cards = [(str(len(models)), f"tasks · {merged} merged"),
             (str(running), f"running · {stale} stale (no activity for {STALE_AFTER_H}h)"),
             (approx + _usd(mean) if mean is not None else "n/a", "mean $ est., tasks with tokens"),
             (approx + _usd(median) if median is not None else "n/a", "median $ est., tasks with tokens"),
             (_n(total["cache_read_input_tokens"]), "cache read, all tasks"),
             (_pct(cache_pct(total)), "cache share of input"),
             (ft._duration(wait), "human wait, all tasks")]
    if runs:
        cards.append((("≈" if any(_rough_flag(m) for m in runs) else "") + _usd(sum(m["cost"] for m in runs)),
                      f"$ est. untracked runs, {len(runs)} sessions"))
    return '<div class="cards">' + "".join(f'<div class="card"><b>{_e(v)}</b><span>{_e(t)}</span></div>' for v, t in cards) + "</div>"


def timeline_svg(task: dict) -> str:
    t0, t1 = task["start"], task["end"]
    if not t0 or not t1:
        return '<p class="muted">no timed events</p>'
    span = max((t1 - t0).total_seconds(), 1.0)
    plot = WIDTH - LABEL_W - 8

    def x(ts: datetime) -> float:
        return LABEL_W + max(0.0, min(1.0, (ts - t0).total_seconds() / span)) * plot

    def y(lane: str) -> int:
        return LANES.index(lane) * LANE_H + 4

    def bar(lane, start, end, cls, title, label="", fallback=""):
        x0, x1 = x(start), max(x(end), x(start) + 2)
        shown = label if label and x1 - x0 > 7 * len(label) else (fallback if fallback and x1 - x0 > 14 else "")
        text = f'<text x="{x0 + 3:.1f}" y="{y(lane) + 14}">{_e(shown)}</text>' if shown else ""
        return (f'<rect class="{cls}" x="{x0:.1f}" y="{y(lane)}" width="{x1 - x0:.1f}" height="{LANE_H - 6}">'
                f'<title>{_e(title)}</title></rect>{text}')

    parts = [f'<text class="lane" x="0" y="{y(lane) + 14}">{lane}</text>' for lane in LANES]
    for i, s in enumerate(task["steps"]):
        parts.append(bar("steps", s["start"], s["end"], f"s{i % 2}",
                         f"{i + 1}. {s['skill']} · {_dur(s['start'], s['end'])} · {_usd(s['cost'])}",
                         _short(s["skill"]), str(i + 1)))
    for s in task["subagents"]:
        parts.append(bar("subagents", s["start"], s["end"], "sub",
                         f"{s['type']} · {s['description']} · {_dur(s['start'], s['end'])}"))
    for a, b in task["waits"]:
        parts.append(bar("waits", a, b, "wait", f"human wait · {_dur(a, b)}"))
    for m in task["markers"]:
        cx, cy = x(m["ts"]), y("gates") + (LANE_H - 6) / 2
        parts.append(f'<circle class="{"retry" if m["kind"] == "retry" else "gate"}" cx="{cx:.1f}" cy="{cy:.1f}" r="5">'
                     f'<title>{_e(m["kind"])}: {_e(m["label"])} · {_hm(m["ts"])}</title></circle>')
    height = len(LANES) * LANE_H + 22
    axis_y = len(LANES) * LANE_H + 6
    parts.append(f'<line class="axis" x1="{LABEL_W}" y1="{axis_y}" x2="{WIDTH - 8}" y2="{axis_y}"/>')
    parts.append(f'<text x="{LABEL_W}" y="{axis_y + 14}">{_hm(t0)} UTC</text>')
    parts.append(f'<text x="{WIDTH - 8}" y="{axis_y + 14}" text-anchor="end">{_hm(t1)} UTC</text>')
    legend = _legend([("s0", "step"), ("sub", "subagent"), ("wait", "human wait"), ("gate", "gate"), ("retry", "retry")])
    return (f'{legend}<div class="wrap"><svg class="tl" viewBox="0 0 {WIDTH} {height}" width="100%" '
            f'role="img" aria-label="timeline of task {_e(task["id"])}">{"".join(parts)}</svg></div>')


def events_list(task: dict) -> str:
    items = [(s["start"], " · ".join(x for x in (_hm(s["start"])[11:], s["type"], s["description"], _dur(s["start"], s["end"])) if x))
             for s in task["subagents"]]
    items += [(m["ts"], f'{_hm(m["ts"])[11:]} · {m["kind"]} · {m["label"]}') for m in task["markers"]]
    if not items:
        return ""
    return '<ul class="events">' + "".join(f"<li>{_e(text)}</li>" for _, text in sorted(items, key=lambda i: i[0])) + "</ul>"


def _counts_cells(counts: dict, dollars: float) -> str:
    return ("".join(f"<td>{_n(counts.get(k, 0))}</td>" for k in tu.KEYS)
            + f"<td>{_usd(dollars)}</td><td>{_pct(cache_pct(counts))}</td>")


TOP = ' class="top"'
TOKEN_HEAD = "<th>input</th><th>cache write</th><th>cache read</th><th>output</th><th>$ est.</th><th>cache %</th>"


def _rough(counts: dict) -> float:
    return tu.dollars(tu.estimate(counts))


def tokens_table(task: dict) -> str:
    if task["source"] == "attribution" and task["by_skill"]:
        rows = sorted(task["by_skill"].items(), key=lambda kv: -_rough(kv[1]))
        body = "".join(f'<tr{TOP if i == 0 else ""}><td>{_e(name)}</td>'
                       f'{_counts_cells(c, _rough(c))}</tr>' for i, (name, c) in enumerate(rows))
        return (f'<p class="muted">no transcript usage: per-skill totals from the attribution event</p>'
                f'<div class="wrap"><table><tr><th>skill</th>{TOKEN_HEAD}</tr>{body}</table></div>')
    if task["source"] == "none":
        return ('<p class="muted">no tokens to show: no transcript usage '
                '(or no step_start at all) and no attribution event</p>')
    unattributed = task.get("unattributed") or _zero()
    if task["source"] == "attribution" and not sum(unattributed.values()) and not any(sum(s["tokens"].values()) for s in task["steps"]):
        return '<p class="muted">no transcript usage, and the attribution event has no per-step breakdown: total only</p>'
    note = ('<p class="muted">no transcript usage left: steps from the attribution event</p>'
            if task["source"] == "attribution" else "")
    steps = task["steps"]
    top = max(range(len(steps)), key=lambda i: steps[i]["cost"]) if steps else -1
    rows = []
    for i, s in enumerate(steps):
        rows.append(f'<tr{TOP if i == top else ""}><td>{i + 1}</td><td>{_e(s["skill"])}</td><td>{_hm(s["start"])}</td>'
                    f'<td>{_dur(s["start"], s["end"])}</td>{_counts_cells(s["tokens"], s["cost"])}</tr>')
        agent_costs = s.get("agent_costs", {})
        spent = {name: tu.dollars(agent_costs.get(name) or tu.estimate(c)) for name, c in s.get("agents", {}).items()}
        for name, c in sorted(s.get("agents", {}).items(), key=lambda kv: -spent[kv[0]]):
            rows.append(f'<tr class="muted"><td></td><td>↳ {_e(name)}</td><td></td><td></td>{_counts_cells(c, spent[name])}</tr>')
    if sum(unattributed.values()):
        rows.append(f'<tr class="muted"><td></td><td>before the first step</td><td></td><td></td>'
                    f'{_counts_cells(unattributed, tu.dollars(task.get("unattributed_cost") or tu.estimate(unattributed)))}</tr>')
    total = f'<tr><td></td><td>total</td><td></td><td></td>{_counts_cells(task["total"], task["cost"])}</tr>'
    return (f'{note}<div class="wrap"><table class="steps"><tr><th>#</th><th>step</th><th>start (UTC)</th><th>took</th>{TOKEN_HEAD}</tr>'
            f'{"".join(rows)}{total}</table></div>')


def _cost_bar(split: dict, counts: dict, top: float, thin: bool = False) -> str:
    """The cost stacked by token category, on the shared scale `top` (the costliest task)."""
    segs = "".join(f'<i class="{TOKEN_CLASS[k]}" style="width:{split.get(k, 0.0) / top * 100:.2f}%" '
                   f'title="{TOKEN_LABEL[k]} {_n(counts.get(k, 0))} tokens · {_usd(split.get(k, 0.0))}"></i>'
                   for k in tu.KEYS if split.get(k, 0.0) > 0)
    return f'<span class="cbar{" thin" if thin else ""}">{segs}</span>'


def _ts_v(ts: datetime | None) -> str:
    return f"{ts.timestamp():.0f}" if ts else "-1"


PAGE_SIZE = 25
BEFORE = "(before the first step)"


def step_totals(models: list[dict]) -> tuple[list[dict], float, int]:
    """Every task's steps summed by skill (the step model of Tokens per step), costliest first; the
    tokens before a task's first step make their own row. Also the cost no step holds (a task whose
    attribution event has only the old per-skill table) and how many tasks that is."""
    rows: dict[str, dict] = {}

    def row(name: str) -> dict:
        return rows.setdefault(name, {"skill": name, "runs": 0, "tasks": set(), "tokens": _zero(),
                                      "cost_split": tu.zero_cost(), "secs": 0.0, "agents": 0.0})

    missing, short = 0.0, 0
    for m in models:
        covered = 0.0
        for st in m["steps"]:
            r = row(st["skill"])
            r["runs"] += 1
            r["tasks"].add(m["id"])
            r["tokens"], r["cost_split"] = _add(r["tokens"], st["tokens"]), _add(r["cost_split"], st["cost_split"])
            r["secs"] += max(_secs(st["start"], st["end"]), 0.0)
            r["agents"] += sum(tu.dollars(c) for c in st.get("agent_costs", {}).values())
            covered += st["cost"]
        before = m.get("unattributed") or _zero()
        if sum(before.values()):
            r = row(BEFORE)
            r["runs"] += 1
            r["tasks"].add(m["id"])
            r["tokens"] = _add(r["tokens"], before)
            r["cost_split"] = _add(r["cost_split"], m.get("unattributed_cost") or tu.estimate(before))
            covered += tu.dollars(m.get("unattributed_cost") or tu.estimate(before))
        if m["cost"] - covered > 0.005:
            missing, short = missing + m["cost"] - covered, short + 1
    out = [{**r, "tasks": len(r["tasks"]), "cost": tu.dollars(r["cost_split"])} for r in rows.values()]
    return sorted(out, key=lambda r: -r["cost"]), missing, short


def steps_overview(models: list[dict]) -> str:
    rows, missing, short = step_totals(models)
    if not rows:
        return ""
    total = sum(r["cost"] for r in rows) + missing
    top = rows[0]["cost"] or 1.0
    approx = "≈" if any(_rough_flag(m) for m in models) else ""
    body = []
    for i, r in enumerate(rows):
        name = r["skill"] if r["skill"] == BEFORE else _short(r["skill"])
        share = r["cost"] / total * 100 if total else 0.0
        body.append(f'<tr{TOP if i == 0 else ""}><td class="wrapit">{_e(name)}</td><td>{r["runs"]}</td><td>{r["tasks"]}</td>'
                    f'<td>{_cost_bar(r["cost_split"], r["tokens"], top)}{approx}{_usd(r["cost"])}</td><td>{share:.0f}%</td>'
                    f'<td>{approx}{_usd(r["cost"] / r["runs"])}</td><td>{ft._duration(r["secs"]) if r["skill"] != BEFORE else "—"}</td>'
                    f'<td>{_usd(r["agents"]) if r["agents"] else "—"}</td><td>{_pct(cache_pct(r["tokens"]))}</td></tr>')
    note = (f'<p class="muted">{short} task(s) with no per-step breakdown (old attribution event): {approx}{_usd(missing)} '
            "counted in the share, not in any row</p>" if short else "")
    return (f'<h2>Cost by step, all tasks</h2><p class="muted">every step of the {len(models)} task(s) on this page summed by '
            "skill, costliest first; the filters of the task table do not apply. A nested helper skill (tdd, verify-done, …) "
            "is its own step; a subagent counts on the step that launched it (\"subagents\" is that part). Wall time includes "
            f"human waits.</p>{note}<div class=\"wrap\"><table id=\"by-step\"><tr><th>step</th><th>runs</th><th>tasks</th>"
            "<th>$ est.</th><th>share</th><th>$ per run</th><th>wall time</th><th>subagents $</th><th>cache %</th></tr>"
            f'{"".join(body)}</table></div>')


def overview(models: list[dict]) -> str:
    top = max((m["cost"] for m in models), default=0.0) or 1.0
    head = ("<thead><tr><th>task</th>"
            '<th title="running / stale (open, no activity for over the threshold) / done: the outcome result">status</th>'
            "<th>start (UTC)</th><th>repo / branch</th><th>ticket</th><th>MR</th>"
            "<th>duration</th><th>$ est.</th><th>cache %</th>"
            '<th title="review-loop / mr-followup rounds">rounds</th><th>human wait</th>'
            '<th title="task-intake path: fast (decided brief), fallback (brief contradicted the code, '
            'or restart at the plan gate), full, resume (work already in progress re-entered the flow); '
            '— not recorded (before this change, or no intake)">intake</th></tr></thead>')
    rows = []
    names = {m["id"]: _name(m) for m in models}
    for m in _by_status(models):
        pct = cache_pct(m["total"])
        tr = m.get("tree") or {"root": m["id"], "depth": 0, "order": 0}
        ticket = _link(m.get("ticket_url", ""), m["ticket"]) or _e(m["ticket"] or "—")
        wait = m["wait_s"] if m["source"] == "transcripts" else -1
        sessions = m.get("sessions", [])
        root = next((s for s in sessions if s["id"] == m.get("root")), {"id": "", "title": ""})
        seen = sum(m["total"].get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        root_title = root["title"] or root["id"][:8] or "no session"
        attrs = (f'class="task-row" data-id="{_e(m["id"])}" data-text="{_e(_text_key(m))}" data-repo="{_e(m["repo"] or "n/a")}" data-intake="{_e(m.get("intake") or "—")}" '
                 f'data-status="{m["status"]}" data-last="{_ts_v(m["end"])}" '
                 f'data-root="{_e(root["id"])}" data-root-title="{_e(root_title)}" '
                 f'data-tree="{_e(tr["root"])}" data-tree-title="{_e(names.get(tr["root"], tr["root"]))}" '
                 f'data-depth="{tr["depth"]}" data-tree-order="{tr["order"]}" '
                 f'data-w="{m["cost"]:.6f}" data-est="{int(_rough_flag(m))}" data-cr="{m["total"].get("cache_read_input_tokens", 0)}" data-seen="{seen}"')
        rows.append(
            f'<tr {attrs}><td class="wrapit"><a href="#task-{_e(m["id"])}">{_e(_name(m))}</a>'
            f'<br><small class="muted">{_e(m["id"])}</small></td>'
            f'<td data-v="{STATUS_RANK[m["status"]]}">{_badge(m)}</td>'
            f'<td data-v="{m["start"].timestamp() if m["start"] else -1:.0f}">{_hm(m["start"]).replace(" ", "<br>", 1)}</td>'
            f'<td class="wrapit">{_e(m["repo"] or "n/a")}<br><small class="muted">{_e(m["branch"] or "n/a")}</small></td>'
            f'<td>{ticket}</td>'
            f'<td>{_link(m["mr"], _mr_text(m["mr"])) or "—"}</td>'
            f'<td data-v="{_secs(m["start"], m["end"]):.0f}">{_dur(m["start"], m["end"])}</td>'
            f'<td data-v="{m["cost"]:.6f}">{_cost_bar(m["cost_split"], m["total"], top)}{_cost_label(m)}</td>'
            f'<td data-v="{pct if pct is not None else -1:.1f}">{_pct(pct)}</td>'
            f'<td data-v="{m["review_rounds"] + m["followup_rounds"]}">{m["review_rounds"]}/{m["followup_rounds"]}</td>'
            f'<td data-v="{wait:.0f}">{ft._duration(wait) if wait >= 0 else "n/a"}</td>'
            f'<td>{_e(m.get("intake") or "—")}</td></tr>')
        if len(sessions) > 1:
            for s in sessions:
                label = (f'{_e(s["title"])} <small class="muted">{_e(s["id"][:8])}</small>' if s["title"]
                         else _e(s["id"][:8]))
                rows.append(f'<tr class="sess" data-task="{_e(m["id"])}"><td>↳ {label}</td><td></td><td>{_hm(s["joined"]).replace(" ", "<br>", 1)}</td>'
                            '<td></td><td></td><td></td><td></td>'
                            f'<td data-v="{s["cost"]:.6f}">{_cost_bar(s.get("cost_split", {}), s["tokens"], top, True)}'
                            f'{_usd(s["cost"])}</td><td>{_pct(cache_pct(s["tokens"]))}</td>'
                            '<td></td><td></td><td></td></tr>')
    controls = ('<div class="controls"><input type="search" id="filter" placeholder="filter by name, repo, branch, ticket, status" hidden>'
                '<select id="status" hidden><option value="all">all statuses</option><option value="running">running</option>'
                '<option value="stale">stale</option><option value="done">done</option><option value="untracked">untracked</option></select>'
                '<select id="group" hidden><option value="none">no grouping</option><option value="status">group by status</option>'
                '<option value="repo">group by repo</option><option value="intake">group by intake path</option>'
                '<option value="session">group by session</option><option value="tree">group by task tree</option></select>'
                '<select id="period" hidden title="last activity in the period"><option value="0">any time</option>'
                '<option value="7">active in the last 7 days</option><option value="30">active in the last 30 days</option>'
                '<option value="90">active in the last 90 days</option></select></div>'
                f'<div class="pager" id="pager" data-size="{PAGE_SIZE}" hidden><span id="shown"></span>'
                '<button type="button" id="prev">‹ prev</button><span id="pageno"></span><button type="button" id="next">next ›</button></div>')
    legend = _legend([(TOKEN_CLASS[k], TOKEN_LABEL[k]) for k in tu.KEYS])
    return (f'{controls}<p class="muted">$ est. bars: cost by token category, one scale for every row '
            f'(the costliest task, {_usd(top)})</p>{legend}'
            f'<div class="wrap"><table id="overview">{head}<tbody>{"".join(rows)}</tbody></table></div>')


CH_W, CH_PAD_L, CH_PAD_B, CH_PAD_T = 460, 56, 30, 8
CHART_BUCKETS = 16
CHART_DAILY_MAX_DAYS = 28
CHART_STEPS = 12


def _nice(value: float) -> float:
    """The smallest round number (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8 × 10^k) at or above `value`: the top of a chart's scale."""
    if value <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(value))
    return next(m * p for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if m * p >= value - 1e-9)


def _nice_dur(seconds: float) -> float:
    """A scale top in seconds that is a round number of hours, or under an hour 4, 8, 20, 40 or 60 minutes (quarters stay whole)."""
    if seconds > 3600:
        return _nice(seconds / 3600) * 3600
    return next(m for m in (4, 8, 20, 40, 60) if m * 60 >= seconds) * 60


def _tok(value: float) -> str:
    return f"{value / 1e6:.1f}M" if value >= 1e6 else f"{value / 1e3:.1f}k" if value >= 1e3 else f"{value:.0f}"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _dur_tick(seconds: float) -> str:
    return f"{seconds / 3600:g}h" if seconds >= 3600 else f"{seconds / 60:.0f}m"


def _figure(title: str, note: str, svg: str, legend: str = "") -> str:
    return (f'<figure class="chart"><figcaption><b>{_e(title)}</b><br><span class="muted">{_e(note)}</span></figcaption>'
            f'{legend}{svg}</figure>')


def _empty(title: str) -> str:
    return _figure(title, "no data yet", "")


def _ticks(top: float, fmt) -> list[str]:
    return [fmt(top * i / 4) for i in range(5)]


def vbars_svg(cols: list[tuple[str, list[tuple[str, float]], str]], fmt, label: str, height: int = 220, nice=_nice) -> str:
    """Stacked vertical bars on one scale: each column is (x label, [(class, value)], hover text)."""
    top = nice(max((sum(v for _, v in segs) for _, segs, _ in cols), default=0.0))
    plot_h, plot_w = height - CH_PAD_B - CH_PAD_T, CH_W - CH_PAD_L - 8
    slot = plot_w / max(len(cols), 1)
    bw = min(slot * 0.7, 36)
    every = max(1, math.ceil(len(cols) / 8))
    parts = []
    for i, tick in enumerate(_ticks(top, fmt)):
        y = CH_PAD_T + plot_h * (1 - i / 4)
        parts.append(f'<line class="grid" x1="{CH_PAD_L}" y1="{y:.1f}" x2="{CH_W - 8}" y2="{y:.1f}"/>'
                     f'<text x="{CH_PAD_L - 4}" y="{y + 3:.1f}" text-anchor="end">{_e(tick)}</text>')
    for j, (xl, segs, tip) in enumerate(cols):
        x, y = CH_PAD_L + slot * j + (slot - bw) / 2, CH_PAD_T + plot_h
        for cls, v in segs:
            h = v / top * plot_h
            if h > 0:
                y -= h
                parts.append(f'<rect class="seg {cls}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}"/>')
        if j % every == 0:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{height - CH_PAD_B + 14}" text-anchor="middle">{_e(xl)}</text>')
        parts.append(f'<rect class="hit" x="{CH_PAD_L + slot * j:.1f}" y="{CH_PAD_T}" width="{slot:.1f}" height="{plot_h}">'
                     f'<title>{_e(tip)}</title></rect>')
    return f'<svg class="ch" viewBox="0 0 {CH_W} {height}" role="img" aria-label="{_e(label)}">{"".join(parts)}</svg>'


def hbars_svg(rows: list[tuple[str, list[tuple[str, float]], str]], fmt, label: str, nice=_nice, fmt_total=None) -> str:
    """Stacked horizontal bars, one row per item, the total printed at the bar's end (`fmt_total`, else `fmt`)."""
    lab_w, row_h, val_w = 118, 20, 52
    height = len(rows) * row_h + 22
    top = nice(max((sum(v for _, v in segs) for _, segs, _ in rows), default=0.0))
    plot_w = CH_W - lab_w - val_w
    parts = []
    for i, tick in enumerate(_ticks(top, fmt)):
        x = lab_w + plot_w * i / 4
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 18}"/>'
                     f'<text x="{x:.1f}" y="{height - 6}" text-anchor="middle">{_e(tick)}</text>')
    for j, (name, segs, tip) in enumerate(rows):
        y, x = j * row_h + 3, lab_w
        shown = name if len(name) <= 19 else name[:18] + "…"
        parts.append(f'<text x="{lab_w - 6}" y="{y + 11}" text-anchor="end">{_e(shown)}</text>')
        for cls, v in segs:
            w = v / top * plot_w
            if w > 0:
                parts.append(f'<rect class="seg {cls}" x="{x:.1f}" y="{y}" width="{w:.1f}" height="{row_h - 6}"/>')
                x += w
        parts.append(f'<text x="{x + 4:.1f}" y="{y + 11}">{_e((fmt_total or fmt)(sum(v for _, v in segs)))}</text>'
                     f'<rect class="hit" x="0" y="{y - 2}" width="{CH_W}" height="{row_h}"><title>{_e(tip)}</title></rect>')
    return f'<svg class="ch" viewBox="0 0 {CH_W} {height}" role="img" aria-label="{_e(label)}">{"".join(parts)}</svg>'


def scatter_svg(points: list[tuple[float, float, str, str]], fx, fy, label: str, height: int = 240,
                nice_x=_nice, nice_y=_nice) -> str:
    """Dots (x, y, class, hover text) on linear scales from zero."""
    tx, ty = nice_x(max((p[0] for p in points), default=0.0)), nice_y(max((p[1] for p in points), default=0.0))
    plot_h, plot_w = height - CH_PAD_B - CH_PAD_T, CH_W - CH_PAD_L - 16
    parts = []
    for i in range(5):
        y, x = CH_PAD_T + plot_h * (1 - i / 4), CH_PAD_L + plot_w * i / 4
        parts.append(f'<line class="grid" x1="{CH_PAD_L}" y1="{y:.1f}" x2="{CH_PAD_L + plot_w}" y2="{y:.1f}"/>'
                     f'<text x="{CH_PAD_L - 4}" y="{y + 3:.1f}" text-anchor="end">{_e(fy(ty * i / 4))}</text>'
                     f'<text x="{x:.1f}" y="{height - CH_PAD_B + 14}" text-anchor="middle">{_e(fx(tx * i / 4))}</text>')
    for vx, vy, cls, tip in points:
        cx, cy = CH_PAD_L + vx / tx * plot_w, CH_PAD_T + plot_h * (1 - vy / ty)
        parts.append(f'<circle class="{cls}" cx="{cx:.1f}" cy="{cy:.1f}" r="5"><title>{_e(tip)}</title></circle>')
    return f'<svg class="ch" viewBox="0 0 {CH_W} {height}" role="img" aria-label="{_e(label)}">{"".join(parts)}</svg>'


def time_buckets(models: list[dict]) -> tuple[str, list[tuple[datetime, list[dict]]]]:
    """Tasks by the UTC day of their start when they span up to CHART_DAILY_MAX_DAYS, else by the week's
    Monday; every bucket from the first to the last (an empty one stays), the last CHART_BUCKETS."""
    starts = [m["start"] for m in models if m["start"]]
    if not starts:
        return "day", []
    unit = "day" if (max(starts) - min(starts)).days <= CHART_DAILY_MAX_DAYS else "week"
    step = timedelta(days=1 if unit == "day" else 7)
    by: dict = {}
    for m in models:
        if m["start"]:
            day = m["start"].replace(hour=0, minute=0, second=0, microsecond=0)
            by.setdefault(day - timedelta(days=day.weekday()) if unit == "week" else day, []).append(m)
    out, cur = [], min(by)
    while cur <= max(by):
        out.append((cur, by.get(cur, [])))
        cur += step
    return unit, out[-CHART_BUCKETS:]


def _token_tip(counts: dict) -> str:
    return " · ".join(f"{TOKEN_LABEL[k]} {_tok(counts.get(k, 0))}" for k in tu.KEYS)


def _cache_cols(unit: str, buckets: list) -> list:
    cols = []
    for t, ms in buckets:
        total = _zero()
        for m in ms:
            total = _add(total, m["total"])
        pct = cache_pct(total)
        cols.append((t.strftime("%m-%d"), [("k-cr", pct or 0.0)],
                     f"{unit} of {t:%Y-%m-%d} · {len(ms)} task(s) · cache read {_pct(pct)} of input"))
    return cols


def chart_grid(models: list[dict]) -> str:
    """The figures of the Charts view: averages per task and per step, over time and side by side; tracked tasks only."""
    tasks = [m for m in models if not m.get("untracked")]
    priced = [m for m in tasks if m["source"] != "none"]
    done = [m for m in tasks if m["status"] == "done" and m["source"] == "transcripts" and m["start"] and m["end"]]
    token_legend = _legend([(TOKEN_CLASS[k], TOKEN_LABEL[k]) for k in tu.KEYS])
    out = []

    unit, buckets = time_buckets(priced)
    title = f"Mean tokens per task, by {unit}"
    if buckets:
        cols = []
        for t, ms in buckets:
            mean = {k: _mean([m["total"].get(k, 0) for m in ms]) for k in tu.KEYS}
            cols.append((t.strftime("%m-%d"), [(TOKEN_CLASS[k], mean[k]) for k in tu.KEYS],
                         f"{unit} of {t:%Y-%m-%d} · {len(ms)} task(s) · mean {_tok(sum(mean.values()))} tokens: {_token_tip(mean)}"))
        out.append(_figure(title, f"{unit} of the task's start (UTC); an empty {unit} has no bar",
                           vbars_svg(cols, _tok, title), token_legend))
        cols = []
        for t, ms in buckets:
            mean = {k: _mean([m["cost_split"].get(k, 0.0) for m in ms]) for k in tu.KEYS}
            cols.append((t.strftime("%m-%d"), [(TOKEN_CLASS[k], mean[k]) for k in tu.KEYS],
                         f"{unit} of {t:%Y-%m-%d} · {len(ms)} task(s) · mean {_usd(sum(mean.values()))}: "
                         + " · ".join(f"{TOKEN_LABEL[k]} {_usd(mean[k])}" for k in tu.KEYS)))
        out.append(_figure(f"Mean $ est. per task, by {unit}", "the same tasks, priced: where the money goes by token category",
                           vbars_svg(cols, _usd, f"mean cost per task by {unit}"), token_legend))
    else:
        out.append(_empty(title))

    unit, buckets = time_buckets(done)
    title = f"Mean task duration, by {unit}"
    if buckets:
        cols = []
        for t, ms in buckets:
            wait = _mean([m["wait_s"] for m in ms])
            active = _mean([max(_secs(m["start"], m["end"]) - m["wait_s"], 0.0) for m in ms])
            cols.append((t.strftime("%m-%d"), [("s0", active), ("wait", wait)],
                         f"{unit} of {t:%Y-%m-%d} · {len(ms)} done task(s) · mean {ft._duration(active + wait)}: "
                         f"active {ft._duration(active)} · human wait {ft._duration(wait)}"))
        out.append(_figure(title, "done tasks with transcripts, first event to outcome; human wait estimated",
                           vbars_svg(cols, _dur_tick, title, nice=_nice_dur), _legend([("s0", "active"), ("wait", "human wait")])))
    else:
        out.append(_empty(title))

    rows = [r for r in step_totals(tasks)[0] if r["skill"] != BEFORE]
    rows = sorted(rows, key=lambda r: -r["runs"])[:CHART_STEPS]
    title = "Mean tokens per step run"
    if rows:
        by_tokens = sorted(rows, key=lambda r: -sum(r["tokens"].values()) / r["runs"])
        bars = [(_short(r["skill"]), [(TOKEN_CLASS[k], r["tokens"].get(k, 0) / r["runs"]) for k in tu.KEYS],
                 f"{_short(r['skill'])} · {r['runs']} run(s) in {r['tasks']} task(s) · mean "
                 f"{_tok(sum(r['tokens'].values()) / r['runs'])} tokens: {_token_tip({k: v / r['runs'] for k, v in r['tokens'].items()})}")
                for r in by_tokens]
        out.append(_figure(title, f"the {len(rows)} most-run steps, all tasks; a subagent counts on the step that launched it",
                           hbars_svg(bars, _tok, title), token_legend))
        by_time = sorted(rows, key=lambda r: -r["secs"] / r["runs"])
        bars = [(_short(r["skill"]), [("s0", r["secs"] / r["runs"])],
                 f"{_short(r['skill'])} · {r['runs']} run(s) · mean {ft._duration(r['secs'] / r['runs'])} · "
                 f"total {ft._duration(r['secs'])} · mean {_usd(r['cost'] / r['runs'])} per run")
                for r in by_time]
        out.append(_figure("Mean wall time per step run", "a step lasts until the next one starts; human waits included",
                           hbars_svg(bars, _dur_tick, "mean wall time per step run", nice=_nice_dur, fmt_total=ft._duration)))
        by_cost = sorted(rows, key=lambda r: -r["cost"] / r["runs"])
        bars = [(_short(r["skill"]), [("s0", max(r["cost"] - r["agents"], 0.0) / r["runs"]), ("sub", r["agents"] / r["runs"])],
                 f"{_short(r['skill'])} · mean {_usd(r['cost'] / r['runs'])} per run · own "
                 f"{_usd(max(r['cost'] - r['agents'], 0.0) / r['runs'])} · subagents {_usd(r['agents'] / r['runs'])}")
                for r in by_cost]
        out.append(_figure("Mean $ est. per step run", "the step's own turns and the subagents it launched",
                           hbars_svg(bars, _usd, "mean cost per step run"), _legend([("s0", "step"), ("sub", "subagents")])))
    else:
        out.append(_empty(title))

    title = "Task duration vs $ est."
    if done:
        points = [(_secs(m["start"], m["end"]), m["cost"], "s0",
                   f"{_name(m)} · {_dur(m['start'], m['end'])} · {_cost_label(m)} · "
                   f"{len(m['steps'])} step(s) · rounds {m['review_rounds']}/{m['followup_rounds']}") for m in done]
        out.append(_figure(title, "one dot per done task: long and cheap is mostly waiting, short and costly is heavy work",
                           scatter_svg(points, _dur_tick, _usd, title, nice_x=_nice_dur)))
    else:
        out.append(_empty(title))

    closed = [m for m in tasks if m["status"] == "done"]
    title = "Tasks by review rounds"
    if closed:
        buckets_r = ["0", "1", "2", "3", "4", "5+"]
        counts = {b: [0, 0] for b in buckets_r}
        for m in closed:
            b = buckets_r[min(m["review_rounds"] + m["followup_rounds"], 5)]
            counts[b][0 if m["result"] == "merged" else 1] += 1
        cols = [(b, [("s0", counts[b][0]), ("retry", counts[b][1])],
                 f"{b} round(s): {counts[b][0]} merged · {counts[b][1]} other outcome") for b in buckets_r]
        out.append(_figure(title, "done tasks by review-loop + mr-followup rounds",
                           vbars_svg(cols, lambda v: f"{v:g}", title, 200),
                           _legend([("s0", "merged"), ("retry", "other outcome")])))
    else:
        out.append(_empty(title))

    unit, buckets = time_buckets(priced)
    title = f"Cache share of input, by {unit}"
    if buckets:
        out.append(_figure(title, "cache read over all input tokens: higher is cheaper",
                           vbars_svg(_cache_cols(unit, buckets), lambda v: f"{v:.0f}%", title, 200, nice=lambda v: 100.0)))
    else:
        out.append(_empty(title))
    return "".join(out)


CHART_PERIODS = (0, 7, 30, 90)


def charts(models: list[dict], now: datetime | None = None) -> str:
    """The Charts view: one grid per CHART_PERIODS entry (tasks started in the last N days, 0 = any time);
    the script shows the one the period select picks, without it only the any-time grid shows."""
    now = now or datetime.now(timezone.utc)
    grids, options = [], []
    for days in CHART_PERIODS:
        shown = models if not days else [m for m in models if m["start"] and m["start"] >= now - timedelta(days=days)]
        label = f"started in the last {days} days" if days else "any time"
        options.append(f'<option value="{days}">{label}</option>')
        grids.append(f'<div class="charts" data-days="{days}"{" hidden" if days else ""}>{chart_grid(shown)}</div>')
    select = f'<div class="controls"><select id="chart-period" hidden title="tasks started in the period">{"".join(options)}</select></div>'
    return (f'<h2>Charts</h2>{select}<p class="muted">tracked tasks only (untracked runs left out); hover a bar or a dot for '
            f'the numbers; the filters of the task table do not apply</p>{"".join(grids)}')


SCRIPT = """
(function(){
var f=document.getElementById('filter'),g=document.getElementById('group'),st=document.getElementById('status'),
tb=document.querySelector('#overview tbody');
if(!f||!g||!st||!tb)return;f.hidden=false;g.hidden=false;st.hidden=false;
var RANK={running:0,stale:1,done:2,untracked:3};
var tasks=[].slice.call(tb.querySelectorAll('tr.task-row')),sess=[].slice.call(tb.querySelectorAll('tr.sess'));
var cols=document.querySelectorAll('#overview thead th').length;
function subs(t){return sess.filter(function(s){return s.dataset.task===t.dataset.id;});}
var per=document.getElementById('period'),pg=document.getElementById('pager'),page=0,
size=+pg.dataset.size||25,now=+document.body.dataset.now||Date.now()/1000;per.hidden=false;pg.hidden=false;
var rowOf=Object.create(null);tasks.forEach(function(t){rowOf['task-'+t.dataset.id]=t;});
function totals(rows){var w=0,cr=0,seen=0,est='';rows.forEach(function(r){w+=+r.dataset.w;cr+=+r.dataset.cr;seen+=+r.dataset.seen;if(r.dataset.est==='1')est='\\u2248';});
return rows.length+(rows.length===1?' task':' tasks')+' \\u00b7 '+est+'$'+(w>=0.01||!w?w.toFixed(2):w.toFixed(4))+' \\u00b7 cache '+(seen?Math.round(100*cr/seen)+'%':'n/a');}
function render(){[].forEach.call(tb.querySelectorAll('tr.group'),function(h){h.remove();});
var q=f.value.trim().toLowerCase(),sv=st.value,days=+per.value,mode=g.value,groups=[],by=Object.create(null);
function shown(el){return (!q||(el.dataset.text||'').indexOf(q)>=0)&&(sv==='all'||el.dataset.status===sv)
&&(!days||+el.dataset.last>=now-days*86400);}
tasks.forEach(function(t){t.hit=shown(t);
t.cells[0].style.paddingLeft=mode==='tree'&&+t.dataset.depth?(1.5*t.dataset.depth)+'em':'';
var k=mode==='status'?t.dataset.status:mode==='repo'?t.dataset.repo:mode==='intake'?t.dataset.intake:mode==='session'?t.dataset.root:mode==='tree'?t.dataset.tree:'';
if(!by[k]){by[k]=[];groups.push(k);}by[k].push(t);});
if(mode==='status')groups.sort(function(a,b){return RANK[a]-RANK[b];});
var ordered=[];groups.forEach(function(k){if(mode==='tree')by[k].sort(function(a,b){return a.dataset.treeOrder-b.dataset.treeOrder;});
by[k].forEach(function(t){if(t.hit)ordered.push(t);});});
var pages=Math.max(1,Math.ceil(ordered.length/size));page=Math.min(page,pages-1);
var on=ordered.slice(page*size,(page+1)*size);tasks.forEach(function(t){t.hidden=on.indexOf(t)<0;});
groups.forEach(function(k){var rows=by[k],vis=rows.filter(function(t){return t.hit;});
if(mode!=='none'&&rows.some(function(t){return !t.hidden;})&&!(mode==='tree'&&rows.length===1)){
var h=document.createElement('tr');h.className='group';var c=document.createElement('td');c.colSpan=cols;
c.textContent=(mode==='repo'||mode==='status'||mode==='intake'?k:mode==='tree'?rows[0].dataset.treeTitle:rows[0].dataset.rootTitle)+' \\u00b7 '+totals(vis);
h.appendChild(c);tb.appendChild(h);}
rows.forEach(function(t){tb.appendChild(t);subs(t).forEach(function(s){s.hidden=t.hidden||mode!=='session';tb.appendChild(s);});});});
document.getElementById('shown').textContent='shown: '+totals(ordered)+(ordered.length<tasks.length?' (of '+tasks.length+')':'');
document.getElementById('pageno').textContent='page '+(page+1)+' / '+pages;
document.getElementById('prev').disabled=page===0;document.getElementById('next').disabled=page>=pages-1;
[].forEach.call(document.querySelectorAll('details.task'),function(d){var r=rowOf[d.id];d.hidden=!!r&&r.hidden;});}
function reset(){page=0;render();}
document.getElementById('prev').addEventListener('click',function(){page--;render();});
document.getElementById('next').addEventListener('click',function(){page++;render();});
per.addEventListener('change',reset);
f.addEventListener('input',reset);g.addEventListener('change',reset);st.addEventListener('change',reset);
var KEY='simo-dev-dashboard-state',PREF='simo-dev-dashboard-refresh',sorters=[],sortedBy=null;
[].forEach.call(document.querySelectorAll('#overview thead th'),function(th,i){th.tabIndex=0;th.className='sortable';sorters[i]=sort;
function sort(){var dir=th.dataset.dir==='asc'?'desc':'asc';sortedBy={i:i,dir:dir};
[].forEach.call(th.parentNode.children,function(h){delete h.dataset.dir;h.removeAttribute('aria-sort');});
th.dataset.dir=dir;th.setAttribute('aria-sort',dir==='asc'?'ascending':'descending');
tasks.sort(function(a,b){var x=a.cells[i],y=b.cells[i],c;
if(x.dataset.v!==undefined&&y.dataset.v!==undefined){c=parseFloat(x.dataset.v)-parseFloat(y.dataset.v);}
else{c=x.textContent.localeCompare(y.textContent);}return dir==='asc'?c:-c;});reset();}
th.addEventListener('click',sort);th.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();sort();}});});
function set(el,v,d){el.value=v;if(el.value!==String(v))el.value=d;}
var cp=document.getElementById('chart-period'),CPREF='simo-dev-dashboard-chart-period';
function chartPer(){[].forEach.call(document.querySelectorAll('div.charts[data-days]'),function(d){d.hidden=d.dataset.days!==cp.value;});}
if(cp){cp.hidden=false;var cv='0';try{cv=localStorage.getItem(CPREF)||'0';}catch(e){}set(cp,cv,'0');chartPer();
cp.addEventListener('change',function(){try{localStorage.setItem(CPREF,cp.value);}catch(e){}chartPer();});}
var views=[].slice.call(document.querySelectorAll('section.view')),nav=document.querySelector('nav.tabs'),view='';
function show(id){if(!views.some(function(v){return v.id===id;}))id=views[0].id;view=id;
views.forEach(function(v){v.hidden=v.id!==id;});
[].forEach.call(nav.querySelectorAll('a'),function(a){a.className=a.getAttribute('href')==='#'+id?'on':'';});}
function viewOf(hash){var el;try{el=hash&&document.getElementById(decodeURIComponent(hash.slice(1)));}catch(e){el=null;}
while(el&&!(el.tagName==='SECTION'&&el.classList.contains('view')))el=el.parentElement;return el?el.id:'';}
if(nav&&views.length){nav.hidden=false;}
var saved=null;try{saved=JSON.parse(sessionStorage.getItem(KEY)||'null');sessionStorage.removeItem(KEY);}catch(e){saved=null;}
if(saved){f.value=saved.q||'';set(st,saved.st,'all');set(g,saved.g,'none');set(per,saved.per,'0');
if(saved.sort&&sorters[saved.sort.i]){sorters[saved.sort.i]();if(saved.sort.dir==='desc')sorters[saved.sort.i]();}
page=+saved.page||0;}
if(nav&&views.length)show(viewOf(location.hash)||(saved&&saved.view)||views[0].id);
render();
if(saved){[].forEach.call(document.querySelectorAll('details.task'),function(d){d.open=(saved.open||[]).indexOf(d.id)>=0;});
window.scrollTo(0,+saved.y||0);}
var rf=document.getElementById('refresh'),box=document.getElementById('refresh-box'),loaded=Date.now();
if(rf&&box){box.hidden=false;var pref='5';try{pref=localStorage.getItem(PREF)||'5';}catch(e){}set(rf,pref,'5');
rf.addEventListener('change',function(){try{localStorage.setItem(PREF,rf.value);}catch(e){}loaded=Date.now();});
setInterval(function(){var m=+rf.value;if(!m||document.visibilityState!=='visible'||Date.now()-loaded<m*60000)return;
if(document.activeElement===f&&f.value)return;
try{sessionStorage.setItem(KEY,JSON.stringify({q:f.value,st:st.value,g:g.value,per:per.value,page:page,sort:sortedBy,y:window.scrollY,view:view,
open:[].map.call(document.querySelectorAll('details.task[open]'),function(d){return d.id;})}));}catch(e){}
location.reload();},10000);}
function openHash(keep){var v=viewOf(location.hash);if(v&&nav&&views.length)show(v);
var el;try{el=location.hash&&document.getElementById(decodeURIComponent(location.hash.slice(1)));}catch(e){return;}
if(el&&el.tagName==='DETAILS'){el.open=true;if(keep!==true)el.scrollIntoView();}}
window.addEventListener('hashchange',openHash);openHash(!!saved);
[].forEach.call(document.querySelectorAll('a[href^="#task-"]'),function(a){a.addEventListener('click',function(){
var d=document.getElementById(a.getAttribute('href').slice(1));if(d&&d.tagName==='DETAILS'){if(nav&&views.length)show(viewOf('#'+d.id));d.open=true;}});});
})();
"""


def _task_ref(tid: str, names: dict) -> str:
    return f'<a href="#task-{_e(tid)}">{_e(names[tid])}</a>' if tid in names else _e(tid)


def task_section(m: dict, open_: bool = False, names: dict | None = None) -> str:
    pct = cache_pct(m["total"])
    names = names or {}
    tr = m.get("tree") or {}
    links = []
    if tr.get("parent"):
        links.append(f'parent task: {_task_ref(tr["parent"], names)}')
    if tr.get("children"):
        links.append("follow-ups: " + ", ".join(_task_ref(c, names) for c in tr["children"]))
    related = f'<p class="muted">{" · ".join(links)}</p>' if links else ""
    ticket = _link(m.get("ticket_url", ""), m["ticket"]) or _e(m["ticket"] or "no ticket")
    mr = _link(m["mr"], _mr_text(m["mr"]))
    meta = " · ".join(x for x in (_badge(m), ticket, _e(m["repo"] or "n/a"), _e(m["branch"] or "n/a"), mr,
                                  _dur(m["start"], m["end"]), f"{_cost_label(m)} est.", f"cache {_pct(pct)}") if x)
    if m["unpriced"]:
        related += (f'<p class="muted">≈ not priced exactly (unknown model: as {_e(tu.FALLBACK_MODEL)}; fast mode: at standard rates): '
                    f'{_e(", ".join(m["unpriced"]))}</p>')
    if m["estimated"]:
        related += f'<p class="muted">≈ no model data in the attribution event: priced as {_e(tu.FALLBACK_MODEL)}</p>'
    return (f'<details class="task" id="task-{_e(m["id"])}" data-text="{_e(_text_key(m))}" data-status="{m["status"]}"'
            f' data-last="{_ts_v(m["end"])}"{" open" if open_ else ""}>'
            f'<summary><span class="name">{_e(_name(m))}</span> <small class="muted">{_e(m["id"])}</small>'
            f'<br><span class="muted">{meta}</span></summary>'
            f'{related}<p class="muted">tokens from {_e(m["source"])}</p>{timeline_svg(m)}{events_list(m)}{tokens_table(m)}</details>')


def render_html(models: list[dict], now: datetime | None = None) -> str:
    names = {m["id"]: _name(m) for m in models}
    sections = "".join(task_section(m, i == 0, names) for i, m in enumerate(_by_status(models)))
    now = now or datetime.now(timezone.utc)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>simo-dev flow dashboard</title><style>{CSS}</style></head><body data-now=\"{_ts_v(now)}\">"
        f'<h1>simo-dev flow dashboard</h1><p class="muted">generated {_hm(now)} UTC · status: done = the task has an outcome '
        f'(its result shown), running = open with activity in the last {STALE_AFTER_H}h, stale = open with none since; '
        f'"ago" counts from generation · $ est. = list price of each record\'s model '
        f'(transcript_usage.PRICES; 1h cache writes at the 1h rate; server tools not included) · ≈ = an estimate: some tokens '
        f'priced as {_e(tu.FALLBACK_MODEL)} (unknown model, or an attribution event with no model data) or fast mode at standard rates · '
        f'human waits are estimated: a gap over {WAIT_MIN_S}s before a real user turn · '
        'pipeline 1st: not recorded yet, the mr-followup rounds are the proxy</p>'
        '<div class="controls"><label class="muted" id="refresh-box" hidden '
        'title="reloads this file, keeping filters, page, sort, scroll and open sections; the file is as fresh as its last '
        'generation (at each merge, or a timer of your own)">auto-refresh <select id="refresh"><option value="0">off</option>'
        '<option value="1">every 1 min</option><option value="5">every 5 min</option><option value="15">every 15 min</option>'
        '</select></label></div>'
        f"{_views(models, sections, now)}<script>{SCRIPT}</script></body></html>\n")


def _tasks_label(models: list[dict]) -> str:
    n = sum(1 for m in models if m.get("untracked"))
    return f"{len(models) - n} + {n} untracked" if n else str(len(models))


def _views(models: list[dict], sections: str, now: datetime | None = None) -> str:
    """The page's sections, one tab each once the script runs; without it they follow one another."""
    views = [("overview", "Overview", f"{summary_cards(models)}{steps_overview(models)}"),
             ("charts", "Charts", charts(models, now)),
             ("tasks", f"Tasks ({_tasks_label(models)})", f"<h2>Tasks</h2>{overview(models)}{sections}")]
    nav = "".join(f'<a href="#view-{key}">{_e(label)}</a>' for key, label, _ in views)
    return (f'<nav class="tabs" hidden>{nav}</nav>'
            + "".join(f'<section class="view" id="view-{key}">{body}</section>' for key, _, body in views))


def build(directory: Path, only: str = "", cache_path: Path | None = None,
          now: datetime | None = None) -> list[dict]:
    """The task models, then (without `only`) the untracked runs of the out-of-task invocations."""
    now = now or datetime.now(timezone.utc)
    tasks = all_tasks = load_tasks(directory)
    tree = task_tree({tid: _parent(events) for tid, events in tasks}, [tid for tid, _ in tasks])
    orphans = load_orphans(directory, tasks)
    bounds = owned_spans(tasks)
    if only:
        tasks = [t for t in tasks if t[0] == only]
        if not tasks:
            raise ValueError(f"no task {only}")
    disk = load_disk_cache(cache_path) if cache_path else None
    before = dict(disk or {})
    store = SessionStore(disk)
    models = [build_task(task_id, events, bounds.get(task_id), store) for task_id, events in tasks]
    memo: dict = {}
    for m in models:
        m["tree"] = tree[m["id"]]
        m["ticket_url"] = ticket_url(m["ticket"], browse_template(m["ticket"], memo))
    if not only:
        models += untracked_runs(all_tasks, bounds, orphans, store)
    for m in models:
        set_status(m, now)
    if cache_path:
        # A full run keeps only the sessions it used; a --task run leaves the others alone.
        kept = disk if only else {sid: disk[sid] for sid in store if sid in disk}
        if kept.keys() != before.keys() or any(kept[sid] is not before[sid] for sid in kept):
            save_disk_cache(cache_path, kept)
    return models


def default_out() -> Path:
    return Path(os.environ.get("SIMO_DEV_DASHBOARD")
                or os.path.expanduser("~/.claude/state/dev-flow-dashboard.html"))


def default_cache() -> Path:
    return Path(os.environ.get("SIMO_DEV_DASHBOARD_CACHE")
                or os.path.expanduser("~/.claude/state/dev-flow-dashboard-cache.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow_dashboard.py")
    parser.add_argument("--out", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--no-reconcile", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else default_out()
    tmp = None
    if not args.no_reconcile and not args.task:
        try:
            closed = ft.reconcile()
            for tid, result, url in closed:
                print(f"reconciled: {tid} {result} {url}")
            if closed:
                ft.render()
        except (ValueError, OSError) as exc:  # a stale task is better than no page
            print(f"flow_dashboard: reconcile skipped: {exc}", file=sys.stderr)
    try:
        now = datetime.now(timezone.utc)
        models = build(ft.telemetry_dir(), args.task, default_cache(), now)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f"{out.name}.tmp.{os.getpid()}")
        tmp.write_text(render_html(models, now), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(out)
    except (ValueError, OSError) as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        print(f"flow_dashboard: {exc}", file=sys.stderr)
        return 2
    print(f"dashboard: {_tasks_label(models)} tasks -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
