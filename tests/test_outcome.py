import json
from datetime import date

import pytest
from pydantic import ValidationError

from emi_agent.outcome import Outcome, OutcomeRecord, append_record


def make(today, **overrides):
    data = dict(
        borrower_id="B001", verified=True, outcome="will_pay", promise_to_pay_date=None,
        payment_link_sent=False, escalate_to_human=False, notes="", language_used="en",
        callback_time=None,
    )
    data.update(overrides)
    return OutcomeRecord.model_validate(data, context={"today": today})


def test_valid_will_pay(today):
    assert make(today).outcome is Outcome.will_pay


@pytest.mark.parametrize("outcome", ["hardship", "dispute"])
def test_hardship_and_dispute_force_escalation(outcome, today):
    assert make(today, outcome=outcome, escalate_to_human=False).escalate_to_human is True


def test_promise_requires_date(today):
    with pytest.raises(ValidationError, match="promise_to_pay_date is required"):
        make(today, outcome="promise_to_pay")


def test_promise_date_today_or_later(today):
    # v10 eval S14a run 1: "aaj shaam tak" was rejected four times; a promise for today is real.
    assert make(today, outcome="promise_to_pay", promise_to_pay_date=today).promise_to_pay_date == today
    with pytest.raises(ValidationError, match="today or later"):
        make(today, outcome="promise_to_pay", promise_to_pay_date=date(2026, 11, 1))
    assert make(today, outcome="promise_to_pay",
                promise_to_pay_date=date(2026, 11, 13)).promise_to_pay_date == date(2026, 11, 13)


def test_date_only_with_promise(today):
    with pytest.raises(ValidationError, match="only allowed"):
        make(today, outcome="will_pay", promise_to_pay_date=date(2026, 11, 13))


@pytest.mark.parametrize("outcome", ["will_pay", "already_paid", "promise_to_pay"])
def test_unverified_forbids_payment_outcomes(outcome, today):
    with pytest.raises(ValidationError, match="verified"):
        make(today, verified=False, outcome=outcome, promise_to_pay_date=date(2026, 11, 13))


@pytest.mark.parametrize("outcome", ["wrong_number", "third_party", "verification_failed",
                                     "stop_requested", "callback_requested", "unresolved"])
def test_unverified_allows_other_outcomes(outcome, today):
    assert make(today, verified=False, outcome=outcome,
                callback_time="2026-11-04 (kal subah)").verified is False


@pytest.mark.parametrize("outcome", ["wrong_number", "third_party", "verification_failed"])
def test_verified_forbids_unverified_only_outcomes(outcome, today):
    with pytest.raises(ValidationError, match="verified"):
        make(today, verified=True, outcome=outcome)


def test_callback_requested_requires_callback_time(today):
    with pytest.raises(ValidationError, match="callback_time"):
        make(today, outcome="callback_requested", callback_time=None)


def test_unknown_outcome_rejected(today):
    with pytest.raises(ValidationError):
        make(today, outcome="waiver_granted")


def test_today_context_required():
    with pytest.raises(ValidationError, match="today"):
        OutcomeRecord.model_validate(dict(
            borrower_id="B001", verified=True, outcome="will_pay", promise_to_pay_date=None,
            payment_link_sent=False, escalate_to_human=False, notes="", language_used="en",
            callback_time=None,
        ))


def test_append_record_writes_one_json_line(tmp_path, today):
    path = tmp_path / "calls.jsonl"
    append_record(make(today), path)
    append_record(make(today, outcome="hardship"), path)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["escalate_to_human"] is True
