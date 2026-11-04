"""Call state. Transitions are made by code (tools.py, brain.py), never by the model."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Borrower(BaseModel):
    borrower_id: str
    name: str
    dob: date
    last4: str
    loan_type: str
    emi_amount: int
    due_date: date
    days_overdue: int = 0
    late_fee: int = 0  # late payment charge already applied, in rupees (owner 2026-11-04; mock data)
    preferred_language: Literal["en", "hi", "hinglish"]

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


class Stage(Enum):
    GREETING = "greeting"
    VERIFYING = "verifying"
    REMINDING = "reminding"
    INTENT = "intent"
    ACTION = "action"
    CLOSING = "closing"
    ENDED = "ended"


@dataclass(frozen=True)
class LoanFacts:
    """Pre-spoken strings only. The model never receives raw numbers or dates."""
    amount_spoken: str
    due_date_spoken: str
    loan_type: str
    overdue_spoken: str | None = None
    late_fee_spoken: str | None = None  # only on an overdue loan with a charge applied

    def as_dict(self) -> dict[str, str]:
        d = {
            "amount_spoken": self.amount_spoken,
            "due_date_spoken": self.due_date_spoken,
            "loan_type": self.loan_type,
        }
        if self.overdue_spoken:
            d["overdue_spoken"] = self.overdue_spoken
        if self.late_fee_spoken:
            d["late_fee_spoken"] = self.late_fee_spoken
        return d


@dataclass(frozen=True)
class Disclosure:
    field: str
    timestamp: float
    stage: Stage
    verified: bool


@dataclass
class CallState:
    borrower_id: str
    reply_language: str
    numbers: str = "english"  # english | hindi: number words in Hindi/Hinglish replies (speech_format.number_lang)
    stage: Stage = Stage.GREETING
    verified: bool = False
    verify_attempts: int = 0
    loan_facts: LoanFacts | None = None
    disclosure_log: list[Disclosure] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    payment_link_sent: bool = False
    escalate_to_human: bool = False
    callback_time: str | None = None
    languages_used: list[str] = field(default_factory=list)
    outcome: object | None = None  # OutcomeRecord once record_outcome succeeds
    guard_hits: list[dict] = field(default_factory=list)
    speech_hits: list[dict] = field(default_factory=list)  # pre-TTS fixes and flags (speakable.py)
    tool_calls: list[dict] = field(default_factory=list)
    heard: list[str] = field(default_factory=list)  # borrower utterances, for grounding checks
    last_agent: str = ""  # what the agent said last turn, for the escalation check
    special: str | None = None  # hardship | dispute_amount | not_mine: the code-owned flow (special.py)
    special_step: int = 0  # the step the model is told to do next; moves on only once heard

    def disclose(self, facts: LoanFacts) -> None:
        now = time.time()
        for name in facts.as_dict():
            self.disclosure_log.append(Disclosure(name, now, self.stage, self.verified))
