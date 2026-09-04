"""Tests for lib/jira_scope.py."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).parent.parent / "jira_scope.py"
TC = Path(__file__).parent.parent / "tracker_config.py"
FAKE_SCOPE_CMD = Path(__file__).parent / "fixtures" / "fake_scope_cmd.py"


def _cache_safe_path(tmp_path, src):
    # copies src into tmp_path so its path never trips tracker_config.py's cache-path guard
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    return dst


def _tracker_config(tmp_path, cmd_value):
    path = tmp_path / "tracker.json"
    subprocess.run(
        [sys.executable, str(TC), "set", "PROJ", "scope_fetch_cmd", cmd_value, "--config", str(path)],
        check=True,
    )
    return path


def run_scope(args, tracker_config_path, extra_env=None):
    env = dict(os.environ)
    env["SIMO_DEV_TRACKER_CONFIG"] = str(tracker_config_path)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, str(LIB)] + args,
        capture_output=True, text=True, env=env,
    )
    return result.returncode, result.stdout, result.stderr


def test_light_mode_returns_summary_with_zero_comments(tmp_path):
    cfg = _tracker_config(tmp_path, f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}")
    rc, out, err = run_scope(["--mode", "light", "--key", "PROJ-1234"], cfg)
    assert rc == 0, err
    assert "PROJ-1234: Canned summary" in out
    assert "comments_shown=0" in out


def test_full_mode_requests_comments(tmp_path):
    cfg = _tracker_config(tmp_path, f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}")
    rc, out, err = run_scope(["--mode", "full", "--key", "PROJ-1234", "--comments", "10"], cfg)
    assert rc == 0, err
    assert "comments_shown=10" in out


def test_no_key_returns_empty(tmp_path):
    cfg = _tracker_config(tmp_path, f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}")
    rc, out, _ = run_scope(["--mode", "light", "--key", ""], cfg)
    assert rc == 0
    assert out == ""


def test_no_tracker_marker_short_circuits(tmp_path):
    cfg = _tracker_config(tmp_path, f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}")
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "no-tracker").write_text("x")
    rc, out, _ = run_scope(["--mode", "light", "--key", "PROJ-1234", "--repo-root", str(repo)], cfg)
    assert rc == 0
    assert out == ""


def test_unconfigured_prefix_returns_empty(tmp_path):
    cfg = tmp_path / "tracker.json"  # never populated
    rc, out, _ = run_scope(["--mode", "light", "--key", "PROJ-1234"], cfg)
    assert rc == 0
    assert out == ""


def test_malformed_tracker_config_returns_empty(tmp_path):
    cfg = tmp_path / "tracker.json"
    cfg.write_text("{not json")
    rc, out, _ = run_scope(["--mode", "light", "--key", "PROJ-1234"], cfg)
    assert rc == 0
    assert out == ""


def test_non_dict_tracker_config_returns_empty(tmp_path):
    cfg = tmp_path / "tracker.json"
    cfg.write_text("[]")
    rc, out, _ = run_scope(["--mode", "light", "--key", "PROJ-1234"], cfg)
    assert rc == 0
    assert out == ""


def test_malformed_scope_fetch_cmd_returns_empty(tmp_path):
    cfg = _tracker_config(tmp_path, 'python3 "/bad/unterminated')
    rc, out, _ = run_scope(["--mode", "light", "--key", "PROJ-1234"], cfg)
    assert rc == 0
    assert out == ""


def test_declined_sentinel_returns_empty(tmp_path):
    cfg = _tracker_config(tmp_path, "-")
    rc, out, _ = run_scope(["--mode", "light", "--key", "PROJ-1234"], cfg)
    assert rc == 0
    assert out == ""


def test_scope_cmd_failure_returns_empty(tmp_path):
    cfg = _tracker_config(tmp_path, f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}")
    rc, out, _ = run_scope(["--mode", "light", "--key", "FAIL-1"], cfg)
    assert rc == 0
    assert out == ""


def test_timeout_returns_empty(tmp_path):
    cfg = _tracker_config(tmp_path, f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_SCOPE_CMD)}")
    rc, out, _ = run_scope(
        ["--mode", "light", "--key", "SLOW-1"], cfg, extra_env={"DEV_REVIEW_JIRA_TIMEOUT": "0.2"},
    )
    assert rc == 0
    assert out == ""


def test_ansi_codes_are_stripped(tmp_path):
    FAKE_ANSI = Path(__file__).parent / "fixtures" / "fake_jira_ansi.py"
    cfg = _tracker_config(tmp_path, f"{sys.executable} {_cache_safe_path(tmp_path, FAKE_ANSI)}")
    rc, out, err = run_scope(["--mode", "light", "--key", "PROJ-1"], cfg)
    assert rc == 0, err
    assert "\x1b[" not in out
    assert "PROJ-1: Title" in out
    assert "Bold body" in out
