"""Generic repayment policy (data/policy.yaml), the same for every account.

Short version: in the system prompt, so the common questions ("what if I pay late?") are answered
at once, with no extra model round. Detailed version: the get_policy tool, for follow-ups. Amounts
are spoken by code, like the loan facts; the model never turns a number into words itself.
Owner, 2026-11-04: on a live call B004 was told "a late payment charge of five hundred rupees has
been added" on a loan that was not due and had no charge: an invented fact (G-08).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from emi_agent.speech_format import amount_to_words

_PATH = Path(__file__).resolve().parents[2] / "data" / "policy.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    return yaml.safe_load(_PATH.read_text())


def amounts() -> dict[str, int]:
    return dict(_load()["amounts"])


def topics() -> list[str]:
    return list(_load()["detailed"])


def _spoken(text: str, lang: str, script: str) -> str:
    return text.format(**{k: amount_to_words(v, lang, script) for k, v in amounts().items()})


def short(lang: str = "en", script: str = "roman") -> str:
    """One line per topic, for the system prompt."""
    return "\n".join(f"- {_spoken(line, lang, script)}" for line in _load()["short"].values())


def detailed(topic: str, lang: str = "en", script: str = "roman") -> str | None:
    text = _load()["detailed"].get(topic)
    return _spoken(text, lang, script) if text else None
