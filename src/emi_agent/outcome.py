"""Outcome record and its code-enforced consistency rules (G-05, G-12)."""
from __future__ import annotations

from datetime import date
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ValidationInfo, model_validator


class Outcome(str, Enum):
    will_pay = "will_pay"
    already_paid = "already_paid"
    promise_to_pay = "promise_to_pay"
    hardship = "hardship"
    dispute = "dispute"
    wrong_number = "wrong_number"
    third_party = "third_party"
    callback_requested = "callback_requested"
    stop_requested = "stop_requested"
    verification_failed = "verification_failed"
    unresolved = "unresolved"


ESCALATING = {Outcome.hardship, Outcome.dispute}
NEEDS_VERIFICATION = {Outcome.will_pay, Outcome.already_paid, Outcome.promise_to_pay}
# A verified caller is the borrower, so these cannot apply.
UNVERIFIED_ONLY = {Outcome.wrong_number, Outcome.third_party, Outcome.verification_failed}


class OutcomeRecord(BaseModel):
    borrower_id: str
    verified: bool
    outcome: Outcome
    promise_to_pay_date: date | None
    payment_link_sent: bool
    escalate_to_human: bool
    notes: str
    language_used: str
    callback_time: str | None

    @model_validator(mode="after")
    def _consistent(self, info: ValidationInfo) -> "OutcomeRecord":
        today = (info.context or {}).get("today")
        if not isinstance(today, date):
            raise ValueError("validation needs context={'today': date}")
        if self.outcome in ESCALATING:
            self.escalate_to_human = True
        if not self.verified and self.outcome in NEEDS_VERIFICATION:
            raise ValueError(f"outcome {self.outcome.value} requires a verified borrower")
        if self.verified and self.outcome in UNVERIFIED_ONLY:
            raise ValueError(f"outcome {self.outcome.value} is not possible once verified")
        if self.outcome is Outcome.callback_requested and not self.callback_time:
            raise ValueError("callback_requested needs callback_time: schedule the callback first")
        if self.outcome is Outcome.promise_to_pay:
            if self.promise_to_pay_date is None:
                raise ValueError("promise_to_pay_date is required for promise_to_pay")
            if self.promise_to_pay_date < today:  # today is a real promise ("aaj shaam tak")
                raise ValueError("promise_to_pay_date must be today or later")
        elif self.promise_to_pay_date is not None:
            raise ValueError("promise_to_pay_date is only allowed with promise_to_pay")
        return self


def append_record(record: OutcomeRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
