#!/usr/bin/env python3
"""Tests for lib/step_replay.py."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))
import step_replay as sr  # noqa: E402

FIXTURES = LIB / "tests" / "fixtures" / "replay"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "src"
    plugin = root / sr.PLUGIN_REL
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "simo-dev", "version": "0.0.1"}))
    (plugin / "skills" / "plan-write").mkdir(parents=True)
    (plugin / "skills" / "plan-write" / "SKILL.md").write_text("original skill\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n")
    (root / ".claude" / "settings.json").write_text('{"enabledPlugins": {}}')
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / "old.md").write_text("old plan\n")
    _git(tmp_path, "init", "-q", "-b", "master", str(root))
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
    (root / "future.txt").write_text("future\n")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "future")
    return root


def test_snapshot_has_one_commit_no_future_and_no_project_settings(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD~1")
    snap = sr.make_snapshot(repo, base, tmp_path / "run")
    assert (snap / "src" / "app.py").read_text() == "x = 1\n"
    assert not (snap / "future.txt").exists()
    assert not (snap / ".claude" / "settings.json").exists()
    assert _git(snap, "rev-list", "--count", "HEAD") == "1"
    assert _git(snap, "remote", "get-url", "origin") == str(tmp_path / "run" / "origin.git")
    assert _git(snap, "rev-parse", "origin/HEAD") == _git(snap, "rev-parse", "HEAD")
    assert _git(snap, "status", "--porcelain") == ""


def test_parse_variant_ref_dir_and_overrides():
    v = sr.parse_variant("B=ref:HEAD~1,override:skills/plan-write/SKILL.md=/tmp/x.md")
    assert (v["label"], v["kind"], v["value"]) == ("B", "ref", "HEAD~1")
    assert v["overrides"] == [["skills/plan-write/SKILL.md", "/tmp/x.md"]]
    assert sr.parse_variant("A=dir:/some/plugin")["kind"] == "dir"


@pytest.mark.parametrize("bad", ["A", "=ref:HEAD", "A=tag:x", "A=ref:", "A=ref:HEAD,override:../x=/y", "A=ref:HEAD,patch:x=y"])
def test_parse_variant_rejects(bad):
    with pytest.raises(sr.ReplayError):
        sr.parse_variant(bad)


def test_materialize_plugin_ref_with_override(repo, tmp_path):
    override = tmp_path / "new.md"
    override.write_text("changed skill\n")
    v = sr.parse_variant(f"B=ref:HEAD,override:skills/plan-write/SKILL.md={override}")
    info = sr.materialize_plugin(repo, v, tmp_path / "plugin")
    assert (tmp_path / "plugin" / "skills" / "plan-write" / "SKILL.md").read_text() == "changed skill\n"
    assert info["version"] == "0.0.1"
    assert info["commit"] == _git(repo, "rev-parse", "HEAD")
    plain = sr.materialize_plugin(repo, sr.parse_variant("A=ref:HEAD"), tmp_path / "plugin2")
    assert plain["tree"] != info["tree"]


def test_materialize_plugin_dir_skips_caches(repo, tmp_path):
    src = repo / sr.PLUGIN_REL
    (src / "lib" / "__pycache__").mkdir(parents=True)
    (src / "lib" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    info = sr.materialize_plugin(repo, sr.parse_variant(f"W=dir:{src}"), tmp_path / "plugin")
    assert info["version"] == "0.0.1" and info["commit"] == ""
    assert not (tmp_path / "plugin" / "lib" / "__pycache__").exists()


def test_materialize_plugin_override_into_missing_dir_refused(repo, tmp_path):
    (tmp_path / "a").write_text("x\n")
    v = sr.parse_variant(f"B=ref:HEAD,override:nope/x.md={tmp_path}/a")
    with pytest.raises(sr.ReplayError):
        sr.materialize_plugin(repo, v, tmp_path / "plugin")


def test_materialize_plugin_missing_sources_refused(repo, tmp_path):
    with pytest.raises(sr.ReplayError):
        sr.materialize_plugin(repo, sr.parse_variant(f"B=ref:HEAD,override:skills/plan-write/SKILL.md={tmp_path}/none"),
                              tmp_path / "p1")
    with pytest.raises(sr.ReplayError):
        sr.materialize_plugin(repo, sr.parse_variant(f"W=dir:{tmp_path}/nodir"), tmp_path / "p2")


def _snap(repo, tmp_path):
    return sr.make_snapshot(repo, _git(repo, "rev-parse", "HEAD~1"), tmp_path / "run")


def test_check_plan_good(repo, tmp_path):
    snap = _snap(repo, tmp_path)
    out = sr.check_plan((FIXTURES / "plan-good.md").read_text(), snap)
    assert out["pass"] is True and out["modify_missing"] == []
    assert (out["tasks"], out["files"], out["python_blocks"], out["python_errors"]) == (2, 4, 3, 0)
    assert out["pytest_refs"] == 3 and out["pytest_unresolved"] == []


def test_check_plan_bad(repo, tmp_path):
    snap = _snap(repo, tmp_path)
    out = sr.check_plan((FIXTURES / "plan-bad.md").read_text(), snap)
    assert out["pass"] is False
    assert out["modify_missing"] == ["src/missing.py"]
    assert out["create_existing"] == ["src/app.py"]
    assert out["python_errors"] == 1
    assert out["pytest_unresolved"] == ["tests/test_nowhere.py"]


def test_find_new_plan_prefers_the_plan_line(repo, tmp_path):
    snap = _snap(repo, tmp_path)
    (snap / "docs" / "plans" / "a.md").write_text("a\n")
    (snap / "docs" / "plans" / "b.md").write_text("b\n")
    assert sr.find_new_plan(snap, "done\nPLAN: docs/plans/b.md") == snap / "docs" / "plans" / "b.md"
    assert sr.find_new_plan(snap, "done") is None
    (snap / "docs" / "plans" / "a.md").unlink()
    assert sr.find_new_plan(snap, "no plan line") == snap / "docs" / "plans" / "b.md"


def _usage_line(mid, model, **usage):
    return json.dumps({"type": "assistant", "timestamp": "2026-11-11T10:00:00.000Z",
                       "message": {"id": mid, "model": model, "usage": usage}})


def test_run_cost_by_model_and_subagents(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    main = tmp_path / ".claude" / "projects" / "-tmp-x" / "sid-1.jsonl"
    subs = main.with_suffix("") / "subagents"
    subs.mkdir(parents=True)
    main.write_text("\n".join([
        _usage_line("m1", "claude-opus-5-5", input_tokens=1_000_000, output_tokens=0),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "head x"}},
            {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "doc.md"}}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "head: x: Operation not permitted"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "a doc quoting Operation not permitted"}]}}),
        json.dumps({"type": "system", "message": {"content": [
            {"type": "text", "text": "If a command fails with \"Operation not permitted\", the sandbox blocked it."}]}}),
        json.dumps({"type": "user", "message": {"content": "a prompt quoting Operation not permitted"}}),
    ]) + "\n")
    for i in range(2):
        (subs / f"agent-{i}.jsonl").write_text(_usage_line(f"s{i}", "claude-sonnet-5-5", output_tokens=1_000_000) + "\n")
        (subs / f"agent-{i}.meta.json").write_text(json.dumps({"agentType": "simo-dev:plan-reviewer"}))
    out = sr.run_cost("sid-1")
    assert out["by_model"] == {"claude-opus-5-5": 4.0, "claude-sonnet-5-5": 20.0}
    assert out["total"] == 24.0
    assert out["subagents"] == {"simo-dev:plan-reviewer": 2}
    assert out["blocks"] == 1
    assert sr.run_cost("missing") is None


@pytest.fixture
def replay_env(repo, tmp_path, monkeypatch):
    fake = tmp_path / "fake_claude.py"
    fake.write_text((FIXTURES / "fake_claude.py").read_text())
    fake.chmod(0o755)
    fixture = repo / "fixtures" / "plan-write-sample"
    fixture.mkdir(parents=True)
    for name in ("fixture.json", "brief.md"):
        (fixture / name).write_text((FIXTURES / "plan-write-sample" / name).read_text())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIMO_DEV_REPLAY_CLAUDE", str(fake))
    monkeypatch.setenv("SIMO_DEV_REPLAY_DIR", str(tmp_path / "manifests"))
    monkeypatch.setenv("SIMO_DEV_REPLAY_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "fake.log"))
    return fixture


def test_run_writes_manifest_and_isolates(repo, tmp_path, replay_env, monkeypatch):
    for name in ("CLAUDECODE", "CLAUDE_CODE_PLUGIN_DIRS", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT",
                 "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy")
    (tmp_path / "parent-tmp").mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "parent-tmp"))
    # Only entries present before and after the run: other sessions create and delete /tmp entries meanwhile.
    tmp, own = Path("/tmp").resolve(), sr.own_tmp()
    before = set(tmp.iterdir()) | (set(own.iterdir()) if own.is_dir() else set())
    rc = sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20"])
    assert rc == 0
    stable = before & (set(tmp.iterdir()) | (set(own.iterdir()) if own.is_dir() else set()))
    outside_own = [q for q in stable if q.parent == tmp and q != own]
    inside_own = [q for q in stable if q.parent == own]
    manifests = sorted((tmp_path / "manifests").glob("*.json"))
    assert len(manifests) == 1
    m = json.loads(manifests[0].read_text())
    assert m["status"] == "ok" and m["step"] == "plan-write" and m["fixture"] == "plan-write-sample"
    assert m["variant"]["version"] == "0.0.1" and m["variant"]["label"] == "A"
    assert m["rounds"] == 1 and m["cost"]["total"] > 0 and m["cli_cost_usd"] == 0.01
    assert m["outcome"]["pass"] is True and m["outcome"]["tasks"] == 1
    assert m["base"] == _git(repo, "rev-parse", "HEAD~1")
    assert (tmp_path / "manifests" / (m["run_id"] + ".plan.md")).exists()
    assert m["snapshot_claude_md"] is False
    assert "at most twice" in sr.STEPS["plan-write"].prompt("x", tmp_path)
    log = json.loads((tmp_path / "fake.log").read_text())
    argv = log["argv"]
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert "--strict-mcp-config" in argv and argv[argv.index("--max-budget-usd") + 1] == "8.0"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits" and m["permission_mode"] == "acceptEdits"
    settings = log["settings"]
    assert settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["network"]["allowedDomains"] == []
    assert str(repo.resolve()) in settings["sandbox"]["filesystem"]["denyRead"]
    deny_read = settings["sandbox"]["filesystem"]["denyRead"]
    assert not any(d.endswith("/.claude/projects") for d in deny_read)
    assert str(tmp) not in deny_read and str(sr.own_tmp()) not in deny_read
    assert str(sr.own_tmp()) in settings["sandbox"]["filesystem"]["allowWrite"]
    assert all(str(q) in deny_read for q in outside_own + inside_own)
    assert str((tmp_path / "parent-tmp").resolve()) in deny_read
    rules = settings["permissions"]["deny"]
    assert log["env"]["ANTHROPIC_BASE_URL"] == "http://proxy" and "ANTHROPIC_BASE_URL" in m["env_inherited"]
    assert len(m["env_sig"]) == 12
    assert f"Read(/{tmp})" not in rules and f"Read(/{tmp}/**)" not in rules
    assert not any(r.startswith(f"Read(/{sr.own_tmp()})") or r.startswith(f"Read(/{sr.own_tmp()}/**)") for r in rules)
    assert all(f"Read(/{q}/**)" in rules for q in outside_own + inside_own)
    assert not {f"Edit(/{q}{tail})" for q in outside_own for tail in ("", "/**")} & set(rules)
    assert f"Edit(/{own})" not in rules and f"Edit(/{own}/**)" not in rules
    assert all(f"Edit(/{q}/**)" in rules for q in inside_own)
    env = log["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "x"
    assert not {"CLAUDECODE", "CLAUDE_CODE_PLUGIN_DIRS", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT",
                "CLAUDE_AGENT_SDK_VERSION"} & set(env)
    assert env["TMPDIR"].endswith("/tmp") and m["run_id"] in env["TMPDIR"]
    assert "CLAUDE_CODE_PLUGIN_DIRS" in m["env_dropped"] and m["may_contain_answer"] is False
    assert f"Edit(/{repo.resolve()}/**)" in settings["permissions"]["deny"]
    assert log["strategy"] == {"plan_critique": "opus"}
    assert log["env"]["SIMO_DEV_TRACKER_CONFIG"].endswith("tracker.json")
    assert log["env"]["SIMO_DEV_REPO_CONFIG"].endswith("repo-config.json")
    assert "simo-dev:plan-write" in log["stdin"] and "{{REPO}}" not in log["stdin"]
    assert log["env"]["SIMO_DEV_MODEL_STRATEGY_CONFIG"].endswith("model-strategy.json")
    assert not list((tmp_path / "work").iterdir())


def test_run_error_still_writes_manifest(repo, tmp_path, replay_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_RC", "1")
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20"]) == 3  # the canary failed
    (manifest,) = [p for p in (tmp_path / "manifests").glob("*.json")]
    m = json.loads(manifest.read_text())
    assert m["status"] == "error" and m["rc"] == 1 and m["outcome"]["pass"] is False


def test_smoke_outcome():
    smoke = sr.STEPS["sandbox-smoke"]
    ok = "ECHO: `ok`\nDENIED: head: x: Operation not permitted\nFETCH: fetched\nNET: blocked\nTMP: tmpok"
    assert smoke.evaluate(None, ok)[1]["pass"] is True
    leak = "ECHO: ok\nDENIED: # Project\nFETCH: fetched\nNET: blocked\nTMP: tmpok"
    out = smoke.evaluate(None, leak)[1]
    assert out["pass"] is False and out["source_denied"] is False
    permission_only = "ECHO: ok\nDENIED: Permission to read was denied\nFETCH: fetched\nNET: blocked\nTMP: tmpok"
    assert smoke.evaluate(None, permission_only)[1]["source_denied"] is False
    net = smoke.evaluate(None, "ECHO: ok\nDENIED: Operation not permitted\nFETCH: fetched\nNET: leaked\nTMP: tmpok")[1]
    assert net["net_blocked"] is False and net["pass"] is False
    proxy = "ECHO: ok\nDENIED: Operation not permitted\nFETCH: fetched\nNET: curl: (56) CONNECT tunnel failed, response 403\nTMP: tmpok"
    assert smoke.evaluate(None, proxy)[1]["pass"] is True


def test_run_timeout_still_writes_manifest(repo, tmp_path, replay_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "5")
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20",
                    "--timeout", "1"]) == 3
    (manifest,) = [p for p in (tmp_path / "manifests").glob("*.json")]
    assert json.loads(manifest.read_text())["status"] == "timeout"


def test_deny_list_covers_transcripts_and_other_runs(repo, tmp_path, replay_env):
    (tmp_path / "work" / "old-run").mkdir(parents=True)
    current = tmp_path / "work" / "this-run"
    current.mkdir()
    home = tmp_path / "home" / ".claude"
    for rel in ("state", "plugins", "backups", "projects/-other-session"):
        (home / rel).mkdir(parents=True, exist_ok=True)
    (home / "history.jsonl").write_text("")
    deny = sr._deny_list(repo, current)
    assert all(str(home / rel) in deny for rel in ("state", "plugins", "backups", "history.jsonl",
                                                   "projects/-other-session"))
    # projects/ itself stays readable: the run's own project dir is created there after it starts.
    assert str(home / "projects") not in deny
    assert str((tmp_path / "work" / "old-run").resolve()) in deny
    assert str(current.resolve()) not in deny
    assert str((tmp_path / "manifests").resolve()) in deny


def test_run_refuses_over_budget_without_yes(repo, tmp_path, replay_env, capsys):
    rc = sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "2", "--budget", "5"])
    assert rc == 2
    assert "estimate" in capsys.readouterr().err
    assert not (tmp_path / "manifests").exists()


def _m(label, cost, ok=True, fixture="f", rounds=1):
    return {"variant": {"label": label}, "cost": {"total": cost}, "outcome": {"pass": ok},
            "status": "ok", "rounds": rounds, "wall_s": 10, "fixture": fixture, "step": "plan-write"}


def test_verdict_rules():
    a = sr.summarize([_m("A", 5.0), _m("A", 6.0)])
    assert (a["mean"], a["spread"], a["pass_rate"]) == (5.5, 1.0, 1.0)
    assert sr.verdict(a, sr.summarize([_m("B", 3.0), _m("B", 4.9)])) == "B cheaper beyond noise"
    assert sr.verdict(a, sr.summarize([_m("B", 4.0), _m("B", 5.5)])) == "no difference beyond noise"
    assert sr.verdict(a, sr.summarize([_m("B", 6.1), _m("B", 7.0)])) == "B costlier beyond noise"
    assert sr.verdict(a, sr.summarize([_m("B", 1.0), _m("B", 1.0, ok=False)])) == "B worse outcome"
    errored = {**_m("B", 0.0), "status": "error", "cli_cost_usd": 8.0}
    b = sr.summarize([_m("B", 1.0), errored])
    assert (b["err"], b["max"]) == (1, 8.0)
    assert sr.verdict(a, b) == "B worse outcome"
    assert sr.verdict(a, sr.summarize([_m("B", 1.0)])).startswith("insufficient")


def test_compare_prints_table_and_verdict(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIMO_DEV_REPLAY_DIR", str(tmp_path))
    for i, (label, cost) in enumerate([("A", 5.0), ("A", 6.0), ("A2", 5.2), ("A2", 5.8)]):
        rec = {"schema": 1, "run_id": f"r{i}", "batch": "b1", **_m(label, cost)}
        (tmp_path / f"r{i}.json").write_text(json.dumps(rec))
    assert sr.main(["compare"]) == 0
    out = capsys.readouterr().out
    assert "plan-write / f" in out and "A2" in out and "no difference beyond noise" in out
    assert "warning" not in out
    rec = {"schema": 1, "run_id": "r9", "batch": "b2", **_m("A", 5.5)}
    rec["variant"]["tree"] = "other"
    (tmp_path / "r9.json").write_text(json.dumps(rec))
    assert sr.main(["compare"]) == 0
    assert "warning: A mixes 2 plugin trees" in capsys.readouterr().out


def test_estimate_uses_ok_manifest_mean(repo, tmp_path, replay_env, capsys):
    (tmp_path / "manifests").mkdir()
    for i, cost in enumerate([2.0, 4.0]):
        rec = {"schema": 1, "fixture": "plan-write-sample", "step": "plan-write", "status": "ok", "cost": {"total": cost},
               "model": "opus", "critique_model": "opus", "effort": "medium"}
        (tmp_path / "manifests" / f"r{i}.json").write_text(json.dumps(rec))
    assert sr.main(["estimate", "--fixture", str(replay_env), "-n", "2", "--variants", "2"]) == 0
    assert "$3.0 per run, 4 runs = $12.0" in capsys.readouterr().out


def test_estimate_only_counts_runs_with_the_same_models(repo, tmp_path, replay_env, capsys):
    (tmp_path / "manifests").mkdir()
    for i, (model, critic, cost) in enumerate([("opus", "opus", 8.0), ("sonnet", "sonnet", 2.0), ("sonnet", "opus", 5.0)]):
        rec = {"schema": 1, "fixture": "plan-write-sample", "step": "plan-write", "status": "ok", "cost": {"total": cost},
               "model": model, "critique_model": critic, "effort": "medium"}
        (tmp_path / "manifests" / f"r{i}.json").write_text(json.dumps(rec))
    assert sr.main(["estimate", "--fixture", str(replay_env), "--model", "sonnet", "--critique-model", "sonnet"]) == 0
    assert "$2.0 per run" in capsys.readouterr().out
    # A run with no transcript cost falls back to the CLI's own figure, and another effort does not count.
    rec = {"schema": 1, "fixture": "plan-write-sample", "step": "plan-write", "status": "ok", "cost": {"total": 0.0},
           "cli_cost_usd": 4.0, "model": "sonnet", "critique_model": "sonnet", "effort": "medium"}
    (tmp_path / "manifests" / "r9.json").write_text(json.dumps(rec))
    (tmp_path / "manifests" / "r10.json").write_text(json.dumps({**rec, "cli_cost_usd": 50.0, "effort": "high"}))
    assert sr.main(["estimate", "--fixture", str(replay_env), "--model", "sonnet", "--critique-model", "sonnet"]) == 0
    assert "$3.0 per run" in capsys.readouterr().out


def test_fixture_extracts_brief_and_reference(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    main = tmp_path / ".claude" / "projects" / "-x" / "sid-9.jsonl"
    main.parent.mkdir(parents=True)
    use = {"type": "tool_use", "name": "Skill", "input": {"skill": "simo-dev:plan-write", "args": f"Plan in {repo}/docs/plans/x.md"}}
    main.write_text(json.dumps({"type": "assistant", "cwd": str(repo), "message": {"content": [use]}}) + "\n")
    out = tmp_path / "fx"
    rc = sr.main(["fixture", "--session", "sid-9", "--base", "HEAD~1", "--out", str(out), "--repo", str(repo),
                  "--reference", "HEAD~1:docs/plans/old.md"])
    assert rc == 0
    assert (out / "brief.md").read_text() == "Plan in {{REPO}}/docs/plans/x.md\n"
    fx = json.loads((out / "fixture.json").read_text())
    assert fx["base"] == _git(repo, "rev-parse", "HEAD~1") and fx["step"] == "plan-write" and fx["name"] == "fx"
    assert fx["reference_plan"]["lines"] == 1
    assert sr.main(["fixture", "--session", "nope", "--base", "HEAD", "--out", str(tmp_path / "z"), "--repo", str(repo)]) == 2


def test_fixture_source_root_when_cwd_is_a_subdir(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    main = tmp_path / ".claude" / "projects" / "-x" / "sid-8.jsonl"
    main.parent.mkdir(parents=True)
    use = {"type": "tool_use", "name": "Skill",
           "input": {"skill": "simo-dev:plan-write",
                     "args": f"Plan at {repo}/docs/plans/x.md from {repo}/sub, not {repo}-other/y, in {repo}."}}
    main.write_text(json.dumps({"type": "assistant", "cwd": f"{repo}/sub", "message": {"content": [use]}}) + "\n")
    out = tmp_path / "fy"
    assert sr.main(["fixture", "--session", "sid-8", "--base", "HEAD~1", "--out", str(out), "--repo", str(repo),
                    "--source-root", str(repo)]) == 0
    assert (out / "brief.md").read_text() == (f"Plan at {{{{REPO}}}}/docs/plans/x.md from {{{{REPO}}}}/sub, "
                                              f"not {repo}-other/y, in {{{{REPO}}}}.\n")


def test_variant_newer_than_base_is_refused_unless_allowed(repo, tmp_path, replay_env):
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD", "-n", "1", "--budget", "20"]) == 2
    assert not (tmp_path / "manifests").exists()
    fx = {"base": "HEAD~1"}
    assert sr.check_answer_leak(repo, fx, sr.parse_variant("A=ref:HEAD~1"), False) is False
    assert sr.check_answer_leak(repo, fx, sr.parse_variant(f"W=dir:{repo}"), True) is True
    with pytest.raises(sr.ReplayError):
        sr.check_answer_leak(repo, fx, sr.parse_variant(f"W=dir:{repo}"), False)
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD", "-n", "1", "--budget", "20",
                    "--allow-newer-plugin"]) == 0
    (manifest,) = [p for p in (tmp_path / "manifests").glob("*.json")]
    assert json.loads(manifest.read_text())["may_contain_answer"] is True


def test_claude_config_dir_refused(repo, tmp_path, replay_env, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20"]) == 2


def test_interrupted_run_still_writes_manifest(repo, tmp_path, replay_env, monkeypatch):
    def boom(_sid):
        raise KeyboardInterrupt
    monkeypatch.setattr(sr, "run_cost", boom)
    with pytest.raises(KeyboardInterrupt):
        sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20"])
    (manifest,) = [p for p in (tmp_path / "manifests").glob("*.json")]
    m = json.loads(manifest.read_text())
    assert m["status"] == "interrupted" and m["cli_cost_usd"] == 0.01 and m["session_id"]
    assert not list((tmp_path / "work").iterdir())


def test_run_stops_before_passing_the_budget(repo, tmp_path, replay_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_INPUT", "250000")
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "3",
                    "--max-budget-usd", "1", "--budget", "2.5", "--yes"]) == 0
    assert len(list((tmp_path / "manifests").glob("*.json"))) == 1


def test_run_caps_each_run_at_the_remaining_budget(repo, tmp_path, replay_env):
    # A first ok run makes the estimate (its cost) lower than --max-budget-usd, so a run fits but its cap must shrink.
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20"]) == 0
    first = json.loads(next((tmp_path / "manifests").glob("*.json")).read_text())
    assert first["max_budget_usd"] == 8.0 and first["capped_by_budget"] is False
    # A cap already in cents passes through unchanged and is not reported as lowered.
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "C=ref:HEAD~1", "-n", "1", "--budget", "20",
                    "--max-budget-usd", "1.15"]) == 0
    c = next(json.loads(p.read_text()) for p in (tmp_path / "manifests").glob("*.json") if "-C-" in p.name)
    assert c["max_budget_usd"] == 1.15 and c["capped_by_budget"] is False
    # A sub-cent cap is rounded down for the CLI, but the budget did not lower it.
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "D=ref:HEAD~1", "-n", "1", "--budget", "20",
                    "--max-budget-usd", "1.155"]) == 0
    d = next(json.loads(p.read_text()) for p in (tmp_path / "manifests").glob("*.json") if "-D-" in p.name)
    assert d["max_budget_usd"] == 1.15 and d["capped_by_budget"] is False
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "B=ref:HEAD~1", "-n", "2", "--budget", "0.5"]) == 0
    b = sorted((json.loads(p.read_text()) for p in (tmp_path / "manifests").glob("*.json") if "-B-" in p.name),
               key=lambda m: m["run_id"])
    assert len(b) == 2 and b[0]["max_budget_usd"] == 0.5 and b[0]["capped_by_budget"] is True
    left = 0.5 - b[0]["cost"]["total"]
    assert b[0]["cost"]["total"] > 0.01 and b[1]["max_budget_usd"] < 0.5
    assert 0 <= left - b[1]["max_budget_usd"] < 0.01 and b[1]["capped_by_budget"] is True
    argv = json.loads((tmp_path / "fake.log").read_text())["argv"]
    assert float(argv[argv.index("--max-budget-usd") + 1]) == b[1]["max_budget_usd"]


def test_find_new_plan_staged_and_absolute_plan_line(repo, tmp_path):
    snap = _snap(repo, tmp_path)
    (snap / "docs" / "plans" / "a.md").write_text("a\n")
    (snap / "docs" / "plans" / "b.md").write_text("b\n")
    _git(snap, "add", "docs/plans/b.md")
    (snap / "docs" / "plans" / "b.md").write_text("b2\n")
    assert sr.find_new_plan(snap, f"PLAN: {snap.resolve()}/docs/plans/b.md") == snap / "docs" / "plans" / "b.md"


def test_check_plan_absolute_file_paths(repo, tmp_path):
    snap = _snap(repo, tmp_path)
    text = f"### Task 1: x\n\n**Files:**\n- Modify: `{snap.resolve()}/src/app.py`\n- Create: `$W/src/new.py`\n"
    out = sr.check_plan(text, snap)
    assert out["modify_missing"] == [] and out["create_existing"] == [] and out["pass"] is True


def test_verdict_better_outcome_and_mixed_fields(tmp_path, monkeypatch, capsys):
    a = sr.summarize([_m("A", 5.0), _m("A", 6.0, ok=False)])
    assert sr.verdict(a, sr.summarize([_m("B", 5.2), _m("B", 5.8)])) == "no cost difference beyond noise, B better outcome"
    monkeypatch.setenv("SIMO_DEV_REPLAY_DIR", str(tmp_path))
    for i, model in enumerate(["opus", "sonnet"]):
        rec = {"schema": 1, "run_id": f"r{i}", "batch": "b", "model": model, **_m("A", 5.0)}
        (tmp_path / f"r{i}.json").write_text(json.dumps(rec))
    assert sr.main(["compare"]) == 0
    assert "warning: A mixes 2 values of model" in capsys.readouterr().out


def test_sandbox_settings_no_edit_rule_for_tmp_entries_sharing_a_name_prefix_with_own_temp_dir(tmp_path):
    tmp, own = Path("/tmp").resolve(), sr.own_tmp()
    siblings = [tmp / own.name[:i] for i in (len("claude"), len(own.name) - 1)] + [tmp / (own.name + "0")]
    inside = own / "entry"
    rules = sr.sandbox_settings(tmp_path, [str(p) for p in siblings] + [str(inside)])["permissions"]["deny"]
    for p in siblings:
        assert f"Read(/{p}/**)" in rules
        assert f"Edit(/{p})" not in rules and f"Edit(/{p}/**)" not in rules
    assert f"Edit(/{inside})" in rules and f"Edit(/{inside}/**)" in rules


def test_deny_list_skips_a_parent_tmpdir_that_is_the_own_temp_dir(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(sr.own_tmp() / "inner"))
    deny = sr._deny_list(repo, tmp_path / "run")
    assert str(sr.own_tmp()) not in deny and str(sr.own_tmp() / "inner") not in deny
    monkeypatch.setenv("TMPDIR", str(sr.own_tmp()))
    assert str(sr.own_tmp()) not in sr._deny_list(repo, tmp_path / "run")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    assert str(tmp_path.resolve()) in sr._deny_list(repo, tmp_path / "run")


def test_env_signature_tracks_behavior_vars_and_hides_secrets():
    names, sig = sr.env_signature({"ANTHROPIC_BASE_URL": "a", "MCP_TIMEOUT": "1", "PATH": "/bin",
                                   "CLAUDE_CODE_OAUTH_TOKEN": "t1"})
    assert names == ["ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "MCP_TIMEOUT"]
    assert sr.env_signature({"ANTHROPIC_BASE_URL": "a", "MCP_TIMEOUT": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t2"})[1] == sig
    assert sr.env_signature({"ANTHROPIC_BASE_URL": "b", "MCP_TIMEOUT": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t1"})[1] != sig


def test_compare_leaves_out_interrupted_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIMO_DEV_REPLAY_DIR", str(tmp_path))
    rows = [("A", "ok", 1.0), ("A", "ok", 1.2), ("B", "ok", 1.1), ("B", "ok", 1.15), ("B", "interrupted", 0.0)]
    for i, (label, status, cost) in enumerate(rows):
        rec = {"schema": 1, "run_id": f"r{i}", "step": "plan-write", "fixture": "f", "batch": "b", "status": status,
               "variant": {"label": label}, "cost": {"total": cost}, "outcome": {"pass": True}}
        (tmp_path / f"r{i}.json").write_text(json.dumps(rec))
    assert sr.main(["compare", "--fixture", "f"]) == 0
    out = capsys.readouterr()
    assert "1 interrupted run(s) left out" in out.err
    assert "B vs A: no difference beyond noise" in out.out


def test_check_plan_never_looks_outside_the_snapshot(repo, tmp_path):
    snap = _snap(repo, tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("x\n")
    modify = sr.check_plan(f"### Task 1: x\n\n**Files:**\n- Modify: `{outside}`\n", snap)
    assert modify["modify_missing"] == [str(outside)]
    create = sr.check_plan(f"### Task 1: x\n\n**Files:**\n- Create: `{outside}`\n", snap)
    assert create["create_existing"] == []


def test_run_timeout_kills_the_process_group(repo, tmp_path, replay_env, monkeypatch):
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CLAUDE_CHILD", str(pid_file))
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    t0 = time.monotonic()
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20",
                    "--timeout", "2"]) == 3
    # Killing only claude would leave its child holding the output pipe until its own sleep ends (60s).
    assert time.monotonic() - t0 < 30
    m = json.loads(next((tmp_path / "manifests").glob("*.json")).read_text())
    assert m["status"] == "timeout"
    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("the run's child process outlived the timeout")


def test_env_signature_hashes_token_budgets(monkeypatch):
    base = {"MAX_THINKING_TOKENS": "1000", "ANTHROPIC_API_KEY": "k1"}
    names, sig = sr.env_signature(base)
    assert names == ["ANTHROPIC_API_KEY", "MAX_THINKING_TOKENS"]
    assert sr.env_signature({**base, "MAX_THINKING_TOKENS": "8000"})[1] != sig
    assert sr.env_signature({**base, "ANTHROPIC_API_KEY": "k2"})[1] == sig


def test_deny_list_keeps_the_projects_dir_when_home_claude_links_into_the_repo(repo, tmp_path, replay_env, monkeypatch):
    claude = repo / ".claude"
    (claude / "projects" / "-other-session").mkdir(parents=True)
    (claude / "state").mkdir()
    home = tmp_path / "linked-home"
    home.mkdir()
    (home / ".claude").symlink_to(claude)
    monkeypatch.setenv("HOME", str(home))
    deny = sr._deny_list(repo, tmp_path / "run")
    real = repo.resolve()
    assert str(real) not in deny and str(real / ".claude") not in deny and str(real / ".claude" / "projects") not in deny
    assert str(real / "src") in deny and str(real / ".claude" / "state") in deny
    assert str(real / ".claude" / "projects" / "-other-session") in deny


def test_ctrl_c_kills_the_run_process_group(repo, tmp_path, replay_env, monkeypatch):
    started = []

    class Interrupted(subprocess.Popen):
        def communicate(self, input=None, timeout=None):
            if input is not None:
                started.append(self.pid)
                raise KeyboardInterrupt
            return super().communicate(input, timeout)

    monkeypatch.setattr(sr.subprocess, "Popen", Interrupted)
    with pytest.raises(KeyboardInterrupt):
        sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20"])
    (pid,) = started
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        os.kill(pid, 9)
        pytest.fail("claude outlived the Ctrl-C")


# --- plan-execute --------------------------------------------------------------------------------

@pytest.fixture
def exec_env(repo, tmp_path, replay_env):
    """A plan-execute fixture with acceptance tests, and a plan saved by an earlier plan-write run."""
    fixture = repo / "fixtures" / "exec-sample"
    (fixture / "acceptance").mkdir(parents=True)
    (fixture / "fixture.json").write_text(json.dumps({
        "schema": 1, "name": "exec-sample", "step": "plan-execute", "base": "HEAD~1", "source_session": "synthetic",
        "suite": ["tests"], "estimate_usd": 0.5, "acceptance_stretch": ["test_stretch"]}))
    (fixture / "acceptance" / "test_contract.py").write_text(
        "import os\nfrom pathlib import Path\n\n"
        "def test_app_bumped():\n    assert (Path(os.environ['REPLAY_REPO']) / 'src/app.py').read_text() == 'x = 2\\n'\n\n"
        "def test_stretch():\n    assert False\n")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    src_id = "20260101T000000-sample-L-1"
    (manifests / f"{src_id}.json").write_text(json.dumps({
        "schema": 1, "run_id": src_id, "plan_path": "docs/plans/2026-01-01-lean.md", "cost": {"total": 1.5}}))
    plan = manifests / f"{src_id}.plan.md"
    plan.write_text(f"# Plan\n\n### Task 1: bump\n\n- Modify: `/old/work/{src_id}/repo/src/app.py`\n"
                    "- Test: `tests/test_new.py`\n")
    return fixture, plan


def _exec_manifest(tmp_path):
    (path,) = [p for p in (tmp_path / "manifests").glob("*.json") if "-B-" in p.name]
    return json.loads(path.read_text())


def test_permission_mode_override(repo, tmp_path, exec_env, monkeypatch):
    fixture, plan = exec_env
    rc = sr.main(["run", "--fixture", str(fixture), "--variant", f"B=ref:HEAD~1,plan:{plan}", "-n", "1",
                  "--budget", "5", "--max-budget-usd", "2", "--permission-mode", "bypassPermissions"])
    assert rc == 0
    argv = json.loads((tmp_path / "fake.log").read_text())["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert _exec_manifest(tmp_path)["permission_mode"] == "bypassPermissions"

def test_parse_variant_plan_part():
    v = sr.parse_variant("B=ref:abc,override:skills/x/SKILL.md=/f,plan:/p/run.plan.md")
    assert v["plan"] == "/p/run.plan.md" and v["overrides"] == [["skills/x/SKILL.md", "/f"]]
    assert sr.parse_variant("A=ref:abc")["plan"] == ""
    with pytest.raises(sr.ReplayError):
        sr.parse_variant("A=ref:abc,plan:/a,plan:/b")


def test_plan_execute_run_grades_with_hidden_acceptance(repo, tmp_path, exec_env, monkeypatch):
    fixture, plan = exec_env
    rc = sr.main(["run", "--fixture", str(fixture), "--variant", f"B=ref:HEAD~1,plan:{plan}", "-n", "1",
                  "--budget", "5", "--max-budget-usd", "2"])
    assert rc == 0
    m = _exec_manifest(tmp_path)
    log = json.loads((tmp_path / "fake.log").read_text())
    assert "simo-dev:plan-execute" in log["stdin"] and "docs/plans/2026-01-01-lean.md" in log["stdin"]
    assert log["strategy"] == {"plan_critique": "opus", "plan_execute": "opus"}
    argv = log["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "auto" and m["permission_mode"] == "auto"
    assert m["input_run"] == "20260101T000000-sample-L-1" and m["input_cost"] == 1.5 and m["execute_model"] == "opus"
    o = m["outcome"]
    assert o["done"] and not o["blocked"] and o["pass"] is True, o
    assert o["diff_files"] == 2 and o["own_tests"]["passed"] == 1 and o["own_tests"]["files"] == ["tests/test_new.py"]
    assert o["suite"]["tests"] == 1 and o["suite"]["regressions"] == []
    assert o["acceptance"] == {"passed": 1, "total": 1, "stretch_passed": 0, "stretch_total": 1,
                               "failed": ["test_stretch"], "error": ""}
    executed = (tmp_path / "manifests" / f"{m['run_id']}.plan.md").read_text()
    assert "/old/work/" not in executed and "/repo/src/app.py" in executed
    out_dir = tmp_path / "manifests"
    assert "x = 2" in (out_dir / f"{m['run_id']}.diff.txt").read_text()
    assert "== acceptance" in (out_dir / f"{m['run_id']}.tests.txt").read_text()
    assert list(out_dir.glob("baseline-exec-sample-*.json"))


def test_plan_execute_acceptance_is_hidden_during_the_run(repo, tmp_path, exec_env):
    fixture, plan = exec_env
    sr.main(["run", "--fixture", str(fixture), "--variant", f"B=ref:HEAD~1,plan:{plan}", "-n", "1", "--budget", "5"])
    log = json.loads((tmp_path / "fake.log").read_text())
    assert log["acceptance_visible"] is False
    assert str(repo.resolve()) in log["settings"]["sandbox"]["filesystem"]["denyRead"]
    assert "test_app_bumped" not in log["stdin"]


def test_plan_execute_failures(repo, tmp_path, exec_env, monkeypatch):
    fixture, plan = exec_env
    monkeypatch.setenv("FAKE_CLAUDE_APP", "x = 3\n")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", "stuck\nBLOCKED: missing dependency")
    sr.main(["run", "--fixture", str(fixture), "--variant", f"B=ref:HEAD~1,plan:{plan}", "-n", "1", "--budget", "5"])
    o = _exec_manifest(tmp_path)["outcome"]
    assert o["blocked"] and o["reason"] == "missing dependency" and not o["done"]
    assert o["acceptance"]["passed"] == 0 and o["acceptance"]["failed"] == ["test_app_bumped", "test_stretch"]
    assert o["pass"] is False


def test_plan_execute_suite_regression_fails_the_run(repo, tmp_path, exec_env, monkeypatch):
    fixture, plan = exec_env
    monkeypatch.setenv("FAKE_CLAUDE_BREAK_SUITE", "1")
    sr.main(["run", "--fixture", str(fixture), "--variant", f"B=ref:HEAD~1,plan:{plan}", "-n", "1", "--budget", "5"])
    o = _exec_manifest(tmp_path)["outcome"]
    assert o["suite"]["baseline_failing"] == 0 and len(o["suite"]["regressions"]) == 1
    assert o["suite"]["regressions"][0].endswith("::test_x") and o["acceptance"]["passed"] == 1
    assert o["pass"] is False


def test_plan_execute_requires_plan_and_plan_write_refuses_it(repo, tmp_path, exec_env, replay_env, capsys):
    fixture, plan = exec_env
    assert sr.main(["run", "--fixture", str(fixture), "--variant", "B=ref:HEAD~1", "-n", "1", "--budget", "5"]) == 2
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", f"A=ref:HEAD~1,plan:{plan}",
                    "-n", "1", "--budget", "20"]) == 2
    assert "plan:<file> part is required" in capsys.readouterr().err


def test_compare_adds_input_cost_and_acceptance(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIMO_DEV_REPLAY_DIR", str(tmp_path))
    for i, (label, own, inp, acc) in enumerate([("T", 3.0, 5.0, 4), ("T", 3.2, 5.4, 4), ("L", 5.0, 3.2, 3), ("L", 6.0, 3.4, 2)]):
        rec = {"schema": 1, "run_id": f"r{i}", "batch": "b1", **_m(label, own), "step": "plan-execute",
               "input_cost": inp}
        rec["outcome"]["acceptance"] = {"passed": acc}
        (tmp_path / f"r{i}.json").write_text(json.dumps(rec))
    assert sr.main(["compare"]) == 0
    out = capsys.readouterr().out
    assert "input plan's run + this run" in out and "run $" in out and "accept" in out
    t = [line for line in out.splitlines() if line.strip().startswith("T ")][0].split()
    assert t[3] == "8.30" and t[-2] == "3.10" and t[-1] == "4.0"
    assert "L vs T: no difference beyond noise" in out


def test_calibrate_runs_acceptance_on_a_commit(repo, tmp_path, exec_env, capsys):
    fixture, _ = exec_env
    assert sr.main(["calibrate", "--fixture", str(fixture), "--ref", "HEAD~1"]) == 1
    out = capsys.readouterr().out
    assert "failed   .replay-acceptance.test_contract::test_app_bumped" in out
    assert "mandatory 0/1, stretch 0/1" in out
    assert not [p for p in (tmp_path / "work").iterdir() if p.name.startswith("calibrate-")]


# --- review-loop and mr-followup ------------------------------------------------------------------

SEEDED = "x = 1\n\ndef add(a, b):\n    return a - b\n"
CLEAN = "x = 1\n\ndef add(a, b):\n    return a + b\n"
OWN_TEST = "import sys\nsys.path.insert(0, 'src')\nimport app\n\ndef test_add_zero():\n    assert app.add(2, 0) == 2\n"


def _patch(tmp_path, name, app_text, test_text=OWN_TEST):
    work = tmp_path / f"patch-{name}-{len(list(tmp_path.glob('patch-*')))}"
    _git(tmp_path, "init", "-q", str(work))
    (work / "src").mkdir()
    (work / "src" / "app.py").write_text("x = 1\n")
    _git(work, "add", "-A")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
    (work / "src" / "app.py").write_text(app_text)
    (work / "tests").mkdir(exist_ok=True)
    (work / "tests" / "test_add.py").write_text(test_text)
    _git(work, "add", "-A")
    return _git(work, "diff", "--cached") + "\n"


def _change_fixture(repo, tmp_path, name, step, **extra):
    fixture = repo / "fixtures" / name
    (fixture / "acceptance").mkdir(parents=True)
    (fixture / "fixture.json").write_text(json.dumps({
        "schema": 1, "name": name, "step": step, "base": "HEAD~1", "source_session": "synthetic",
        "estimate_usd": 0.5, "branch": "feat/add", "commit_message": "feat: add", **extra}))
    (fixture / "changes.patch").write_text(_patch(tmp_path, "changes", SEEDED,
                                                  OWN_TEST + ("def test_add():\n    assert app.add(1, 2) == 3\n"
                                                              if step == "mr-followup" else "")))
    (fixture / "reference.patch").write_text(_patch(tmp_path, "reference", CLEAN))
    (fixture / "acceptance" / "test_contract.py").write_text(
        "import os\nfrom pathlib import Path\n\n"
        "def test_add_fixed():\n    assert 'a + b' in (Path(os.environ['REPLAY_REPO']) / 'src/app.py').read_text()\n\n"
        "def test_x_kept():\n    assert (Path(os.environ['REPLAY_REPO']) / 'src/app.py').read_text().startswith('x = 1')\n")
    return fixture


@pytest.fixture
def review_env(repo, tmp_path, replay_env):
    return _change_fixture(repo, tmp_path, "review-sample", "review-loop", tolerance=1, items=[
        {"id": "B1", "severity": "high", "file": "src/app.py", "lines": [[4, 4]], "test": "test_add_fixed"},
        {"id": "B2", "severity": "medium", "file": "src/app.py", "lines": [[1, 1]], "test": "test_x_kept"},
        {"id": "B3", "severity": "low", "file": "src/app.py", "lines": [[2, 2]], "test": ""}])


@pytest.fixture
def followup_env(repo, tmp_path, replay_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_GH_LOG", str(tmp_path / "gh.log"))
    # The deny list denies the parent's $TMPDIR, which holds tmp_path: the fixture CI check would see no run dir.
    (tmp_path / "parent-tmp").mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "parent-tmp"))
    return _change_fixture(repo, tmp_path, "followup-sample", "mr-followup", items=[
        {"id": "C1", "kind": "ci", "test": "test_add_fixed"},
        {"id": "C2", "kind": "decline", "test": "test_x_kept", "fails_on_input": False}], forge={
        "pr": {"number": 7, "title": "feat: add", "slug": "replay/sample"},
        "threads": [{"path": "src/app.py", "line": 4, "author": "alice", "body": "this subtracts"}],
        "reviews": [{"author": "alice", "state": "CHANGES_REQUESTED", "body": "see inline"}],
        "ci": {"name": "test", "command": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                           "tests/test_add.py"]}})


def _manifest(tmp_path, label="A"):
    (path,) = [p for p in (tmp_path / "manifests").glob("*.json") if f"-{label}-" in p.name]
    return json.loads(path.read_text())


def test_parse_findings_and_match_items(tmp_path):
    texts = ["Findings:\n- [High] `lib/x.py:12` — bad\n**[medium] lib/x.py:40-42 – meh**\n[low] other.py:1 - nit\n"
             "[high] lib/x.py:12 — bad\nverdict: not-mergeable — x", "no findings here"]
    found = sr.parse_findings(texts)
    assert [(f["severity"], f["file"], f["line"]) for f in found] == [
        ("high", "lib/x.py", 12), ("medium", "lib/x.py", 40), ("low", "other.py", 1)]
    items = [{"id": "A", "file": "pkg/lib/x.py", "lines": [[10, 10]]}, {"id": "B", "file": "pkg/lib/x.py", "lines": [[42, 45]]},
             {"id": "C", "file": "pkg/lib/y.py", "lines": [[1, 1]]}]
    assert sr.match_items(found, items, 2, tmp_path) == {"A": [0], "B": [1], "C": []}


def test_review_loop_run_grades_recall_and_fixes(repo, tmp_path, review_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FIX", "1")
    assert sr.main(["run", "--fixture", str(review_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "5"]) == 0
    m = _manifest(tmp_path)
    log = json.loads((tmp_path / "fake.log").read_text())
    assert "simo-dev:review-loop" in log["stdin"] and "`feat/add`" in log["stdin"]
    assert log["strategy"] == {"plan_critique": "opus", "code_review": "opus"}
    assert log["acceptance_visible"] is False and m["permission_mode"] == "auto"
    o = m["outcome"]
    assert o["items"]["B1"] == {"severity": "high", "found": True, "fixed": True}
    assert o["items"]["B2"]["found"] is False and o["items"]["B2"]["fixed"] is True
    assert o["seeded"] == 2 and o["found"] == 1 and o["recall"] == 0.5 and o["fixed"] == 2
    assert o["findings"] == 2 and o["unmatched"] == 1 and o["by_severity"]["high"] == 1
    assert m["rounds"] == 1 and o["own_tests"]["passed"] == 1 and o["pass"] is True, o
    assert "a + b" in (tmp_path / "manifests" / f"{m['run_id']}.diff.txt").read_text()


def test_review_loop_input_is_uncommitted_on_the_branch(repo, tmp_path, review_env, monkeypatch):
    monkeypatch.setenv("SIMO_DEV_REPLAY_CLAUDE", str(tmp_path / "probe.py"))
    (tmp_path / "probe.py").write_text(
        "#!/usr/bin/env python3\nimport json, subprocess, sys\n"
        "if '--version' in sys.argv: print('9.9.9'); sys.exit(0)\nsys.stdin.read()\n"
        f"open({str(tmp_path / 'probe.json')!r}, 'w').write(json.dumps([subprocess.run(c, capture_output=True, text=True).stdout"
        " for c in (['git', 'branch', '--show-current'], ['git', 'status', '--porcelain'], ['git', 'rev-list', '--count', 'HEAD'])]))\n"
        "print(json.dumps({'result': 'DONE'}))\n")
    (tmp_path / "probe.py").chmod(0o755)
    sr.main(["run", "--fixture", str(review_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "5"])
    branch, status, count = json.loads((tmp_path / "probe.json").read_text())
    assert branch.strip() == "feat/add" and " M src/app.py" in status and "?? tests/test_add.py" in status
    assert count.strip() == "1"


def test_review_loop_misses_a_high_item(repo, tmp_path, review_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FINDINGS", "[medium] src/app.py:1 — x\nverdict: mergeable — fine")
    sr.main(["run", "--fixture", str(review_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "5"])
    o = _manifest(tmp_path)["outcome"]
    assert o["items"]["B1"]["found"] is False and o["items"]["B2"]["found"] is True and o["recall"] == 0.5
    assert o["items"]["B1"]["fixed"] is False and o["pass"] is False


def test_mr_followup_run_serves_a_fake_forge(repo, tmp_path, followup_env):
    assert sr.main(["run", "--fixture", str(followup_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "5"]) == 0
    m = _manifest(tmp_path)
    gh = json.loads((tmp_path / "gh.log").read_text())
    assert gh["version"] == [0, "gh version replay-fake\n"]
    assert gh["view"] == [0, "feat/add\n"]
    threads = json.loads(gh["threads"][1])["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    assert threads[0]["path"] == "src/app.py" and threads[0]["comments"]["nodes"][0]["body"] == "this subtracts"
    assert json.loads(gh["checks"][1])[0]["bucket"] == "fail" and "assert -1 == 3" in gh["log"][1]
    log = json.loads((tmp_path / "fake.log").read_text())
    assert "simo-dev:mr-followup" in log["stdin"] and log["acceptance_visible"] is False
    o = m["outcome"]
    assert o["items"] == {"C1": {"kind": "ci", "resolved": True}, "C2": {"kind": "decline", "resolved": True}}
    assert o["pushed"] and o["dirty"] == [] and o["fix_commits"] == 1 and o["replies"] == 1
    assert o["rounds"] == 1 and m["rounds"] == 1 and o["ci_reads"] == 1, o
    assert o["pass"] is True, json.dumps(o)
    assert m["input_head"] and m["input_head"] != ""


def test_mr_followup_unpushed_fix_fails(repo, tmp_path, followup_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_NO_PUSH", "1")
    sr.main(["run", "--fixture", str(followup_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "5"])
    o = _manifest(tmp_path)["outcome"]
    assert o["resolved"] == 2 and o["pushed"] is False and o["pass"] is False


def test_fake_gh_ci_turns_green_after_a_pushed_fix(repo, tmp_path, followup_env, monkeypatch):
    monkeypatch.setenv("SIMO_DEV_REPLAY_WORK", str(tmp_path / "work"))
    sr.main(["run", "--fixture", str(followup_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "5", "--keep"])
    m = _manifest(tmp_path)
    run_dir = tmp_path / "work" / m["run_id"]
    env = {**os.environ, "SIMO_DEV_REPLAY_FORGE": str(run_dir / "forge")}
    out = subprocess.run([str(run_dir / "bin" / "gh"), "pr", "checks", "7", "--json", "bucket", "-q", ".[].bucket"],
                         capture_output=True, text=True, env=env, cwd=run_dir / "repo")
    assert out.stdout.strip() == "pass"
    bad = subprocess.run([str(run_dir / "bin" / "gh"), "repo", "delete"], capture_output=True, text=True, env=env)
    assert bad.returncode == 1 and "unsupported" in bad.stderr


def test_materialized_plugin_drops_replay_fixtures(repo, tmp_path):
    fx = repo / sr.PLUGIN_REL / sr.FIXTURES_REL / "x"
    fx.mkdir(parents=True)
    (fx / "fixture.json").write_text("{}")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fixtures")
    sr.materialize_plugin(repo, sr.parse_variant("A=ref:HEAD"), tmp_path / "p")
    sr.materialize_plugin(repo, sr.parse_variant(f"B=dir:{repo / sr.PLUGIN_REL}"), tmp_path / "q")
    assert not (tmp_path / "p" / sr.FIXTURES_REL).exists() and not (tmp_path / "q" / sr.FIXTURES_REL).exists()


def test_calibrate_change_fixture(repo, tmp_path, review_env, followup_env, capsys):
    assert sr.main(["calibrate", "--fixture", str(review_env)]) == 1  # B2 passes on the input
    out = capsys.readouterr().out
    assert "reference.patch: acceptance 2/2" in out and "B1   test_add_fixed" in out
    assert "B2" in out and "expected failed" in out.split("B2", 1)[1].splitlines()[0]
    capsys.readouterr()
    assert sr.main(["calibrate", "--fixture", str(followup_env)]) == 0
    out = capsys.readouterr().out
    assert "own tests passed 1 failed 1" in out and "C2   test_x_kept" in out


def test_compare_shows_recall(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIMO_DEV_REPLAY_DIR", str(tmp_path))
    for i, (label, recall) in enumerate([("A", 1.0), ("A", 0.5), ("B", 0.5), ("B", None)]):
        rec = {"schema": 1, "run_id": f"r{i}", "batch": "b1", **_m(label, 2.0 + i), "step": "review-loop"}
        rec["outcome"]["recall"] = recall
        (tmp_path / f"r{i}.json").write_text(json.dumps(rec))
    assert sr.main(["compare"]) == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: line.split() for line in out.splitlines() if line.strip()[:2] in ("A ", "B ") and line.split()[1].isdigit()}
    assert "recall" in out and rows["A"][-1] == "0.75" and rows["B"][-1] == "0.25"


def _handback_session(tmp_path, monkeypatch, reports, critic="simo-dev:code-reviewer"):
    """A main transcript whose reviewer reports arrive as hand-backs: the first on an attachment's origin,
    later ones on user lines (CLI 2.1.322); the reviewer's own transcript holds no report text."""
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    main = tmp_path / "h" / ".claude" / "projects" / "-x" / "sid-hb.jsonl"
    subs = main.with_suffix("") / "subagents"
    subs.mkdir(parents=True)
    (subs / "agent-abc.jsonl").write_text(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}}) + "\n")
    (subs / "agent-abc.meta.json").write_text(json.dumps({"agentType": critic}))
    lines = []
    for i, text in enumerate(reports):
        origin = {"kind": "peer", "from": "abc", "name": critic, "body": "frame\n  " + text, "handback": True,
                  "handbackReportText": text}
        lines.append({"type": "attachment", "attachment": {"type": "queued_command", "origin": origin}} if i == 0
                     else {"type": "user", "origin": origin, "message": {"role": "user", "content": "frame"}})
        lines.append({"type": "queue-operation", "content": text})
    main.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return "sid-hb"


def test_review_findings_from_handbacks_first_review_only(tmp_path, monkeypatch):
    sid = _handback_session(tmp_path, monkeypatch, [
        "  [high] src/app.py:4 — bug\n  verdict: not-mergeable — x",
        "  [medium] src/app.py:1 — later, on fixed code\n  verdict: mergeable — y",
        "  [high] src/app.py:4 — bug\n  verdict: not-mergeable — x"])
    assert len(sr.critic_texts(sid, "simo-dev:code-reviewer")) == 2
    assert sr.critic_texts(sid, "simo-dev:plan-reviewer") == []
    items = [{"id": "B1", "file": "src/app.py", "lines": [[4, 4]]}, {"id": "B2", "file": "src/app.py", "lines": [[1, 1]]}]
    got = sr.review_findings(sid, "simo-dev:code-reviewer", items, 0, tmp_path)
    assert got["hits"] == {"B1": [0], "B2": []}
    assert got["rounds"] == 2 and got["findings"] == 2 and got["first_findings"] == 1 and got["unmatched"] == 0


def test_regrade_recomputes_review_manifests(repo, tmp_path, review_env, monkeypatch, capsys):
    sid = _handback_session(tmp_path, monkeypatch, ["  [high] src/app.py:4 — bug\n  verdict: not-mergeable — x"])
    manifests = tmp_path / "manifests"
    manifests.mkdir(exist_ok=True)
    stale = {"schema": 1, "run_id": "r1", "batch": "b1", "step": "review-loop", "fixture": "review-sample",
             "session_id": sid, "rounds": 0, "outcome": {"done": True, "blocked": False, "own_ok": True, "recall": 0.0,
                                                         "items": {"B1": {"fixed": True}, "B2": {"fixed": False}}}}
    (manifests / "r1.json").write_text(json.dumps(stale))
    assert sr.main(["regrade", "--fixture", str(review_env)]) == 0
    m = json.loads((manifests / "r1.json").read_text())
    o = m["outcome"]
    assert o["recall"] == 0.5 and o["items"]["B1"] == {"severity": "high", "found": True, "fixed": True}
    assert o["fixed"] == 1 and o["pass"] is True and m["rounds"] == 1 and m["regraded"]


# --- checks before a paid batch: the fixture's CI, then a canary run ---------------------------------

def _set_ci(fixture, command):
    data = json.loads((fixture / "fixture.json").read_text())
    data["forge"]["ci"]["command"] = command
    (fixture / "fixture.json").write_text(json.dumps(data))


def _no_run_started(tmp_path):
    return not (tmp_path / "fake.log").exists() and not list((tmp_path / "manifests").glob("*.json")) \
        and not list((tmp_path / "work").iterdir())


def test_fixture_ci_check_green_on_reference_red_on_changes(repo, tmp_path, followup_env):
    checks = sr.fixture_ci_check(sr.load_fixture(followup_env), repo)
    assert [(c["patch"], c["bucket"], c["ok"]) for c in checks] == [("reference.patch", "pass", True),
                                                                     ("changes.patch", "fail", True)]
    assert all(c["confined"] for c in checks) == bool(sr.shutil.which("sandbox-exec"))
    assert "assert -1 == 3" in checks[1]["log"]
    assert not list((tmp_path / "work").iterdir())


def test_fixture_ci_check_skips_a_fixture_without_ci(repo, review_env):
    assert sr.fixture_ci_check(sr.load_fixture(review_env), repo) == []


@pytest.mark.parametrize("rc", [0, 1])
def test_run_refuses_a_fixture_whose_ci_ignores_the_patch(repo, tmp_path, followup_env, rc, capsys):
    _set_ci(followup_env, [sys.executable, "-c", f"raise SystemExit({rc})"])
    assert sr.main(["run", "--fixture", str(followup_env), "--variant", "A=ref:HEAD~1", "-n", "2", "--budget", "5"]) == 2
    err = capsys.readouterr().err
    assert "no run started" in err and ("changes.patch" if rc == 0 else "reference.patch") in err.split("CI log tail")[1]
    assert _no_run_started(tmp_path)


@pytest.mark.skipif(not sr.shutil.which("sandbox-exec"), reason="needs sandbox-exec")
def test_fixture_ci_check_runs_inside_the_run_sandbox(repo, tmp_path, followup_env, monkeypatch, capsys):
    # Green outside the sandbox, red inside: the CI writes into the source repo, which every run is denied
    # (observed 2026-11-13: pytest's basetemp in a denied temp dir kept the check red after every fix).
    probe = tmp_path / "ci_probe.py"
    probe.write_text(f"import subprocess, sys\nopen({str(repo / 'ci-probe')!r}, 'w').write('x')\n"
                     "sys.exit(subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', "
                     "'tests/test_add.py']).returncode)\n")
    _set_ci(followup_env, [sys.executable, str(probe)])
    monkeypatch.setenv("SIMO_DEV_REPLAY_GRADE_SANDBOX", "0")
    assert all(c["ok"] for c in sr.fixture_ci_check(sr.load_fixture(followup_env), repo))
    monkeypatch.delenv("SIMO_DEV_REPLAY_GRADE_SANDBOX")
    reference = sr.fixture_ci_check(sr.load_fixture(followup_env), repo)[0]
    assert reference["confined"] and reference["bucket"] == "fail" and "Operation not permitted" in reference["log"]
    assert sr.main(["run", "--fixture", str(followup_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "5"]) == 2
    assert _no_run_started(tmp_path)


@pytest.mark.parametrize("env, reason", [
    ({"FAKE_CLAUDE_RC": "1"}, "status error"),
    ({"FAKE_CLAUDE_RESULT": "Review loop report.\nBLOCKED: every Bash call denied"}, "ended BLOCKED: every Bash call denied"),
    ({"FAKE_CLAUDE_DENIALS": "3"}, "3 permission denial(s) in"),
])
def test_canary_stops_the_batch(repo, tmp_path, review_env, monkeypatch, capsys, env, reason):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    rc = sr.main(["run", "--fixture", str(review_env), "--variant", "A=ref:HEAD~1", "--variant", "B=ref:HEAD~1",
                  "-n", "2", "--budget", "5"])
    assert rc == 3
    (manifest,) = (tmp_path / "manifests").glob("*.json")
    assert "-A-1" in manifest.name
    err = capsys.readouterr().err
    assert "canary" in err and reason in err and "stopped after it" in err


def test_canary_passes_and_the_batch_runs(repo, tmp_path, review_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FIX", "1")
    assert sr.main(["run", "--fixture", str(review_env), "--variant", "A=ref:HEAD~1", "--variant", "B=ref:HEAD~1",
                    "-n", "2", "--budget", "5"]) == 0
    assert len(list((tmp_path / "manifests").glob("*.json"))) == 4


def test_canary_failure_rules():
    ok = {"status": "ok", "outcome": {"blocked": False}, "denials": 0, "wall_s": 5}
    assert sr.canary_failure(ok) == ""
    assert sr.canary_failure({**ok, "status": "timeout"}) == "status timeout"
    assert sr.canary_failure({**ok, "outcome": {"blocked": True, "reason": "x"}}) == "ended BLOCKED: x"
    assert sr.canary_failure({**ok, "denials": 2}) == "2 permission denial(s) in 5 s"
    assert sr.canary_failure({**ok, "denials": 2, "wall_s": sr.CANARY_FAST_S}) == ""


def _line(**rec):
    return json.dumps(rec) + "\n"


def test_preload_renders_the_main_chain_until_the_skill(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    main = tmp_path / ".claude" / "projects" / "-x" / "sid-p.jsonl"
    main.parent.mkdir(parents=True)
    use = {"type": "tool_use", "name": "Skill", "input": {"skill": "simo-dev:plan-execute", "args": "plan.md"}}
    usage = {"input_tokens": 10, "cache_read_input_tokens": 300_000, "cache_creation_input_tokens": 5}
    main.write_text(
        _line(type="user", message={"content": "before the compaction"})
        + _line(type="system", subtype="compact_boundary")
        + _line(type="user", message={"content": "summary of the earlier work"})
        + _line(type="attachment", attachment={"type": "instructions", "files": [{"path": "a", "content": "house rules"}]})
        + _line(type="attachment", attachment={"type": "queued_command", "prompt": "also do y"})
        + _line(type="attachment", attachment={"type": "skill_listing", "content": "listed skills"})
        + _line(type="assistant", message={"content": [{"type": "thinking", "thinking": "secret"},
                                                       {"type": "text", "text": "I read it"},
                                                       {"type": "tool_use", "name": "Read",
                                                        "input": {"file_path": f"{repo}/src/app.py"}}]})
        + _line(type="user", message={"content": [{"type": "tool_result", "content": [{"type": "text", "text": "x = 1"}]}]})
        + _line(type="assistant", isSidechain=True, message={"content": [{"type": "text", "text": "subagent talk"}]})
        + _line(type="assistant", cwd=str(repo), message={"content": [use], "usage": usage})
        + _line(type="user", message={"content": "after the invocation"}))
    out = tmp_path / "pre" / "ctx.md"
    assert sr.main(["preload", "--session", "sid-p", "--out", str(out)]) == 0
    text = out.read_text()
    assert "summary of the earlier work" in text and "I read it" in text and "x = 1" in text
    assert "## attachment instructions\nhouse rules" in text and "also do y" in text
    assert '[tool call Read] {"file_path": "{{REPO}}/src/app.py"}' in text
    for absent in ("before the compaction", "secret", "subagent talk", "after the invocation", "listed skills", str(repo)):
        assert absent not in text
    assert "carried 300015 tokens" in capsys.readouterr().out
    assert sr.main(["preload", "--session", "sid-p", "--until", "simo-dev:nope", "--out", str(out)]) == 2


def test_run_with_preload_puts_it_before_the_prompt(repo, tmp_path, replay_env):
    pre = tmp_path / "ctx.md"
    pre.write_text("## user\nwork in {{REPO}}/src\n")
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20",
                    "--preload", str(pre)]) == 0
    stdin = json.loads((tmp_path / "fake.log").read_text())["stdin"]
    assert stdin.startswith(sr.PRELOAD_FRAME.split("{text}")[0])
    assert stdin.index("</earlier-session>") < stdin.index("simo-dev:plan-write")
    assert "{{REPO}}" not in stdin and f"work in {tmp_path / 'work'}" in stdin
    m = json.loads(next((tmp_path / "manifests").glob("*.json")).read_text())
    assert m["preload"]["chars"] == len(pre.read_text()) and m["preload_sig"] == sr.load_preload(pre)["sig"]
    assert "preload_sig" in sr.MANIFEST_MIX_FIELDS


def test_run_refuses_an_empty_preload(repo, tmp_path, replay_env):
    (tmp_path / "empty.md").write_text("\n")
    assert sr.main(["run", "--fixture", str(replay_env), "--variant", "A=ref:HEAD~1", "-n", "1", "--budget", "20",
                    "--preload", str(tmp_path / "empty.md")]) == 2
    assert not (tmp_path / "manifests").exists()


def test_estimate_counts_only_runs_with_the_same_preload(repo, tmp_path, replay_env, capsys):
    (tmp_path / "manifests").mkdir()
    pre = tmp_path / "ctx.md"
    pre.write_text("context\n")
    sig = sr.load_preload(pre)["sig"]
    base = {"schema": 1, "fixture": "plan-write-sample", "step": "plan-write", "status": "ok",
            "model": "opus", "critique_model": "opus", "effort": "medium"}
    (tmp_path / "manifests" / "r1.json").write_text(json.dumps({**base, "cost": {"total": 2.0}}))
    (tmp_path / "manifests" / "r2.json").write_text(json.dumps({**base, "cost": {"total": 6.0}, "preload_sig": sig}))
    assert sr.main(["estimate", "--fixture", str(replay_env)]) == 0
    assert "$2.0 per run" in capsys.readouterr().out
    assert sr.main(["estimate", "--fixture", str(replay_env), "--preload", str(pre)]) == 0
    assert "$6.0 per run" in capsys.readouterr().out
