"""
Config loader: defaults.yaml → profile → env vars → --set overrides.
Unknown keys are errors. Guardrail behaviour is not present in the config
and cannot be toggled via profiles.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).parent.parent.parent
_CONFIGS_DIR = _ROOT / "configs"
_DEFAULTS_PATH = _CONFIGS_DIR / "defaults.yaml"


def load_dotenv(path: Path = _ROOT / ".env") -> None:
    """Minimal .env reader: KEY=value lines; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

# Env-var prefix → config path, e.g. LIVEKIT_URL → livekit.url
_ENV_MAP: dict[str, str] = {
    "LIVEKIT_URL": "livekit.url",
    "LIVEKIT_API_KEY": "livekit.api_key",
    "LIVEKIT_API_SECRET": "livekit.api_secret",
    "DEEPGRAM_API_KEY": "stt.deepgram_api_key",
    "SARVAM_API_KEY": "tts.sarvam_api_key",
    "CARTESIA_API_KEY": "tts.cartesia_api_key",
    "ANTHROPIC_API_KEY": "llm.anthropic_api_key",
    "GROQ_API_KEY": "llm.groq_api_key",
    "GEMINI_API_KEY": "llm.gemini_api_key",
    "LANGFUSE_PUBLIC_KEY": "langfuse.public_key",
    "LANGFUSE_SECRET_KEY": "langfuse.secret_key",
    "LANGFUSE_HOST": "langfuse.host",
}

_REQUIRED_KEYS_BY_PROVIDER: dict[str, list[str]] = {
    "deepgram_stt": ["stt.deepgram_api_key"],
    "sarvam_tts": ["tts.sarvam_api_key"],
    "cartesia_tts": ["tts.cartesia_api_key"],
    "anthropic_llm": ["llm.anthropic_api_key"],
    "groq_llm": ["llm.groq_api_key"],
    "gemini_llm": ["llm.gemini_api_key"],
    "sarvam_llm": ["tts.sarvam_api_key"],  # same key as Sarvam TTS
    "livekit": ["livekit.url", "livekit.api_key", "livekit.api_secret"],
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def _set_nested(d: dict, dotpath: str, value: Any) -> None:
    parts = dotpath.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


_MISSING = object()


def _get_nested(d: dict, dotpath: str, default: Any = None) -> Any:
    parts = dotpath.split(".")
    for part in parts:
        if not isinstance(d, dict) or part not in d:
            return default
        d = d[part]
    return d


def _validate_keys(config: dict, reference: dict, path: str = "") -> None:
    """Raise ValueError for any key in config not present in reference."""
    for k in config:
        full = f"{path}.{k}".lstrip(".")
        if k not in reference:
            raise ValueError(f"Unknown config key: {full!r}")
        if isinstance(config[k], dict) and isinstance(reference.get(k), dict):
            _validate_keys(config[k], reference[k], full)


def load(
    profile: str | None = None,
    overrides: list[str] | None = None,
    *,
    require_keys: list[str] | None = None,
) -> dict:
    """
    Load and return the resolved config dict.

    profile:   name of a yaml file under configs/profiles/ (without .yaml)
    overrides: list of "section.key=value" strings from --set CLI flags
    require_keys: provider-capability names whose API keys must be present,
                  e.g. ["deepgram_stt", "livekit"]
    """
    with open(_DEFAULTS_PATH) as f:
        config = yaml.safe_load(f)
    defaults_ref = copy.deepcopy(config)

    if profile:
        profile_path = _CONFIGS_DIR / "profiles" / f"{profile}.yaml"
        if not profile_path.exists():
            raise FileNotFoundError(f"Profile not found: {profile_path}")
        with open(profile_path) as f:
            profile_data = yaml.safe_load(f) or {}
        _validate_keys(profile_data, defaults_ref)
        config = _deep_merge(config, profile_data)

    # Inject env-var secrets (not validated against defaults_ref — secrets are
    # not in defaults.yaml by design).
    for env_name, dotpath in _ENV_MAP.items():
        val = os.environ.get(env_name)
        if val:
            _set_nested(config, dotpath, val)

    # --set overrides
    for override in overrides or []:
        if "=" not in override:
            raise ValueError(f"--set value must be key=value, got: {override!r}")
        dotpath, val = override.split("=", 1)
        if _get_nested(defaults_ref, dotpath, _MISSING) is _MISSING:  # a null default is still a key
            raise ValueError(f"Unknown config key: {dotpath!r}")
        _set_nested(config, dotpath, yaml.safe_load(val))

    # Check required keys
    missing = []
    for cap in (require_keys or []):
        for dotpath in _REQUIRED_KEYS_BY_PROVIDER.get(cap, []):
            if not _get_nested(config, dotpath):
                missing.append(f"{dotpath} (for {cap})")
    if missing:
        raise EnvironmentError(
            "Missing required config/env vars:\n  " + "\n  ".join(missing)
        )

    return config


def require_env(*names: str) -> dict[str, str]:
    """
    Return {name: value} for each env var in names.
    Raises EnvironmentError (fast fail) if any are absent or empty.
    """
    result: dict[str, str] = {}
    missing = []
    for name in names:
        val = os.environ.get(name, "")
        if not val:
            missing.append(name)
        else:
            result[name] = val
    if missing:
        raise EnvironmentError(
            f"Required environment variables not set: {', '.join(missing)}\n"
            "Check your .env file (see .env.example)."
        )
    return result
