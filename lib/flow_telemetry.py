#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Flow events of the simo-dev flow: one JSONL file per task.

A task ID (`<YYYYMMDDTHHMM>-<session8>`) is minted by `start` (task-intake step 1)
and stored in the flow_state session flag; the worktree marker inherits it.
Events go to <dir>/<task-id>.jsonl; outside a task only `step_start` is kept, in
<dir>/session-<session-id>.jsonl. <dir> is ~/.claude/state/dev-flow, or
$SIMO_DEV_TELEMETRY_DIR. Events carry names, short answers, counts and times —
never prompts, code or diffs; `outcome` alone has three summary fields.
A session that never took part in a task and runs a work step of the flow (ADOPT_SKILLS:
the Skill hook calls `adopt`) gets an adopted task: telemetry only, the flow state is
left as it was. Its ID sits in <dir>/adopted/<session-id>, read by current_task but never
by flow_state.is_active; it is the `task id:` line of the session's first prompt (a
hand-off child joins its task, unless that task has an outcome), else a new ID, and the
prompt's `parent task:` line becomes its `task_link`. A later `start` in that session
releases the adopted task (handoff gate) and clears its ID.
On every `start` and `adopt`, `session-*.jsonl` files, adopted IDs, and task files with no `outcome` that went untouched for 90 days are pruned; a finished task stays, so the journal keeps it.

    flow_telemetry.py start --session S [--repo P] [--task-id T] [--parent-task T]
    flow_telemetry.py current [--session S] [--cwd P]
    flow_telemetry.py event --type T [--session S] [--cwd P] [--task T] [--field k=v]...
    flow_telemetry.py close [--session S] [--task T]
    flow_telemetry.py attribute (--task T | --session S)
    flow_telemetry.py render [--out PATH]
    flow_telemetry.py reconcile
`reconcile` closes tasks whose MR/PR was merged outside mr-followup's post-merge step (merged from
the forge UI, by a batch session, after the session was archived). For each task with an
`mr_created` gate and no `outcome`, the MR/PR is the `pr-link` record nearest in time to the last
such gate in the gate's session transcript, within LINK_GAP_S (task_mr_url; `task_bound` has no
forge host, so a task without such a record stays open). Its state comes from `glab api` (JSON
parsed with strict=False: glab can emit raw control characters) or `gh pr view` (github.com only).
A merged one, merged at least RECONCILE_GRACE_S ago from the task_bound branch, gets an `outcome`
with result `merged`, no session, dated at merged_at, never at now. The grace period lets a live
post-merge step record it first; it is no guarantee: a step 8.5 run later still appends its own
outcome, and every reader takes the latest. The outcome's ts matters because it bounds the
outcome bounds the task's cost window (flow_dashboard._task_until). A closed or open MR/PR, a
branch mismatch, an unknown URL shape or a forge failure leave the task open. No attribution event
is added (the dashboard reads the transcripts). Each open task is queried at most once every
RECONCILE_EVERY_S (<dir>/reconcile-checked.json, plus its .lock: a run finding it held skips;
with no open task and no cache yet, nothing is written).
The CLI re-renders the journal when it closed any; flow_dashboard.py runs it before a full build.
`--parent-task` appends a `task_link` event naming the task whose session started
this one; a value that is not a well-formed task ID, or is this task's own ID, is
ignored with a warning (an unknown ID is kept).
`attribute` sums the four token categories of the task's sessions (main thread +
subagents, deduped by message.id, lib/transcript_usage.py) per step, inside the task's
time window, and appends an `attribution` event whose `by_step` replaces the old
per-transcript-skill grouping. The step model (`step_marks`, `split_by_step`), shared
with lib/flow_dashboard.py and the journal: per session a step opens at each step_start
of a flow step (STEP_SKILLS; any other skill is a helper and its cost stays on the step
that called it, unless the session has no step yet), and at a retry of a flow step other than the open one (the flow went
back to it). A skill has no end signal, so one gate opens a step: the `review_exit` of
commit's pre-flight review-loop reopens commit.
A main-thread record goes to the step open in its session at its own time, a
subagent's whole transcript to the step open at its launch (its first usage record),
labelled with its agentType; one launched before any step goes by each record's own
time. A record before its own session's first step goes to the step open at that time
in any session (the latest started before it), so `(none)` holds only what precedes
the task's first step. The summary line also prints the estimated $ cost
(transcript_usage.PRICES, each record at its own model's list price; an unknown model is
priced as the fallback and a fast-mode record at standard rates, both counted in the note
and named in the event's `unpriced`); the
event carries `cost` (USD per category) and `unpriced`. With `--session` alone there are no step events, so only
the summary line is printed. `event --type outcome` on a task attributes it too (same
event, same summary line), so a close typed by hand cannot leave a finished task without
its cost; a failure there is a warning naming the `attribute --task` re-run, and the
outcome stays. `render` rewrites the journal whole from every task file;
a journal not starting with the generated header is moved once to
dev-flow-journal-archive.md next to it. `close` clears the session's task ID after the
merge (the flag's or the adopted one) and leaves the flow as it was; with `--task T` it does so
only when T is the task the session holds now (rc 1 otherwise: the session moved on).
rc 0 done (an event outside a task is skipped, still rc 0), 1 `current` found
no task, 2 refused (bad type or field, unsafe task ID).
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

try:
    import fcntl
except ImportError:  # Windows: reconcile runs unlocked
    fcntl = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_state  # noqa: E402
import transcript_usage as tu  # noqa: E402

PRUNE_AFTER_S = 90 * 24 * 3600
SHORT_MAX = 120
LONG_MAX = 600
LONG_FIELDS = {"task", "friction", "notes"}
INT_FIELDS = {"iteration", "sessions", "api_retries"}
FIELDS = {
    "step_start": {"skill"},
    "task_bound": {"ticket", "branch", "repo"},
    "gate": {"skill", "gate", "answer"},
    "retry": {"skill", "iteration"},
    "outcome": {"result", "mr", "task", "friction", "notes"},
    "attribution": {"by_step", "total", "cost", "unpriced", "sessions", "api_retries", "note"},
    "task_link": {"parent"},
}
REQUIRED = {
    "step_start": {"skill"},
    "task_bound": set(),
    "gate": {"gate", "answer"},
    "retry": {"skill", "iteration"},
    "outcome": {"result"},
    "attribution": {"total"},
    "task_link": {"parent"},
}
TASK_ID_RE = flow_state.TASK_ID_RE


def telemetry_dir() -> Path:
    return Path(os.environ.get("SIMO_DEV_TELEMETRY_DIR")
                or os.path.expanduser("~/.claude/state/dev-flow"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_task_id(session: str, now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    short = re.sub(r"[^A-Za-z0-9]", "", session)[:8] or "nosess"
    return f"{dt:%Y%m%dT%H%M}-{short}"


def _safe_name(name: str) -> str:
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"unsafe file name: {name!r}")
    return name


def task_file(task_id: str) -> Path:
    return telemetry_dir() / f"{_safe_name(task_id)}.jsonl"


def session_file(session: str) -> Path:
    return telemetry_dir() / f"session-{_safe_name(session)}.jsonl"


def build_event(etype: str, task: str, session: str, fields: dict) -> dict:
    if etype not in FIELDS:
        raise ValueError(f"unknown event type: {etype}")
    extra = set(fields) - FIELDS[etype]
    if extra:
        raise ValueError(f"field(s) not allowed for {etype}: {', '.join(sorted(extra))}")
    missing = REQUIRED[etype] - set(fields)
    if missing:
        raise ValueError(f"missing field(s) for {etype}: {', '.join(sorted(missing))}")
    for key, value in fields.items():
        if not isinstance(value, str):
            continue
        cap = LONG_MAX if key in LONG_FIELDS else SHORT_MAX
        if len(value) > cap:
            raise ValueError(f"{key} longer than {cap} chars")
        if key not in LONG_FIELDS and ("\n" in value or "\r" in value):
            raise ValueError(f"{key} must be one line")
    return {"ts": now_iso(), "type": etype, "task": task, "session": session, **fields}


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def read_events(path: Path) -> list[dict]:
    events = []
    try:
        # split("\n"), not splitlines(): ensure_ascii=False leaves NEL/U+2028/U+2029 raw in a line.
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return events
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


ADOPT_SKILLS = frozenset({
    "simo-dev:plan-write", "simo-dev:plan-execute", "simo-dev:review-loop", "simo-dev:commit", "simo-dev:mr-followup",
})
_PROMPT_ID_RE = re.compile(r"^[ \t>*_-]*`?(task id|parent task)`?[*_]*:[*_`]*[ \t]*`?([^`\s*]+)`?[ \t]*$",
                           re.MULTILINE | re.IGNORECASE)


def adopted_file(session: str) -> Path:
    return telemetry_dir() / "adopted" / _safe_name(session)


def adopted_task(session: str) -> str:
    try:
        task = adopted_file(session).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""
    return task if TASK_ID_RE.fullmatch(task) else ""


def current_task(session: str, cwd: str) -> str:
    return flow_state.session_task(session) or adopted_task(session) or flow_state.worktree_task(cwd)


def _first_prompt(transcript: str) -> tuple[str, str]:
    """(text of the session's first user message that has text, timestamp of its first timed record)."""
    first_ts = ""
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if not first_ts and isinstance(obj.get("timestamp"), str) and _parse_ts(obj["timestamp"]):
                    first_ts = obj["timestamp"]
                if obj.get("type") != "user" or obj.get("isMeta"):
                    continue
                content = (obj.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
                if isinstance(content, str) and content.strip():
                    return content, first_ts
    except OSError:
        pass
    return "", first_ts


def prompt_ids(transcript: str) -> tuple[str, str]:
    """(`task id:`, `parent task:`) lines of the session's first prompt, each '' when absent or not a task ID."""
    found = {"task id": "", "parent task": ""}
    for key, value in _PROMPT_ID_RE.findall(_first_prompt(transcript)[0] if transcript else ""):
        if not found[key.lower()] and TASK_ID_RE.fullmatch(value):
            found[key.lower()] = value
    return found["task id"], found["parent task"]


def _in_a_task(session: str) -> bool:
    needle = json.dumps(session)
    for path in telemetry_dir().glob("*.jsonl"):
        if path.name.startswith("session-"):
            continue
        try:
            if needle in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
    return False


def adopt(session: str, skill: str, transcript: str = "") -> str:
    """Give a session that never took part in a task a telemetry-only one at its first work step;
    '' (nothing written) when the skill is not a work step or the session already had a task, so a
    session whose task step 8.5 closed never reopens it. The `adopted` gate is dated at the
    transcript's first record, so the work done before that step falls in the task's window, and
    the session's earlier `session-*.jsonl` lines move into the task. A `task id:` of a finished
    task is not joined: it becomes the parent when the prompt names none. The adopted ID is
    written last; an I/O failure midway leaves a partial task file, and the session is not
    adopted later."""
    if skill not in ADOPT_SKILLS or _in_a_task(session):
        return ""
    task_id, parent = prompt_ids(transcript)
    if task_id and any(e.get("type") == "outcome" for e in read_events(task_file(task_id))):
        task_id, parent = "", parent or task_id
    task_id = task_id or new_task_id(session)
    first = _parse_ts(_first_prompt(transcript)[1]) if transcript else None
    gate = build_event("gate", task_id, session, {"skill": skill, "gate": "adopted", "answer": "no-intake"})
    if first and first.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") < gate["ts"]:
        gate["ts"] = first.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = task_file(task_id)
    earlier = session_file(session)
    for e in read_events(earlier):
        append_event(path, {**e, "task": task_id})
    append_event(path, gate)
    record("step_start", session, "", {"skill": skill}, task=task_id)
    if parent and parent != task_id:
        record("task_link", session, "", {"parent": parent}, task=task_id)
    earlier.unlink(missing_ok=True)
    marker = adopted_file(session)
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, f"{task_id}\n".encode("utf-8"))
    finally:
        os.close(fd)
    prune(telemetry_dir())
    return task_id


def record(etype: str, session: str, cwd: str, fields: dict, task: str = "") -> Path | None:
    """Append one event; None when it was skipped (not step_start, no active task)."""
    task = task or current_task(session, cwd)
    if task:
        path = task_file(task)
    elif etype == "step_start" and session:
        path = session_file(session)
    else:
        return None
    append_event(path, build_event(etype, task, session, fields))
    return path


def prune(directory: Path, now: float | None = None) -> None:
    now = now or time.time()
    for path in directory.glob("*.jsonl"):
        try:
            if now - path.stat().st_mtime <= PRUNE_AFTER_S:
                continue
            finished = not path.name.startswith("session-") and any(
                e.get("type") == "outcome" for e in read_events(path))
            if not finished:
                path.unlink()
        except OSError:
            pass
    for path in [*(directory / "adopted").glob("*"), *(directory / "reuse-warned").glob("*")]:
        try:
            if now - path.stat().st_mtime > PRUNE_AFTER_S:
                path.unlink()
        except OSError:
            pass


def start(session: str, repo: str = "", task_id: str = "", parent_task: str = "") -> str:
    task_id = task_id or new_task_id(session)
    if not session or not TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"unsafe task id or no session: {task_id!r}")
    adopted = adopted_task(session)
    if not flow_state.set_session(session, True, task_id):
        raise ValueError("could not write the session flag")
    if adopted:  # an intake after an adoption ends the adopted task's share of this session
        if adopted != task_id:
            record("gate", session, "", {"gate": "handoff", "answer": "released"}, task=adopted)
        adopted_file(session).unlink(missing_ok=True)
    if repo:
        flow_state.set_worktree(repo, True, task_id)
    record("step_start", session, repo, {"skill": "simo-dev:task-intake"}, task=task_id)
    if parent_task and TASK_ID_RE.fullmatch(parent_task) and parent_task != task_id:
        record("task_link", session, repo, {"parent": parent_task}, task=task_id)
    elif parent_task:  # a bad line in a spawned prompt must not cost the task its telemetry
        print(f"flow_telemetry: parent task ignored, not a task id or this task's own: {parent_task!r}", file=sys.stderr)
    prune(telemetry_dir())
    return task_id


GENERATED = "<!-- generated by flow_telemetry.py render: edit the events, not this file -->"


_parse_ts = tu.parse_ts


def _window(events: list[dict]) -> tuple[datetime | None, datetime | None]:
    times = [t for t in (_parse_ts(e.get("ts", "")) for e in events) if t]
    ends = [_parse_ts(e.get("ts", "")) for e in events if e.get("type") == "outcome"]
    ends = [t for t in ends if t]
    return (min(times) if times else None, max(ends) if ends else None)


NONE = "(none)"


def _zero() -> dict:
    return dict.fromkeys(tu.KEYS, 0)


def _plus(a: dict, b: dict) -> dict:
    return {key: a.get(key, 0) + b.get(key, 0) for key in tu.KEYS}


COMMIT, REVIEW_LOOP = "simo-dev:commit", "simo-dev:review-loop"
# The skills that open a step. Any other skill is a helper of the step that called it (a
# reviewer dispatch, a project skill): its step_start event is kept, its cost stays there.
STEP_SKILLS = ADOPT_SKILLS | {"simo-dev:task-intake", "simo-dev:grill-me"}


def step_marks(events: list[dict]) -> dict[str, list[tuple[datetime, str]]]:
    """Per session, when each step opens, oldest first: a step_start of a STEP_SKILLS skill, or a
    retry of one that is not the step open in that session (the flow went back to it). Any other
    skill opens a step only when the session has none yet: there is no caller to book it on (a
    session outside a task that ran only `mr-review`). A skill has no end signal, so one gate
    opens a step: review-loop opened while commit was the open step is commit's pre-flight, and
    its `review_exit` gate reopens commit, whatever the answer (commit decides what comes next)."""
    timed = sorted(((t, e) for t, e in ((_parse_ts(e.get("ts", "")), e) for e in events) if t), key=lambda r: r[0])
    out: dict[str, list[tuple[datetime, str]]] = {}
    preflight: set[str] = set()  # sessions whose open step is a pre-flight review-loop
    for t, e in timed:
        sid, skill, kind = e.get("session"), e.get("skill"), e.get("type")
        if not isinstance(sid, str) or not sid or not isinstance(skill, str) or not skill:
            continue
        marks = out.setdefault(sid, [])
        opened = marks[-1][1] if marks else ""
        if (kind == "step_start" and (skill in STEP_SKILLS or not marks)) or (
                kind == "retry" and skill in STEP_SKILLS and opened != skill):
            if skill == REVIEW_LOOP and opened == COMMIT:
                preflight.add(sid)
            else:
                preflight.discard(sid)
            marks.append((t, skill))
        elif kind == "gate" and e.get("gate") == "review_exit" and sid in preflight:
            preflight.discard(sid)
            marks.append((t, COMMIT))
    return out


def split_by_step(marks: dict[str, list[tuple[datetime, str]]], records: Iterable[dict]) -> list[dict]:
    """Each usage record to one step: a main-thread record to the step open in its session at its
    own time, a subagent's (`agent` set) to the step open at its `launch` (its own time when
    that does not parse, or when no step was open yet at the launch). A record before its
    session's first step (a hand-off child's first turn) goes to the step open at that time
    in any session (the latest started before it); before the task's first step, to
    `(none)`. Every step is listed, even with no tokens; the rows sum to the records. Each row
    also carries `cost` (USD per category, transcript_usage.cost) and `agent_costs`.
    A row's `tokens` and `cost` already include its subagents: `agents` and `agent_costs` break
    that sum down by agent type, so adding them to it counts the agents twice."""
    rows: dict[tuple[str, int], dict] = {}
    times: dict[str, list[datetime]] = {}
    for sid, steps in marks.items():
        times[sid] = [t for t, _ in steps]
        for i, (t, skill) in enumerate(steps):
            rows[(sid, i)] = {"skill": skill, "session": sid, "start": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "tokens": _zero(), "agents": {}, "cost": tu.zero_cost(), "agent_costs": {}}
    anywhere = sorted((t, sid, i) for sid, steps in marks.items() for i, (t, _) in enumerate(steps))
    anywhere_times = [t for t, _, _ in anywhere]
    for rec in records:
        sid, row = rec.get("sid", ""), None
        for at in (_parse_ts(rec.get("launch")), _parse_ts(rec.get("ts", ""))):
            if not at:
                continue
            i = bisect.bisect_right(times.get(sid, []), at) - 1
            if i >= 0:
                row = rows[(sid, i)]
            elif (j := bisect.bisect_right(anywhere_times, at) - 1) >= 0:
                row = rows[anywhere[j][1:]]
            if row:
                break
        if row is None:
            row = rows.setdefault((sid, -1), {"skill": NONE, "session": sid, "start": "", "tokens": _zero(), "agents": {},
                                              "cost": tu.zero_cost(), "agent_costs": {}})
        spent = tu.cost(rec)
        row["tokens"] = _plus(row["tokens"], rec["usage"])
        row["cost"] = _plus(row["cost"], spent)
        if rec.get("agent"):
            row["agents"][rec["agent"]] = _plus(row["agents"].get(rec["agent"], _zero()), rec["usage"])
            row["agent_costs"][rec["agent"]] = _plus(row["agent_costs"].get(rec["agent"], tu.zero_cost()), spent)
    return sorted(rows.values(), key=lambda r: (r["start"] != "", r["start"], r["session"]))


def attribute(task_id: str = "", session: str = "") -> dict:
    if task_id:
        events = read_events(task_file(task_id))
        if not events:
            raise ValueError(f"no events for task {task_id}")
        sessions = sorted({e["session"] for e in events if e.get("session")})
        lo, hi = _window(events)
        last = {}
        for e in events:
            ts = _parse_ts(e.get("ts", ""))
            if ts and e.get("session") and (e["session"] not in last or ts > last[e["session"]]):
                last[e["session"]] = ts
    else:
        events, sessions, lo, hi, last = [], [session], None, None, {}
    kept: dict[str, dict] = {}
    files = skipped = 0
    missing = []
    for sid in sessions:
        main_path = tu.find_main_transcript(sid)
        if not main_path:
            missing.append(sid)
            continue
        # Closed task: each session stops at its own last event, so an orchestrator
        # that handed the task to a child session stops counting at the hand-off.
        session_hi = min(last.get(sid, hi), hi) if hi else None
        for path in [main_path, *tu.find_subagent_transcripts(main_path)]:
            got = tu.read_usage(path)
            if got is None:
                continue
            files += 1
            skipped += got[1]
            agent = "" if path == main_path else tu.agent_type(path)
            launch = tu.launch_ts(got[0]) if agent else ""
            for key, rec in got[0].items():
                ts = _parse_ts(rec["ts"])
                if lo and (ts is None or ts < lo or (session_hi and ts > session_hi)):
                    continue
                kept[key] = {**rec, "sid": sid, "agent": agent, "launch": launch}
    note = f"{files} transcripts"
    if missing:
        note += f", {len(missing)} sessions without transcript"
    if skipped:
        note += f", {skipped} lines skipped"
    total_cost = tu.zero_cost()
    for rec in kept.values():
        total_cost = _plus(total_cost, tu.cost(rec))
    unpriced = tu.unpriced(kept.values())
    if unpriced:  # a count only: `note` is a short field, the names live in `unpriced`
        note += f", {len(unpriced)} model{'s' if len(unpriced) > 1 else ''} not priced exactly"
    return {"by_step": split_by_step(step_marks(events), kept.values()), "total": tu.totals(kept.values()),
            "cost": total_cost, "unpriced": unpriced, "sessions": len(sessions), "api_retries": sum(1 for rec in kept.values() if rec["retry"]), "note": note}


def open_tasks(session: str) -> list[tuple[str, str]]:
    """(task ID, branch) of the tasks this session still holds and that have no outcome, oldest
    first; a task the session released (its last event there is the handoff gate) is not held."""
    out, needle = [], json.dumps(session)
    for path in sorted(telemetry_dir().glob("*.jsonl")):
        if path.name.startswith("session-"):
            continue
        try:  # finished tasks are never pruned: parse only the files that name this session
            if needle not in path.read_text(encoding="utf-8", errors="replace"):
                continue
        except OSError:
            continue
        events = read_events(path)
        mine = [e for e in events if e.get("session") == session]
        if not mine or mine[-1].get("gate") == "handoff" or any(e.get("type") == "outcome" for e in events):
            continue
        out.append((path.stem, next((str(e["branch"]) for e in reversed(events)
                                     if e.get("type") == "task_bound" and e.get("branch")), "")))
    return out


def journal_path() -> Path:
    return Path(os.environ.get("SIMO_DEV_JOURNAL")
                or os.path.expanduser("~/.claude/state/dev-flow-journal.md"))


def _duration(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _usd(value: float) -> str:
    return f"${value:.2f}" if value >= 0.01 or not value else f"${value:.4f}"


def cost_text(attr: dict) -> str:
    """An attribution event's cost: its own `cost`, else (an event from before costs) its tokens at fallback prices."""
    spent = attr.get("cost")
    if isinstance(spent, dict) and all(k in spent and isinstance(spent[k], (int, float)) and not isinstance(spent[k], bool)
                                       for k in tu.KEYS):
        return f"{'≈' if attr.get('unpriced') else ''}{_usd(tu.dollars(spent))} est."
    return f"≈{_usd(tu.dollars(tu.estimate(attr.get('total') or {})))} est. (no model data, priced as {tu.FALLBACK_MODEL})"


def _tokens_line(attr: dict | None) -> str:
    if not attr:
        return "- tokens: not attributed"
    t = attr["total"]
    return (f"- tokens: {sum(t.values())}, {cost_text(attr)} (in={t['input_tokens']} cache_write={t['cache_creation_input_tokens']} "
            f"cache_read={t['cache_read_input_tokens']} out={t['output_tokens']}; {attr.get('note', '')})")


def render_task(task_id: str, events: list[dict]) -> str:
    events = sorted(events, key=lambda e: e.get("ts", ""))
    bound: dict = {}
    for e in events:
        if e.get("type") == "task_bound":
            bound.update({k: e[k] for k in ("ticket", "branch", "repo") if k in e})
    outcomes = [e for e in events if e.get("type") == "outcome"]
    outcome = outcomes[-1] if outcomes else {}
    attrs = [e for e in events if e.get("type") == "attribution"]
    links = [e for e in events if e.get("type") == "task_link" and e.get("parent")]
    steps = sorted(mark for marks in step_marks(events).values() for mark in marks)
    parts = []
    for i, (start_t, skill) in enumerate(steps):
        end_t = steps[i + 1][0] if i + 1 < len(steps) else _parse_ts(outcome.get("ts", ""))
        took = _duration((end_t - start_t).total_seconds()) if end_t else "open"
        parts.append(f"{skill} {took}")
    gates = [f"{e.get('gate')}={e.get('answer')}" for e in events
             if e.get("type") == "gate" and e.get("gate") != "handoff"]  # handoff: attribution bound only
    retries: dict[str, int] = {}
    for e in events:
        if e.get("type") == "retry":
            retries[e.get("skill", "?")] = retries.get(e.get("skill", "?"), 0) + 1
    result = outcome.get("result", "in progress")
    if outcome.get("mr"):
        result += f" ({outcome['mr']})"
    date = events[0].get("ts", "")[:10] if events else "n/a"
    lines = [
        f"## {date} · {bound.get('repo') or 'n/a'} · {bound.get('branch') or 'n/a'} · task {task_id}",
        f"- ticket: {bound.get('ticket') or 'none'}",
        *([f"- parent task: {links[-1]['parent']}"] if links else []),
        f"- task: {outcome.get('task', 'n/a')}",
        f"- result: {result}",
        f"- steps: {', '.join(parts) or 'n/a'}",
        f"- gates: {', '.join(gates) or 'none'}",
        f"- retries: {', '.join(f'{k} {v}' for k, v in retries.items()) or 'none'}",
        _tokens_line(attrs[-1] if attrs else None),
        f"- problems/friction: {outcome.get('friction', 'n/a')}",
        f"- notes: {outcome.get('notes', 'n/a')}",
    ]
    return "\n".join(lines)


def _archive_once(journal: Path) -> None:
    try:
        text = journal.read_text(encoding="utf-8")
    except OSError:
        return
    if text.startswith(GENERATED):
        return
    archive = journal.with_name("dev-flow-journal-archive.md")
    with open(archive, "a", encoding="utf-8") as f:
        if archive.stat().st_size:
            f.write("\n")
        f.write(text)
    journal.unlink()


def render(out: Path | None = None) -> int:
    tasks = []
    for path in telemetry_dir().glob("*.jsonl"):
        if path.name.startswith("session-"):
            continue
        events = read_events(path)
        if events:
            tasks.append((min(e.get("ts", "") for e in events), path.stem, events))
    tasks.sort()
    body = "\n\n".join(render_task(task_id, events) for _, task_id, events in tasks)
    journal = out or journal_path()
    journal.parent.mkdir(parents=True, exist_ok=True)
    _archive_once(journal)
    tmp = journal.with_name(f"{journal.name}.tmp.{os.getpid()}")
    tmp.write_text(f"{GENERATED}\n\n# simo-dev flow journal\n\n{body}\n", encoding="utf-8")
    tmp.replace(journal)
    return len(tasks)


RECONCILE_EVERY_S = 15 * 60
RECONCILE_GRACE_S = 2 * 3600
RECONCILE_CACHE = "reconcile-checked.json"
LINK_GAP_S = 10 * 60
FORGE_TIMEOUT_S = 20
_GITLAB_MR_RE = re.compile(r"^https://([^/]+)/(.+?)/-/merge_requests/(\d+)(?:[/?#].*)?$")
_GITHUB_PR_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/\d+(?:[/?#].*)?$")


def _utc(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr_links(transcript: str) -> list[tuple[datetime, str]]:
    links = []
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"pr-link"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(obj.get("timestamp", "")) if isinstance(obj, dict) else None
                if ts and obj.get("type") == "pr-link" and isinstance(obj.get("prUrl"), str):
                    links.append((ts, obj["prUrl"]))
    except OSError:
        pass
    return links


def task_mr_url(events: list[dict]) -> str:
    """The MR/PR the task opened: the `pr-link` record of the session of its last `mr_created` gate
    nearest in time to that gate. A later link in the session (another MR opened or only cited
    there) is never preferred for coming last. '' when there is no such gate or record."""
    gates = sorted((t, e["session"]) for e in events if e.get("type") == "gate" and e.get("gate") == "mr_created"
                   and e.get("session") and (t := _parse_ts(e.get("ts", ""))))
    if not gates:
        return ""
    at, sid = gates[-1]
    path = tu.find_main_transcript(sid)
    links = [(abs((t - at).total_seconds()), url) for t, url in (_pr_links(path) if path else [])]
    near = [link for link in links if link[0] <= LINK_GAP_S]
    return min(near, key=lambda link: link[0])[1] if near else ""


def forge_state(url: str, run=None) -> dict | None:
    """{state: open|merged|closed, at: UTC ts or '', branch: source branch} of an MR/PR; None when the
    URL is not a GitLab MR or a github.com PR, or the forge CLI fails or answers something else."""
    run = run or subprocess.run
    if m := _GITLAB_MR_RE.match(url):
        cmd = ["glab", "api", "--hostname", m[1], f"projects/{quote(m[2], safe='')}/merge_requests/{m[3]}"]
        states, at, branch = {"opened": "open", "merged": "merged", "closed": "closed"}, "merged_at", "source_branch"
    elif _GITHUB_PR_RE.match(url):
        cmd = ["gh", "pr", "view", url, "--json", "state,mergedAt,headRefName"]
        states, at, branch = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}, "mergedAt", "headRefName"
    else:
        return None
    try:
        got = run(cmd, capture_output=True, text=True, timeout=FORGE_TIMEOUT_S)
        data = json.loads(got.stdout, strict=False) if got.returncode == 0 else None  # glab can emit raw control chars
    except (OSError, ValueError, subprocess.SubprocessError):  # ValueError: JSON and UTF-8 decode errors
        return None
    if not isinstance(data, dict) or data.get("state") not in states:
        return None
    ts = _parse_ts(data.get(at) or "")
    return {"state": states[data["state"]], "at": _utc(ts) if ts else "",
            "branch": data.get(branch) if isinstance(data.get(branch), str) else ""}


def _reconcile_one(tid: str, events: list[dict], run, now: float) -> tuple[str, str, str] | None:
    url = task_mr_url(events)
    got = forge_state(url, run) if url else None
    if not got or got["state"] != "merged" or not got["at"]:
        return None
    branch = next((e["branch"] for e in reversed(events) if e.get("type") == "task_bound" and e.get("branch")), "")
    if branch and got["branch"] and branch != got["branch"]:
        return None
    if now - _parse_ts(got["at"]).timestamp() < RECONCILE_GRACE_S:  # mr-followup's post-merge step may still record it
        return None
    if any(e.get("type") == "outcome" for e in read_events(task_file(tid))):  # closed meanwhile
        return None
    fields = {"result": "merged",
              "notes": "outcome reconciled: merged outside the post-merge step (flow_telemetry.py reconcile)"}
    if len(url) <= SHORT_MAX:
        fields["mr"] = url
    event = build_event("outcome", tid, "", fields)
    event["ts"] = got["at"]
    append_event(task_file(tid), event)
    return tid, "merged", url


def reconcile(run=None, now: float | None = None) -> list[tuple[str, str, str]]:
    """Close the open tasks with an `mr_created` gate whose MR/PR was merged at least RECONCILE_GRACE_S
    ago, from a branch matching the task's `task_bound` one: append an `outcome` dated at the merge.
    A closed (not merged) MR/PR leaves the task open. Each open task is queried at most every
    RECONCILE_EVERY_S (<dir>/RECONCILE_CACHE, written after each task); a run that finds
    <dir>/RECONCILE_CACHE.lock held returns at once. Returns (task, result, url) per task closed."""
    now = now or time.time()
    directory = telemetry_dir()
    cache_path = directory / RECONCILE_CACHE
    if not _open_with_mr(directory) and not cache_path.exists():
        return []
    with open(directory / f"{RECONCILE_CACHE}.lock", "a") as lock:
        if fcntl:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:  # another run is querying the forges: leave it to that one
                return []
        try:
            checked = json.loads(cache_path.read_text(encoding="utf-8"))
            checked = checked if isinstance(checked, dict) else {}
        except (OSError, ValueError):
            checked = {}
        open_ = _open_with_mr(directory)
        kept = {tid: t for tid, t in checked.items() if tid in open_}
        closed = []
        for tid, events in sorted(open_.items()):
            last = kept.get(tid)
            if isinstance(last, (int, float)) and 0 <= now - last < RECONCILE_EVERY_S:
                continue
            try:
                done = _reconcile_one(tid, events, run, now)
            except (ValueError, OSError) as exc:
                print(f"flow_telemetry: reconcile {tid}: {exc}", file=sys.stderr)
                done = None
            if done:
                closed.append(done)
                kept.pop(tid, None)
            else:
                kept[tid] = now
            _save_checked(cache_path, kept)  # per task: a run killed midway keeps what it learned
            checked = dict(kept)
        if kept != checked:  # drops the entries of tasks closed elsewhere
            _save_checked(cache_path, kept)
    return closed


def _open_with_mr(directory: Path) -> dict[str, list[dict]]:
    """The task files with an `mr_created` gate and no `outcome`."""
    tasks = {p.stem: read_events(p) for p in directory.glob("*.jsonl") if not p.name.startswith("session-")}
    return {tid: events for tid, events in tasks.items()
            if not any(e.get("type") == "outcome" for e in events)
            and any(e.get("type") == "gate" and e.get("gate") == "mr_created" for e in events)}


def _save_checked(path: Path, kept: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(kept, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _summary(got: dict) -> str:
    return f"attribution: total={sum(got['total'].values())} cost={_usd(tu.dollars(got['cost']))} ({got['note']})"


def _fields(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"--field needs key=value: {pair!r}")
        out[key] = int(value) if key in INT_FIELDS else value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow_telemetry.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    session_default = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    p = sub.add_parser("start")
    p.add_argument("--session", default=session_default)
    p.add_argument("--repo", default="")
    p.add_argument("--task-id", default="")
    p.add_argument("--parent-task", default="")
    p = sub.add_parser("current")
    p.add_argument("--session", default=session_default)
    p.add_argument("--cwd", default="")
    p = sub.add_parser("close")
    p.add_argument("--session", default=session_default)
    p.add_argument("--task", default="")
    p = sub.add_parser("event")
    p.add_argument("--type", required=True)
    p.add_argument("--session", default=session_default)
    p.add_argument("--cwd", default="")
    p.add_argument("--task", default="")
    p.add_argument("--field", action="append", default=[])
    p = sub.add_parser("attribute")
    p.add_argument("--task", default="")
    p.add_argument("--session", default=session_default)
    p = sub.add_parser("render")
    p.add_argument("--out", default="")
    sub.add_parser("reconcile")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "reconcile":
            closed = reconcile()
            for tid, result, url in closed:
                print(f"reconciled: {tid} {result} {url}")
            if closed:
                render()
            print(f"reconcile: {len(closed)} task(s) closed")
            return 0
        if args.cmd == "start":
            print(f"task: {start(args.session, args.repo, args.task_id, args.parent_task)}")
            return 0
        if args.cmd == "attribute":
            got = attribute(task_id=args.task, session=args.session)
            if args.task:
                record("attribution", args.session, "", got, task=args.task)
            print(_summary(got))
            return 0
        if args.cmd == "render":
            count = render(Path(args.out) if args.out else None)
            print(f"journal: {count} tasks -> {args.out or journal_path()}")
            return 0
        if args.cmd == "close":
            adopted = adopted_task(args.session)
            task = flow_state.session_task(args.session) or adopted
            if args.task and task != args.task:  # the session moved on: its current task is not ours to end
                print(f"task: not closed, the session holds {task or 'no task'}, not {args.task}")
                return 1
            if task:  # the session's last event marks where its share of the task ends
                try:
                    record("gate", args.session, "", {"gate": "handoff", "answer": "released"}, task=task)
                except (ValueError, OSError) as exc:  # clearing the ID must not depend on it
                    print(f"flow_telemetry: handoff event not written: {exc}", file=sys.stderr)
            closed = flow_state.clear_session_task(args.session)
            if adopted:
                adopted_file(args.session).unlink(missing_ok=True)
                closed = True
            print("task: closed" if closed else "task: no flow flag for this session")
            return 0 if closed else 1
        if args.cmd == "current":
            task = current_task(args.session, args.cwd)
            if task:
                print(task)
            return 0 if task else 1
        if args.task and not TASK_ID_RE.match(args.task):
            raise ValueError(f"not a task id: {args.task!r}")
        path = record(args.type, args.session, args.cwd, _fields(args.field), args.task)
    except (ValueError, OSError) as exc:
        print(f"flow_telemetry: {exc}", file=sys.stderr)
        return 2
    print(f"event: {args.type} -> {path}" if path else "event: skipped (no active task)")
    if path and args.type == "outcome":  # the outcome closes the task: no close path can skip its cost
        try:
            got = attribute(task_id=path.stem)
            record("attribution", args.session, "", got, task=path.stem)
            print(_summary(got))
        except Exception as exc:  # noqa: BLE001 - the outcome is written: a failed attribution must not stop the close
            print(f"flow_telemetry: outcome recorded, attribution failed ({exc}); "
                  f"re-run: flow_telemetry.py attribute --task {path.stem}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
