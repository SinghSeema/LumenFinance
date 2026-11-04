"""Tests for the config loader."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emi_agent.config import load, _deep_merge, _validate_keys


def test_load_defaults():
    cfg = load()
    assert cfg["stt"]["provider"] == "deepgram"
    assert cfg["tts"]["provider"] == "sarvam"
    assert cfg["llm"]["provider"] == "sarvam"
    assert cfg["behavior"]["verify_max_attempts"] == 3


def test_load_profile_hindi_bulbul():
    cfg = load(profile="hindi_bulbul")
    assert cfg["tts"]["provider"] == "sarvam"
    assert cfg["tts"]["model"] == "bulbul:v4-flash"
    assert cfg["stt"]["provider"] == "deepgram"


def test_load_profile_cartesia_en():
    cfg = load(profile="cartesia_en")
    assert cfg["tts"]["provider"] == "cartesia"
    # Keys not in the profile inherit from defaults
    assert cfg["stt"]["provider"] == "deepgram"


def test_unknown_key_in_profile(tmp_path, monkeypatch):
    import yaml
    bad_profile = tmp_path / "bad.yaml"
    bad_profile.write_text(yaml.dump({"stt": {"nonexistent_key": True}}))

    import emi_agent.config as cfg_mod
    orig = cfg_mod._CONFIGS_DIR
    monkeypatch.setattr(cfg_mod, "_CONFIGS_DIR", tmp_path)
    # Also create a defaults file in tmp
    import shutil
    shutil.copy(orig / "defaults.yaml", tmp_path / "defaults.yaml")
    Path(tmp_path / "profiles").mkdir()
    shutil.copy(bad_profile, tmp_path / "profiles" / "bad.yaml")

    with pytest.raises(ValueError, match="Unknown config key"):
        cfg_mod.load(profile="bad")


def test_deep_merge_overwrites_leaf():
    base = {"a": {"b": 1, "c": 2}}
    override = {"a": {"b": 99}}
    result = _deep_merge(base, override)
    assert result["a"]["b"] == 99
    assert result["a"]["c"] == 2  # preserved


def test_deep_merge_does_not_mutate_base():
    base = {"a": {"b": 1}}
    override = {"a": {"b": 2}}
    _deep_merge(base, override)
    assert base["a"]["b"] == 1


def test_env_var_injection(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-dg-key")
    cfg = load()
    assert cfg["stt"]["deepgram_api_key"] == "test-dg-key"


def test_set_override_parses_value():
    cfg = load(overrides=["turn.vad_stop_secs=0.4", "llm.provider=groq"])
    assert cfg["turn"]["vad_stop_secs"] == 0.4
    assert cfg["llm"]["provider"] == "groq"


def test_set_override_unknown_key_rejected():
    with pytest.raises(ValueError, match="Unknown config key"):
        load(overrides=["turn.vad_stop_sec=0.4"])


def test_missing_profile_raises():
    with pytest.raises(FileNotFoundError):
        load(profile="does_not_exist")


def test_borrowers_json_valid():
    import json
    from datetime import date
    path = Path(__file__).parent.parent / "data" / "borrowers.json"
    data = json.loads(path.read_text())
    assert len(data) >= 5
    for b in data:
        assert "borrower_id" in b
        assert "dob" in b
        assert "last4" in b
        assert len(b["last4"]) == 4
        assert b["preferred_language"] in {"en", "hi", "hinglish"}
        date.fromisoformat(b["dob"])   # must be a valid date
        date.fromisoformat(b["due_date"])


def test_set_overrides_a_key_whose_default_is_null():
    assert load()["llm"]["timeout_secs"] is None
    assert load(overrides=["llm.timeout_secs=4"])["llm"]["timeout_secs"] == 4
    with pytest.raises(ValueError, match="Unknown config key"):
        load(overrides=["llm.no_such_key=1"])
