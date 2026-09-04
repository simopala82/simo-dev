"""Tests for lib/check_mr_state.py (pre-push MR/PR state guard)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "check_mr_state.py"

spec = importlib.util.spec_from_file_location("check_mr_state", LIB)
cms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cms)


def _fake_run(stdout="", returncode=0, stderr=""):
    def run(args, **kwargs):
        if returncode == 0:
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        raise subprocess.CalledProcessError(returncode, args, output=stdout, stderr=stderr)
    return run


def test_glab_state_opened(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "opened"})))
    assert cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"]) == 0


def test_glab_state_merged_stops(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "merged"})))
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == "merged"


def test_glab_state_closed_stops(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "closed"})))
    assert cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"]) == 1


def test_gh_state_open_uppercase(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "OPEN"})))
    assert cms.main(["feat/x", "--cwd", "/tmp", "--forge", "gh"]) == 0


def test_gh_state_merged_stops(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "MERGED"})))
    assert cms.main(["feat/x", "--cwd", "/tmp", "--forge", "gh"]) == 1


def test_glab_recognized_not_found_returns_3(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(returncode=1, stderr='No open merge request available for "feat/x".'),
    )
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 3
    assert capsys.readouterr().out.strip() == "none"


def test_gh_recognized_not_found_returns_3(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(returncode=1, stderr='no pull requests found for branch "feat/x"'),
    )
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "gh"])
    assert rc == 3
    assert capsys.readouterr().out.strip() == "none"


def test_glab_auth_error_returns_2_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(returncode=1, stderr="401 Unauthorized"),
    )
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_missing_binary_returns_2_unverifiable(monkeypatch, capsys):
    def raise_not_found(args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, "run", raise_not_found)
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_malformed_json_returns_2_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="not json"))
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_missing_state_key_returns_2_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({})))
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_empty_state_value_returns_2_unverifiable(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": ""})))
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_non_object_json_returns_2_unverifiable_not_crash(monkeypatch, capsys):
    # A syntactically valid but non-dict JSON payload (list/null/number/string)
    # must not raise AttributeError out of glab_state's .get() call.
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps(["opened"])))
    rc = cms.main(["feat/x", "--cwd", "/tmp", "--forge", "glab"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_auto_forge_tries_glab_then_gh(monkeypatch):
    def run(args, **kwargs):
        if args[0] == "glab":
            raise subprocess.CalledProcessError(
                1, args, output="", stderr='No open merge request available for "feat/x".'
            )
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"state": "OPEN"}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert cms.main(["feat/x", "--cwd", "/tmp"]) == 0


def test_auto_forge_prefers_glab_when_both_answer(monkeypatch):
    def run(args, **kwargs):
        if args[0] == "glab":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"state": "opened"}), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"state": "MERGED"}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert cms.main(["feat/x", "--cwd", "/tmp"]) == 0


def test_auto_forge_not_found_wins_over_unverifiable(monkeypatch, capsys):
    # glab confirms "no MR"; gh is simply missing from PATH -- the definitive
    # glab answer must win, not gh's "could not verify".
    def run(args, **kwargs):
        if args[0] == "glab":
            raise subprocess.CalledProcessError(
                1, args, output="", stderr='No open merge request available for "feat/x".'
            )
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, "run", run)
    rc = cms.main(["feat/x", "--cwd", "/tmp"])
    assert rc == 3
    assert capsys.readouterr().out.strip() == "none"


def test_auto_forge_both_unverifiable_returns_2(monkeypatch, capsys):
    # The single most realistic real-world trigger: --forge omitted, neither
    # glab nor gh can be asked (both missing from PATH).
    def raise_not_found(args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, "run", raise_not_found)
    rc = cms.main(["feat/x", "--cwd", "/tmp"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "unverifiable"


def test_ref_accepts_mr_iid_form(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen["ref"] = args[3]  # ["glab", "mr", "view", <ref>, "-F", "json"]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"state": "opened"}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert cms.main(["42", "--cwd", "/tmp", "--forge", "glab"]) == 0
    assert seen["ref"] == "42"


def test_forge_accepts_detect_remote_gitlab_spelling(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "merged"})))
    assert cms.main(["feat/x", "--cwd", "/tmp", "--forge", "gitlab"]) == 1


def test_forge_accepts_detect_remote_github_spelling(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "OPEN"})))
    assert cms.main(["feat/x", "--cwd", "/tmp", "--forge", "github"]) == 0


def test_cli_missing_from_path_end_to_end(tmp_path):
    """Real subprocess invocation with PATH stripped of glab/gh -- exercises
    the actual missing-binary branch, not a mock."""
    result = subprocess.run(
        [sys.executable, str(LIB), "feat/x", "--cwd", str(tmp_path), "--forge", "glab"],
        capture_output=True,
        text=True,
        env={"PATH": str(tmp_path)},
    )
    assert result.returncode == 2
    assert result.stdout.strip() == "unverifiable"


def test_cwd_reaches_subprocess_for_glab(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"state": "opened"}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert cms.main(["feat/x", "--cwd", "/tmp/wt", "--forge", "glab"]) == 0
    assert seen["cwd"] == "/tmp/wt"


def test_cwd_reaches_subprocess_for_gh(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"state": "OPEN"}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert cms.main(["feat/x", "--cwd", "/tmp/wt", "--forge", "gh"]) == 0
    assert seen["cwd"] == "/tmp/wt"


def test_missing_cwd_exits_2(monkeypatch):
    """A caller that forgot to scope the guard must not get an answer resolved
    from whatever directory the process happened to sit in -- argparse's usage
    error is rc 2, the same fail-closed STOP as an unverifiable state.

    The monkeypatch is what keeps this test off the network on regression: were
    --cwd to stop being required, an unpatched run would invoke the real glab.
    """
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "opened"})))
    with pytest.raises(SystemExit) as exc:
        cms.main(["feat/x", "--forge", "glab"])
    assert exc.value.code == 2


def test_unusable_cwd_returns_2_unverifiable(tmp_path):
    """--cwd pointing at a file raises NotADirectoryError out of subprocess --
    an OSError that is NOT FileNotFoundError, so it must still fail closed
    rather than escape as a traceback."""
    not_a_dir = tmp_path / "afile"
    not_a_dir.write_text("")
    result = subprocess.run(
        [sys.executable, str(LIB), "feat/x", "--cwd", str(not_a_dir), "--forge", "glab"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout.strip() == "unverifiable"


def test_relative_cwd_exits_2(monkeypatch):
    """A relative --cwd resolves against the ambient process cwd -- the defect
    the flag exists to close -- so it is rejected before any CLI is run. The
    monkeypatch would make a leaked-through call return "opened" (rc 0), so
    this test fails loudly rather than passing for the wrong reason."""
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=json.dumps({"state": "opened"})))
    with pytest.raises(SystemExit) as exc:
        cms.main(["feat/x", "--cwd", ".", "--forge", "glab"])
    assert exc.value.code == 2
