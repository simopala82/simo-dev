"""Tests for lib/check_deploy_required.py (plan **Deploy required:** detector)."""
import importlib.util
from pathlib import Path

LIB = Path(__file__).parent.parent / "check_deploy_required.py"

spec = importlib.util.spec_from_file_location("check_deploy_required", LIB)
cdr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdr)


def _plan(tmp_path, name, body):
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / name).write_text(body)


def test_one_match_deploy_required_no(tmp_path):
    _plan(tmp_path, "2026-09-17-proj-9-no-deploy-example.md", "**Deploy required:** no\n")
    assert cdr.is_no_deploy(tmp_path, "PROJ-9") is True


def test_one_match_deploy_required_yes(tmp_path):
    _plan(tmp_path, "2026-09-17-proj-9-no-deploy-example.md", "**Deploy required:** yes\n")
    assert cdr.is_no_deploy(tmp_path, "PROJ-9") is False


def test_one_match_field_absent(tmp_path):
    _plan(tmp_path, "2026-09-17-proj-9-no-deploy-example.md", "**Goal:** does a thing\n")
    assert cdr.is_no_deploy(tmp_path, "PROJ-9") is False


def test_zero_matches(tmp_path):
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    assert cdr.is_no_deploy(tmp_path, "PROJ-9") is False


def test_no_plans_dir_at_all(tmp_path):
    assert cdr.is_no_deploy(tmp_path, "PROJ-9") is False


def test_two_matches_is_ambiguous(tmp_path):
    _plan(tmp_path, "2026-09-17-proj-9-no-deploy-example.md", "**Deploy required:** no\n")
    _plan(tmp_path, "2026-09-18-proj-9-second.md", "**Deploy required:** no\n")
    assert cdr.is_no_deploy(tmp_path, "PROJ-9") is False


def test_decoy_key_not_anchored_after_date(tmp_path):
    _plan(tmp_path, "2026-09-17-other-ticket-mentions-proj-9-in-slug.md", "**Deploy required:** no\n")
    assert cdr.is_no_deploy(tmp_path, "PROJ-9") is False


def test_cli_prints_1(tmp_path, capsys):
    _plan(tmp_path, "2026-09-17-proj-9-no-deploy-example.md", "**Deploy required:** no\n")
    assert cdr.main(["PROJ-9", "--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "1"


def test_cli_prints_0(tmp_path, capsys):
    assert cdr.main(["PROJ-9", "--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "0"
