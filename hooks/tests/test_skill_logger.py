#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""pytest port of hooks/test_skill_logger.sh — same 5 cases plus one new case
for the plugin-enabled gate, which this port inlines (previously covered only
by hooks/test_plugin_enabled.sh, against a different hook)."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "skill_logger.py"


def run_hook(payload: dict, tmp_path: Path, settings: dict | None = None) -> tuple[str, bool]:
    """Runs the real hook subprocess with HOME=tmp_path. Returns (log content, log exists)."""
    if settings is not None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(json.dumps(settings))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SIMO_DEV_")}
    env["HOME"] = str(tmp_path)
    subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        env=env,
        check=True,
    )
    log = tmp_path / ".claude" / "state" / "dev-flow-skills.log"
    if log.exists():
        return log.read_text(), True
    return "", False


def test_happy_path(tmp_path, git_repo):
    want_branch = subprocess.run(
        ["git", "-C", str(git_repo), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    out, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
    )
    assert exists
    fields = out.rstrip("\n").split("\t")
    assert len(fields) == 5
    ts, session, repo, branch, skill = fields
    assert session == "sess-42"
    assert repo == git_repo.name
    assert branch == want_branch
    assert skill == "simo-dev:commit"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts)


def test_worktree_yields_main_repo_name(tmp_path, git_repo):
    wt = git_repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(git_repo), "worktree", "add", str(wt), "-b", "side"],
        check=True, capture_output=True,
    )
    out, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(wt), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
    )
    assert exists
    repo = out.rstrip("\n").split("\t")[2]
    assert repo == git_repo.name
    assert repo != "wt"


def test_no_skill_writes_nothing(tmp_path, git_repo):
    # cwd is a real repo so this isolates the empty-skill guard from the
    # non-repo-cwd guard below — a "/" cwd would pass for either reason.
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {}}, tmp_path
    )
    assert not exists


def test_non_repo_cwd_writes_nothing(tmp_path):
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": "/", "tool_input": {"skill": "simo-dev:commit"}}, tmp_path
    )
    assert not exists


def test_detached_head_logs_with_empty_branch(tmp_path):
    repo = tmp_path / "detached-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "--allow-empty", "-m", "init"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", "--detach", "HEAD"], check=True)
    out, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
    )
    assert exists
    fields = out.rstrip("\n").split("\t")
    assert fields[1] == "sess-42"
    assert fields[3] == ""
    assert fields[4] == "simo-dev:commit"


def test_plugin_disabled_writes_nothing(tmp_path, git_repo):
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
        settings={"enabledPlugins": {"simo-dev@simo-local": False}},
    )
    assert not exists


def test_settings_json_without_the_key_still_logs(tmp_path, git_repo):
    # A settings.json that exists and parses, but never mentions this plugin,
    # must fail OPEN (log), same as a missing settings.json — only a literal
    # `false` disables. This is the only case that actually distinguishes
    # `is not False` from a naive `== True` on the enabled check: both read
    # identically when the stored value is a literal False.
    _, exists = run_hook(
        {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}},
        tmp_path,
        settings={"enabledPlugins": {}},
    )
    assert exists


def test_log_write_failure_does_not_crash_the_hook(tmp_path, git_repo):
    # ~/.claude/state exists as a plain FILE instead of a directory, so
    # mkdir(parents=True, exist_ok=True) raises FileExistsError. The hook must
    # still exit 0 (fail open), never propagate an uncaught exception.
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "state").write_text("not a directory")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SIMO_DEV_")}
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(
            {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}}
        ),
        text=True,
        env=env,
    )
    assert result.returncode == 0


def test_invalid_utf8_settings_json_does_not_crash_the_hook(tmp_path, git_repo):
    # settings.json containing a byte sequence that is not valid UTF-8 must
    # not crash _plugin_enabled()'s read — treated as fail-open (still logs),
    # same as a missing or malformed settings.json. Written as raw bytes, not
    # through the `settings=` dict helper, so this doesn't depend on the
    # process's default locale/encoding the way an LC_ALL trick would.
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_bytes(b'{"enabledPlugins": {"x": "\xff\xfe"}}')
    env = {k: v for k, v in os.environ.items() if not k.startswith("SIMO_DEV_")}
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(
            {"session_id": "sess-42", "cwd": str(git_repo), "tool_input": {"skill": "simo-dev:commit"}}
        ),
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert (tmp_path / ".claude" / "state" / "dev-flow-skills.log").exists()


def _events(tmp_path):
    out = {}
    tel = tmp_path / ".claude" / "state" / "dev-flow"
    for p in sorted(tel.glob("*.jsonl")):
        out[p.name] = [json.loads(line) for line in p.read_text().splitlines()]
    return out


def test_outside_the_flow_only_simo_dev_skills_reach_the_session_file(tmp_path, git_repo):
    run_hook({"tool_input": {"skill": "simo-dev:guide"}, "session_id": "s1", "cwd": str(git_repo)}, tmp_path)
    run_hook({"tool_input": {"skill": "other:thing"}, "session_id": "s1", "cwd": str(git_repo)}, tmp_path)
    events = _events(tmp_path)
    assert list(events) == ["session-s1.jsonl"]
    assert [e["skill"] for e in events["session-s1.jsonl"]] == ["simo-dev:guide"]


def test_inside_a_task_a_non_git_cwd_still_gets_the_event(tmp_path):
    active = tmp_path / ".claude" / "state" / "dev-flow" / "active"
    active.mkdir(parents=True)
    (active / "s3").write_text("1\t20261109T1712-s3\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    run_hook({"tool_input": {"skill": "simo-dev:commit"}, "session_id": "s3", "cwd": str(plain)}, tmp_path)
    assert list(_events(tmp_path)) == ["20261109T1712-s3.jsonl"]


def test_inside_a_task_every_skill_reaches_the_task_file(tmp_path, git_repo):
    active = tmp_path / ".claude" / "state" / "dev-flow" / "active"
    active.mkdir(parents=True)
    (active / "s2").write_text("1\t20261109T1712-s2\n")
    run_hook({"tool_input": {"skill": "other:thing"}, "session_id": "s2", "cwd": str(git_repo)}, tmp_path)
    events = _events(tmp_path)
    assert list(events) == ["20261109T1712-s2.jsonl"]
    assert events["20261109T1712-s2.jsonl"][0]["type"] == "step_start"
    log, exists = run_hook({"tool_input": {"skill": "simo-dev:review-loop"}, "session_id": "s2",
                            "cwd": str(git_repo)}, tmp_path)
    assert exists and "simo-dev:review-loop" in log


def test_a_telemetry_failure_still_writes_the_tsv_line(tmp_path, git_repo):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SIMO_DEV_")}
    env.update({"HOME": str(tmp_path), "SIMO_DEV_TELEMETRY_DIR": str(blocker / "dev-flow")})
    r = subprocess.run([sys.executable, str(HOOK)], text=True, env=env, capture_output=True,
                       input=json.dumps({"tool_input": {"skill": "simo-dev:commit"}, "session_id": "s4",
                                         "cwd": str(git_repo)}))
    assert r.returncode == 0
    log = tmp_path / ".claude" / "state" / "dev-flow-skills.log"
    assert "simo-dev:commit" in log.read_text()


def test_a_work_step_with_no_task_adopts_one_and_keeps_the_flow_off(tmp_path, git_repo):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {
        "content": "follow-up work\nparent task: 20261111T0930-abcd1234\n"}}) + "\n")
    base = {"session_id": "s5", "cwd": str(git_repo), "transcript_path": str(transcript)}
    run_hook({**base, "tool_input": {"skill": "simo-dev:commit"}}, tmp_path)
    run_hook({**base, "tool_input": {"skill": "simo-dev:code-review-protocol"}}, tmp_path)
    events = _events(tmp_path)
    assert len(events) == 1
    name, rows = next(iter(events.items()))
    assert re.fullmatch(r"\d{8}T\d{4}-s5\.jsonl", name)
    assert [(e["type"], e.get("skill")) for e in rows] == [
        ("gate", "simo-dev:commit"), ("step_start", "simo-dev:commit"), ("task_link", None),
        ("step_start", "simo-dev:code-review-protocol")]
    assert rows[2]["parent"] == "20261111T0930-abcd1234"
    assert not (tmp_path / ".claude" / "state" / "dev-flow" / "active").exists()
