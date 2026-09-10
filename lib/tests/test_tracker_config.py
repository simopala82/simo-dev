"""Tests for lib/tracker_config.py (global tracker adapter config)."""
import importlib.util
import json
from pathlib import Path

LIB = Path(__file__).parent.parent / "tracker_config.py"

spec = importlib.util.spec_from_file_location("tracker_config", LIB)
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)


def test_get_absent_prefix_returns_empty(tmp_path):
    path = tmp_path / "tracker.json"
    assert tc.get_field("CUS", "fetch_skill", path) == ""


def test_get_absent_field_returns_empty(tmp_path):
    path = tmp_path / "tracker.json"
    path.write_text(json.dumps({"CUS": {"fetch_skill": "team:jira-ops"}}))
    assert tc.get_field("CUS", "transition_cmd", path) == ""


def test_set_then_get_roundtrip(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "fetch_skill", "team:jira-ops", path)
    assert tc.get_field("CUS", "fetch_skill", path) == "team:jira-ops"


def test_set_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "tracker.json"
    tc.set_field("CUS", "fetch_skill", "team:jira-ops", path)
    assert path.exists()


def test_prefix_is_uppercased(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("cus", "fetch_skill", "team:jira-ops", path)
    assert tc.get_field("CUS", "fetch_skill", path) == "team:jira-ops"


def test_set_second_field_preserves_first(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "fetch_skill", "team:jira-ops", path)
    tc.set_field("CUS", "transition_cmd", "python3 /x.py", path)
    assert tc.get_field("CUS", "fetch_skill", path) == "team:jira-ops"
    assert tc.get_field("CUS", "transition_cmd", path) == "python3 /x.py"


def test_scope_fetch_cmd_roundtrip(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "scope_fetch_cmd", "python3 /x/jira_scope_fetch.py", path)
    assert tc.get_field("CUS", "scope_fetch_cmd", path) == "python3 /x/jira_scope_fetch.py"


def test_supports_subtasks_true(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "supports_subtasks", "true", path)
    assert tc.get_field("CUS", "supports_subtasks", path) == "true"


def test_supports_subtasks_invalid_value_exits_2(tmp_path):
    path = tmp_path / "tracker.json"
    import pytest
    with pytest.raises(SystemExit) as exc:
        tc.set_field("CUS", "supports_subtasks", "yes", path)
    assert exc.value.code == 2


def test_get_invalid_existing_json_exits_1(tmp_path):
    path = tmp_path / "tracker.json"
    path.write_text("{not json")
    import pytest
    with pytest.raises(SystemExit) as exc:
        tc.get_field("CUS", "fetch_skill", path)
    assert exc.value.code == 1


def test_cli_get_prints_empty_and_exits_0(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    rc = tc.main(["get", "CUS", "fetch_skill", "--config", str(path)])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_cli_set_then_cli_get(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    assert tc.main(["set", "CUS", "fetch_skill", "team:jira-ops", "--config", str(path)]) == 0
    assert tc.main(["get", "CUS", "fetch_skill", "--config", str(path)]) == 0
    assert capsys.readouterr().out == "team:jira-ops"


def test_config_path_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("SIMO_DEV_TRACKER_CONFIG", str(target))
    assert tc.config_path() == target


def test_config_path_default_no_override(monkeypatch):
    monkeypatch.delenv("SIMO_DEV_TRACKER_CONFIG", raising=False)
    assert tc.config_path() == Path.home() / ".claude" / "simo-dev" / "tracker.json"


def test_run_field_empty_cmd_is_noop(tmp_path):
    path = tmp_path / "tracker.json"
    assert tc.run_field("CUS", "transition_cmd", ["KEY", "State"], path) == 0


def test_run_field_splits_and_invokes(tmp_path, capfd):
    path = tmp_path / "tracker.json"
    target = tmp_path / "echo_args.py"
    target.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n")
    tc.set_field("CUS", "transition_cmd", f"python3 {target}", path)
    rc = tc.run_field("CUS", "transition_cmd", ["KEY-1", "CodeReview", "--leaf-only"], path)
    assert rc == 0
    assert capfd.readouterr().out.strip() == "KEY-1 CodeReview --leaf-only"


def test_run_field_propagates_nonzero_exit(tmp_path):
    path = tmp_path / "tracker.json"
    target = tmp_path / "fail.py"
    target.write_text("import sys\nsys.exit(3)\n")
    tc.set_field("CUS", "transition_cmd", f"python3 {target}", path)
    assert tc.run_field("CUS", "transition_cmd", [], path) == 3


def test_run_field_malformed_quoting_exits_1(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "transition_cmd", 'python3 "unterminated', path)
    rc = tc.run_field("CUS", "transition_cmd", [], path)
    assert rc == 1
    assert "not a valid shell command" in capsys.readouterr().err


def test_run_field_whitespace_only_cmd_exits_1(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "transition_cmd", "   ", path)
    rc = tc.run_field("CUS", "transition_cmd", [], path)
    assert rc == 1
    assert "not a valid shell command" in capsys.readouterr().err


def test_run_field_missing_executable_exits_1(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "transition_cmd", "/no/such/binary-xyz", path)
    rc = tc.run_field("CUS", "transition_cmd", [], path)
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_cli_run_empty_cmd_exits_0(tmp_path):
    path = tmp_path / "tracker.json"
    assert tc.main(["run", "--config", str(path), "CUS", "transition_cmd", "KEY", "State"]) == 0


def test_cli_run_invokes_and_forwards_args(tmp_path, capfd):
    path = tmp_path / "tracker.json"
    target = tmp_path / "echo_args.py"
    target.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n")
    tc.main(["set", "CUS", "transition_cmd", f"python3 {target}", "--config", str(path)])
    rc = tc.main(["run", "--config", str(path), "CUS", "transition_cmd", "KEY-1", "CodeReview", "--leaf-only"])
    assert rc == 0
    assert capfd.readouterr().out.strip() == "KEY-1 CodeReview --leaf-only"


def test_set_rejects_versioned_plugin_cache_path(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    pinned = "python3 /Users/x/.claude/plugins/cache/simo-local/simo-dev/0.1.140/lib/jira_forward_transition.py"
    import pytest
    with pytest.raises(SystemExit) as exc:
        tc.set_field("CUS", "transition_cmd", pinned, path)
    assert exc.value.code == 2
    assert "versioned plugin cache path" in capsys.readouterr().err


def test_set_rejects_versioned_plugin_cache_path_in_scope_fetch_cmd(tmp_path):
    path = tmp_path / "tracker.json"
    pinned = "python3 /Users/x/.claude/plugins/cache/simo-local/simo-dev/0.1.140/lib/jira_scope_fetch.py"
    import pytest
    with pytest.raises(SystemExit) as exc:
        tc.set_field("CUS", "scope_fetch_cmd", pinned, path)
    assert exc.value.code == 2


def test_set_rejects_versioned_plugin_cache_path_generic_marketplace(tmp_path):
    path = tmp_path / "tracker.json"
    pinned = "python3 /Users/x/.claude/plugins/cache/some-other-marketplace/some-plugin/2.3.10/lib/thing.py"
    import pytest
    with pytest.raises(SystemExit) as exc:
        tc.set_field("CUS", "transition_cmd", pinned, path)
    assert exc.value.code == 2


def test_set_accepts_source_working_tree_path(tmp_path):
    path = tmp_path / "tracker.json"
    stable = "python3 /Users/x/projects/myrepo/.claude/local-marketplace/simo-dev/lib/jira_forward_transition.py"
    tc.set_field("CUS", "transition_cmd", stable, path)
    assert tc.get_field("CUS", "transition_cmd", path) == stable


def test_set_accepts_near_miss_cache_mentions(tmp_path):
    path = tmp_path / "tracker.json"
    no_version = "python3 /Users/x/.claude/plugins/cache/simo-local/simo-dev/lib/jira_forward_transition.py"
    tc.set_field("CUS", "transition_cmd", no_version, path)
    assert tc.get_field("CUS", "transition_cmd", path) == no_version

    path2 = tmp_path / "tracker2.json"
    prose_mention = "the cache dir 0.1.140 was cleaned up, see docs"
    tc.set_field("CUS", "fetch_skill", prose_mention, path2)
    assert tc.get_field("CUS", "fetch_skill", path2) == prose_mention


def test_set_rejected_value_is_not_persisted(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "fetch_skill", "team:jira-ops", path)
    pinned = "python3 /Users/x/.claude/plugins/cache/simo-local/simo-dev/0.1.140/lib/jira_forward_transition.py"
    import pytest
    with pytest.raises(SystemExit):
        tc.set_field("CUS", "transition_cmd", pinned, path)
    assert tc.get_field("CUS", "transition_cmd", path) == ""
    assert tc.get_field("CUS", "fetch_skill", path) == "team:jira-ops"


def test_comment_cmd_roundtrip(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "comment_cmd", "python3 /x/jira_comment.py", path)
    assert tc.get_field("CUS", "comment_cmd", path) == "python3 /x/jira_comment.py"


def test_link_cmd_roundtrip(tmp_path):
    path = tmp_path / "tracker.json"
    tc.set_field("CUS", "link_cmd", "python3 /x/jira_link.py", path)
    assert tc.get_field("CUS", "link_cmd", path) == "python3 /x/jira_link.py"


def test_assign_cmd_roundtrip(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    assert tc.main(["set", "CUS", "assign_cmd", "python3 /x/jira_assign.py", "--config", str(path)]) == 0
    assert tc.main(["get", "CUS", "assign_cmd", "--config", str(path)]) == 0
    assert capsys.readouterr().out == "python3 /x/jira_assign.py"


def test_requirements_review_cmd_roundtrip(tmp_path, capsys):
    path = tmp_path / "tracker.json"
    assert tc.main(["set", "CUS", "requirements_review_cmd", "python3 /x/jira_requirements_review.py", "--config", str(path)]) == 0
    assert tc.main(["get", "CUS", "requirements_review_cmd", "--config", str(path)]) == 0
    assert capsys.readouterr().out == "python3 /x/jira_requirements_review.py"
