"""Tests for lib/model_strategy.py (global model-strategy config)."""
import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).parent.parent / "model_strategy.py"

spec = importlib.util.spec_from_file_location("model_strategy", LIB)
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


def test_get_absent_category_returns_empty(tmp_path):
    path = tmp_path / "model-strategy.json"
    assert ms.get_value("code_review", path) == ""


def test_set_then_get_roundtrip(tmp_path):
    path = tmp_path / "model-strategy.json"
    ms.set_value("code_review", "opus", path)
    assert ms.get_value("code_review", path) == "opus"


def test_set_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "model-strategy.json"
    ms.set_value("code_review", "opus", path)
    assert path.exists()


def test_set_second_category_preserves_first(tmp_path):
    path = tmp_path / "model-strategy.json"
    ms.set_value("code_review", "opus", path)
    ms.set_value("explore_research", "haiku", path)
    assert ms.get_value("code_review", path) == "opus"
    assert ms.get_value("explore_research", path) == "haiku"


def test_set_rejects_unknown_category(tmp_path, capsys):
    path = tmp_path / "model-strategy.json"
    with pytest.raises(SystemExit) as exc:
        ms.set_value("background_mechanical", "haiku", path)
    assert exc.value.code == 2
    assert "unknown category" in capsys.readouterr().err


def test_get_invalid_existing_json_exits_1(tmp_path):
    path = tmp_path / "model-strategy.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        ms.get_value("code_review", path)
    assert exc.value.code == 1


def test_cli_get_prints_empty_and_exits_0(tmp_path, capsys):
    path = tmp_path / "model-strategy.json"
    rc = ms.main(["get", "code_review", "--config", str(path)])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_cli_set_then_cli_get(tmp_path, capsys):
    path = tmp_path / "model-strategy.json"
    assert ms.main(["set", "code_review", "opus", "--config", str(path)]) == 0
    assert ms.main(["get", "code_review", "--config", str(path)]) == 0
    assert capsys.readouterr().out == "opus"


def test_cli_set_rejects_unknown_category_exits_2(tmp_path):
    path = tmp_path / "model-strategy.json"
    with pytest.raises(SystemExit) as exc:
        ms.main(["set", "not_a_category", "opus", "--config", str(path)])
    assert exc.value.code == 2


def test_config_path_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("SIMO_DEV_MODEL_STRATEGY_CONFIG", str(target))
    assert ms.config_path() == target


def test_config_path_default_no_override(monkeypatch):
    monkeypatch.delenv("SIMO_DEV_MODEL_STRATEGY_CONFIG", raising=False)
    assert ms.config_path() == Path.home() / ".claude" / "simo-dev" / "model-strategy.json"


def test_plan_execute_is_a_valid_category(tmp_path):
    path = tmp_path / "model-strategy.json"
    ms.set_value("plan_execute", "opus", path)
    assert ms.get_value("plan_execute", path) == "opus"
