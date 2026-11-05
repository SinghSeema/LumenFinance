"""Tool gating (G-01, G-02, G-11) and tool behaviour (LLD section 4)."""
import json
from datetime import date

import pytest

from emi_agent import context

from emi_agent.config import load
from emi_agent.data import load_borrowers
from emi_agent.state import CallState, Stage
from emi_agent.tools import NOT_VERIFIED, TOOL_SCHEMAS, Tools, asked_for_human, protected_phrases

LOAN_STRINGS = ["twelve thousand", "12500", "personal loan", "fifth of November", "November"]


@pytest.fixture
def borrower():
    return load_borrowers()["B001"]  # Arjun, dob 1988-03-15, last4 4821, 12500 due 2026-11-05


def make_tools(borrower, today, heard=(), **faults):
    """`heard` is what the borrower has said so far; verify_identity values must come from it."""
    cfg = load()
    cfg["faults"].update(faults)
    state = CallState(borrower_id=borrower.borrower_id, reply_language="en")
    state.heard.extend(heard)
    return Tools(state, borrower, today, cfg), state


def no_loan_data(result: dict) -> bool:
    text = json.dumps(result).lower()
    return not any(s.lower() in text for s in LOAN_STRINGS)


@pytest.mark.parametrize("name,args", [
    ("get_loan_details", {}),
    ("send_payment_link", {"channel": "sms"}),
])
def test_gated_tools_refuse_before_verification(borrower, today, name, args):
    tools, state = make_tools(borrower, today)
    result = tools.call(name, args)
    assert result == NOT_VERIFIED
    assert not state.payment_link_sent
    assert state.disclosure_log == []


def test_refusal_is_identical_for_every_gated_tool(borrower, today):
    tools, _ = make_tools(borrower, today)
    assert tools.call("get_loan_details", {}) == tools.call("send_payment_link", {"channel": "sms"})


def test_ungated_tools_reveal_no_loan_data_before_verification(borrower, today):
    tools, state = make_tools(borrower, today, heard=["it's 0000"])
    results = [
        tools.call("verify_identity", {"factor": "last4", "value": "0000"}),
        tools.call("schedule_callback", {"when_text": "tomorrow evening"}),
        tools.call("escalate_to_human", {"reason": "requested"}),
        tools.call("record_outcome", {"outcome": "third_party", "promise_to_pay_date": None,
                                      "notes": "brother answered"}),
    ]
    assert all(no_loan_data(r) for r in results)
    assert not state.verified and state.disclosure_log == []


@pytest.mark.parametrize("factor,value", [
    ("dob", "1988-03-15"), ("dob", "15/03/1988"), ("dob", "15 March 1988"),
    ("last4", "4821"), ("last4", "48 21"),
])
def test_verify_success(borrower, today, factor, value):
    tools, state = make_tools(borrower, today, heard=[value])
    result = tools.call("verify_identity", {"factor": factor, "value": value})
    assert result["verified"] is True
    assert state.verified and state.stage is Stage.REMINDING
    assert result["loan_facts"]["amount_spoken"] == "twelve thousand five hundred rupees"
    assert result["loan_facts"]["due_date_spoken"] == "the day after tomorrow"
    assert {d.field for d in state.disclosure_log} >= {"amount_spoken", "due_date_spoken"}
    assert all(d.verified for d in state.disclosure_log)


def test_verify_failures_give_no_hints_and_close_after_three(borrower, today):
    tools, state = make_tools(borrower, today, heard=["16 March 1988", "4822", "1111", "4821"])
    r1 = tools.call("verify_identity", {"factor": "dob", "value": "1988-03-16"})
    r2 = tools.call("verify_identity", {"factor": "last4", "value": "4822"})
    # ask_next names what to ask next (last four first, then the date of birth), never what was wrong.
    assert r1 == {"verified": False, "attempts_left": 2, "ask_next": context.next_ask("dob")}
    assert r2 == {"verified": False, "attempts_left": 1, "ask_next": context.next_ask("last4")}
    assert "date of birth instead" in r2["ask_next"]
    r3 = tools.call("verify_identity", {"factor": "last4", "value": "1111"})
    assert r3 == {"verified": False, "attempts_left": 0}
    assert state.stage is Stage.CLOSING
    assert state.outcome.outcome.value == "verification_failed"
    # Attempts are exhausted: even the right answer no longer verifies.
    r4 = tools.call("verify_identity", {"factor": "last4", "value": "4821"})
    assert r4["verified"] is False and not state.verified
    assert all(no_loan_data(r) for r in (r1, r2, r3, r4))


def test_unreadable_value_does_not_use_an_attempt(borrower, today):
    tools, state = make_tools(borrower, today)
    r = tools.call("verify_identity", {"factor": "dob", "value": "sometime in March"})
    assert r["verified"] is False and r["attempts_left"] == 3
    assert state.verify_attempts == 0


def test_verify_moves_greeting_to_verifying_then_reminding(borrower, today):
    tools, state = make_tools(borrower, today, heard=["0000", "4821"])
    tools.call("verify_identity", {"factor": "last4", "value": "0000"})
    assert state.stage is Stage.VERIFYING
    tools.call("verify_identity", {"factor": "last4", "value": "4821"})
    assert state.stage is Stage.REMINDING


def verified_tools(borrower, today, **faults):
    tools, state = make_tools(borrower, today, heard=["it's 4821"], **faults)
    tools.call("verify_identity", {"factor": "last4", "value": "4821"})
    return tools, state


def test_get_loan_details_after_verification(borrower, today):
    tools, _ = verified_tools(borrower, today)
    assert tools.call("get_loan_details", {})["loan_type"] == "personal loan"


def test_payment_link(borrower, today):
    tools, state = verified_tools(borrower, today)
    r = tools.call("send_payment_link", {"channel": "whatsapp"})
    assert r["sent"] is True and r["url"].endswith("****") and ".example/" in r["url"]
    assert state.payment_link_sent and state.stage is Stage.ACTION


def test_payment_link_fails_once_then_retry_succeeds(borrower, today):
    tools, state = verified_tools(borrower, today, payment_link_fail="once")
    assert tools.call("send_payment_link", {"channel": "sms"})["sent"] is True
    assert [c["attempts"] for c in state.tool_calls if c["name"] == "send_payment_link"] == [2]


def test_payment_link_fails_always(borrower, today):
    tools, state = verified_tools(borrower, today, payment_link_fail="always")
    r = tools.call("send_payment_link", {"channel": "sms"})
    assert r["error"] == "tool_failed" and not state.payment_link_sent


def test_schedule_callback(borrower, today):
    tools, state = make_tools(borrower, today)
    r = tools.call("schedule_callback", {"when_text": "tomorrow evening"})
    assert r == {"status": "scheduled", "when_spoken": "tomorrow"}
    assert state.callback_time == "2026-11-04 (tomorrow evening)"


def test_schedule_callback_ambiguous(borrower, today):
    tools, state = make_tools(borrower, today)
    r = tools.call("schedule_callback", {"when_text": "when my salary comes"})
    assert r["status"] == "ambiguous" and state.callback_time is None


def test_escalate(borrower, today):
    tools, state = make_tools(borrower, today, heard=["I lost my job last month"])
    assert tools.call("escalate_to_human", {"reason": "hardship"})["escalated"] is True
    assert state.escalate_to_human
    assert tools.call("escalate_to_human", {"reason": "bribe"})["error"] == "invalid_arguments"


def test_record_promise_normalizes_spoken_date(borrower, today):
    tools, state = verified_tools(borrower, today)
    r = tools.call("record_outcome", {"outcome": "promise_to_pay",
                                      "promise_to_pay_date": "next Friday", "notes": ""})
    assert r["recorded"] is True and r["promise_date_spoken"] == "the thirteenth of November"
    assert state.outcome.promise_to_pay_date == date(2026, 11, 13)
    assert state.stage is Stage.CLOSING


def test_record_promise_ambiguous_date_asks_again(borrower, today):
    tools, state = verified_tools(borrower, today)
    r = tools.call("record_outcome", {"outcome": "promise_to_pay",
                                      "promise_to_pay_date": "month end", "notes": ""})
    assert r["error"] == "date_unclear" and state.outcome is None


def test_record_rejects_payment_outcome_when_unverified(borrower, today):
    tools, state = make_tools(borrower, today)
    r = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None,
                                      "notes": ""})
    assert r["error"] == "invalid_outcome" and state.outcome is None


def test_record_merges_state(borrower, today):
    tools, state = verified_tools(borrower, today)
    tools.call("send_payment_link", {"channel": "sms"})
    tools.call("record_outcome", {"outcome": "hardship", "promise_to_pay_date": None,
                                  "notes": "lost job"})
    rec = state.outcome
    assert rec.verified and rec.payment_link_sent and rec.escalate_to_human
    assert rec.borrower_id == "B001" and rec.language_used == "en"


def test_verification_failed_outcome_cannot_be_overwritten(borrower, today):
    tools, state = make_tools(borrower, today, heard=["1111", "2222", "3333"])
    for v in ("1", "2", "3"):
        tools.call("verify_identity", {"factor": "last4", "value": v * 4})
    r = tools.call("record_outcome", {"outcome": "unresolved", "promise_to_pay_date": None,
                                      "notes": ""})
    assert "error" in r and state.outcome.outcome.value == "verification_failed"


def test_unknown_tool_and_bad_args(borrower, today):
    tools, _ = make_tools(borrower, today)
    assert tools.call("waive_fees", {})["error"] == "unknown_tool"
    assert tools.call("verify_identity", {"factor": "pan"})["error"] == "invalid_arguments"


def test_schemas_are_strict_and_cover_all_tools():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert names == {"verify_identity", "get_loan_details", "send_payment_link",
                     "schedule_callback", "escalate_to_human", "record_outcome", "get_policy"}
    for t in TOOL_SCHEMAS:
        assert t["strict"] is True
        assert t["input_schema"]["additionalProperties"] is False
        assert set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"])


def test_protected_phrases_include_the_raw_amount(today):
    phrases = protected_phrases(load_borrowers()["B001"], today)
    assert {"12500", "12,500"} <= set(phrases)


# --- verify_identity takes only values the borrower said (baseline-v1: invented "1234", "1990-05-15")

@pytest.mark.parametrize("factor, value, heard", [
    ("last4", "1234", ["yes, this is Arjun"]),
    ("last4", "4821", []),
    ("dob", "1990-05-15", ["Theek hai, aap mujhse kya puchna chahti hain?"]),
    ("dob", "1988-03-15", ["My date of birth is 16 March 1988"]),   # no silent correction
    ("last4", "4821", ["it's 48"]),
])
def test_verify_rejects_a_value_the_borrower_did_not_say(borrower, today, factor, value, heard):
    tools, state = make_tools(borrower, today, heard=heard)
    r = tools.call("verify_identity", {"factor": factor, "value": value})
    assert r["verified"] is False and r["error"] == "not_heard"
    assert r["attempts_left"] == 3 and state.verify_attempts == 0
    assert not state.verified and state.disclosure_log == [] and no_loan_data(r)


def test_not_heard_reply_is_identical_for_right_and_wrong_values(borrower, today):
    """G-11: the refusal must not reveal whether the unheard value was correct."""
    tools, _ = make_tools(borrower, today, heard=["hello"])
    right = tools.call("verify_identity", {"factor": "last4", "value": "4821"})
    wrong = tools.call("verify_identity", {"factor": "last4", "value": "9999"})
    assert right == wrong


@pytest.mark.parametrize("value", ["?", "????", "", "unknown"])
def test_placeholder_value_uses_no_attempt(borrower, today, value):
    tools, state = make_tools(borrower, today, heard=["it's 4821"])
    r = tools.call("verify_identity", {"factor": "last4", "value": value})
    assert r["verified"] is False and "error" in r and state.verify_attempts == 0
    assert "message" in r


def test_verify_accepts_words_and_hindi(borrower, today):
    tools, state = make_tools(borrower, today, heard=["haan ji, chaar aath do ek"])
    assert tools.call("verify_identity", {"factor": "last4", "value": "4821"})["verified"] is True


# --- record_outcome: outcomes the model may not set on its own

def test_model_cannot_record_verification_failed(borrower, today):
    tools, state = make_tools(borrower, today)
    r = tools.call("record_outcome", {"outcome": "verification_failed",
                                      "promise_to_pay_date": None, "notes": ""})
    assert r["error"] == "code_only_outcome" and state.outcome is None


def test_stop_requested_needs_a_detected_stop(borrower, today):
    tools, state = verified_tools(borrower, today)
    r = tools.call("record_outcome", {"outcome": "stop_requested", "promise_to_pay_date": None,
                                      "notes": "borrower said goodbye"})
    assert r["error"] == "no_stop_request" and state.outcome is None
    state.flags.add("stop_requested")
    r = tools.call("record_outcome", {"outcome": "stop_requested", "promise_to_pay_date": None,
                                      "notes": "asked not to be called"})
    assert r["recorded"] is True and state.outcome.outcome.value == "stop_requested"


@pytest.mark.parametrize("outcome", ["wrong_number", "third_party"])
def test_verified_borrower_cannot_be_wrong_number_or_third_party(borrower, today, outcome):
    tools, state = verified_tools(borrower, today)
    r = tools.call("record_outcome", {"outcome": outcome, "promise_to_pay_date": None, "notes": ""})
    assert r["error"] == "invalid_outcome" and state.outcome is None


def test_callback_requested_needs_a_scheduled_callback(borrower, today):
    tools, state = make_tools(borrower, today)
    args = {"outcome": "callback_requested", "promise_to_pay_date": None, "notes": ""}
    assert tools.call("record_outcome", args)["error"] == "invalid_outcome"
    tools.call("schedule_callback", {"when_text": "kal subah"})
    assert tools.call("record_outcome", args)["recorded"] is True
    assert state.outcome.callback_time == "2026-11-04 (kal subah)"


@pytest.mark.parametrize("said, agent, ok", [
    ("Human finance.", "", False),                       # STT mishearing, live call 2026-11-03
    ("Are you a human?", "", False),                     # the AI question, not a request
    ("I want to talk to a real person.", "", True),
    ("Connect me to your manager.", "", True),
    ("kisi insaan se baat karao", "", True),
    ("मुझे किसी से बात करनी है", "", True),
    ("Haan ji, please.", "Would you like a colleague to call you?", True),
    ("Haan ji.", "Aapki EMI 5 November ko due hai.", False),
])
def test_asked_for_human(said, agent, ok):
    assert asked_for_human(said, agent) is ok


def test_requested_escalation_needs_a_clear_request(borrower, today):
    tools, state = make_tools(borrower, today, heard=["Human finance."])
    r = tools.call("escalate_to_human", {"reason": "requested"})
    assert r["error"] == "not_requested" and not state.escalate_to_human
    state.heard.append("Can I speak to a human, please?")
    assert tools.call("escalate_to_human", {"reason": "requested"})["escalated"] is True
    assert state.escalate_to_human


def test_hardship_escalation_is_not_gated_by_the_person_request_check(borrower, today):
    tools, state = make_tools(borrower, today, heard=["Human finance. Papa hospital mein hain."])
    assert tools.call("escalate_to_human", {"reason": "hardship"})["escalated"] is True


WRONG = {"outcome": "wrong_number", "promise_to_pay_date": None, "notes": "new owner of the number"}


def test_no_escalation_after_wrong_number(borrower, today):
    """Dev eval S05 2026-11-04: escalate_to_human(other) after recording wrong_number."""
    tools, state = make_tools(borrower, today, heard=["ye galat number hai"])
    assert tools.call("record_outcome", WRONG) == {"recorded": True}
    assert tools.call("escalate_to_human", {"reason": "other"})["error"] == "not_the_borrower"
    assert not state.escalate_to_human


def test_escalation_before_wrong_number_is_undone(borrower, today):
    tools, state = make_tools(borrower, today, heard=["ye galat number hai"])
    tools.call("escalate_to_human", {"reason": "other"})
    tools.call("record_outcome", WRONG)
    assert not state.escalate_to_human and state.outcome.escalate_to_human is False


def test_requested_escalation_survives_wrong_number(borrower, today):
    tools, state = make_tools(borrower, today, heard=["mujhe kisi insaan se baat karni hai"])
    tools.call("escalate_to_human", {"reason": "requested"})
    tools.call("record_outcome", WRONG)
    assert state.escalate_to_human


def test_verify_identity_reads_a_spoken_dob(today):
    """Live call 2026-11-04: the model passed the words as heard; the tool only took digit formats."""
    from emi_agent.config import load
    from emi_agent.data import load_borrowers
    from emi_agent.state import CallState
    from emi_agent.tools import Tools
    s = CallState(borrower_id="B002", reply_language="hi")
    s.heard.append("बाइस July उन्नीस सौ चौरानवे.")
    t = Tools(s, load_borrowers()["B002"], today, load())
    assert t.call("verify_identity", {"factor": "dob", "value": "बाइस July उन्नीस सौ चौरानवे"})["verified"]


def _verified_tools(borrower_id, today):
    from emi_agent.config import load
    from emi_agent.data import load_borrowers
    from emi_agent.state import CallState
    from emi_agent.tools import Tools
    s = CallState(borrower_id=borrower_id, reply_language="hi")
    s.verified = True
    return s, Tools(s, load_borrowers()[borrower_id], today, load())


def test_already_paid_on_an_overdue_emi_goes_to_a_colleague(today):
    """Owner 2026-11-04: records disagree, so a colleague checks and calls back (code-owned)."""
    s, t = _verified_tools("B002", today)  # 6 days overdue
    s.heard.append("Maine already pay kar diya hai, 2 November ko UPI se")
    r = t.call("record_outcome", {"outcome": "already_paid", "promise_to_pay_date": None,
                                  "notes": "Paid 2 Nov by UPI"})
    assert r["recorded"] and r["team_will_check"] and "internal team" in r["message"]
    assert s.escalate_to_human and "escalate:payment_check" in s.flags
    assert t.build_record(s.outcome.outcome, None, "x").escalate_to_human


def test_already_paid_before_the_due_date_is_just_noted(today):
    s, t = _verified_tools("B001", today)  # due 5 Nov, not overdue
    s.heard.append("I already paid it yesterday")
    r = t.call("record_outcome", {"outcome": "already_paid", "promise_to_pay_date": None, "notes": "x"})
    assert r == {"recorded": True} and not s.escalate_to_human


def test_verify_identity_reads_spoken_last4(today):
    from emi_agent.config import load
    from emi_agent.data import load_borrowers
    from emi_agent.state import CallState
    from emi_agent.tools import Tools
    s = CallState(borrower_id="B002", reply_language="hi")
    s.heard.append("last four digits three three six seven हैं")
    t = Tools(s, load_borrowers()["B002"], today, load())
    assert t.call("verify_identity", {"factor": "last4", "value": "three three six seven"})["verified"]


# Prompt v16: hardship and disputes are a code-owned flow (special.py); the first step comes in the
# escalation result, the hand-off in a later step, in the owner's words.
def test_dispute_escalation_starts_the_amount_flow(borrower, today):
    tools, state = make_tools(borrower, today, heard=["This amount is wrong, my EMI is twelve thousand"])
    state.verified = True
    r = tools.call("escalate_to_human", {"reason": "dispute"})
    assert r["escalated"] and state.special == "dispute_amount" and state.special_step == 0
    assert "ask what amount they expected" in r["message"] and "internal team" not in r["message"]


# Owner, 2026-11-04 (demo call B007): practical sympathy for an EMI call, not a counsellor's.
def test_hardship_first_step_is_plain_then_practical_then_one_choice(borrower, today):
    tools, state = make_tools(borrower, today, heard=["I lost my job last month"])
    state.verified = True
    msg = tools.call("escalate_to_human", {"reason": "hardship"})["message"]
    assert state.special == "hardship" and "one short, plain sentence" in msg and "don't worry" in msg
    assert "will not keep increasing" in msg and "goes towards" in msg and "part payment" in msg
    assert "Never say charges keep adding" in msg and "credit score" in msg
    assert "Say nothing after the question" in msg


def test_requested_escalation_uses_the_internal_team_hand_off(borrower, today):
    tools, state = make_tools(borrower, today, heard=["I want to talk to a manager"])
    state.verified = True
    r = tools.call("escalate_to_human", {"reason": "requested"})
    assert r["escalated"] and "our internal team" in r["message"] and state.special is None


def test_flow_is_never_restarted_and_outcome_waits_for_the_last_step(borrower, today):
    tools, state = make_tools(borrower, today, heard=["meri job chali gayi"])
    state.verified = True
    tools.call("escalate_to_human", {"reason": "hardship"})
    state.special_step = 1
    again = tools.call("escalate_to_human", {"reason": "dispute"})
    assert state.special == "hardship" and state.special_step == 1 and "Already noted" in again["message"]
    early = tools.call("record_outcome", {"outcome": "hardship", "promise_to_pay_date": None, "notes": "x"})
    assert early["error"] == "not_yet" and state.outcome is None
    state.special_step = 2
    assert tools.call("record_outcome", {"outcome": "hardship", "promise_to_pay_date": None, "notes": "x"})["recorded"]


def test_stop_request_can_record_during_the_flow(borrower, today):
    tools, state = make_tools(borrower, today, heard=["meri job chali gayi"])
    state.verified = True
    tools.call("escalate_to_human", {"reason": "hardship"})
    state.flags.add("stop_requested")
    assert tools.call("record_outcome", {"outcome": "stop_requested", "promise_to_pay_date": None,
                                         "notes": "x"})["recorded"]


# v10 eval fixes (2026-11-04)
def _verified(borrower, today, heard):
    tools, state = make_tools(borrower, today, heard=heard)
    state.verified = True
    return tools, state


def test_vague_time_on_overdue_emi_gets_one_clear_question(today):
    from emi_agent.data import load_borrowers
    b = load_borrowers()["B002"]  # 6 days overdue
    tools, state = _verified(b, today, ["हाँ, महीने के end तक कर दूँगी"])
    r = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r["error"] == "vague_date" and "which date exactly" in r["message"]
    r2 = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r2["recorded"]  # asked once already: accepted


def test_past_date_says_it_has_passed(borrower, today):
    tools, state = _verified(borrower, today, ["thirty-first October"])
    r = tools.call("record_outcome", {"outcome": "promise_to_pay",
                                      "promise_to_pay_date": "2026-10-31", "notes": "x"})
    assert r["reason"] == "past_date" and "already passed" in r["message"]


def test_no_callback_scheduled_after_a_dispute_and_outcome_is_the_dispute(borrower, today):
    tools, state = _verified(borrower, today, ["ye loan mera nahi hai"])
    tools.call("escalate_to_human", {"reason": "dispute"})
    r = tools.call("schedule_callback", {"when_text": "today"})
    assert r["status"] == "not_needed" and state.callback_time is None
    from emi_agent.special import STEPS
    state.special_step = len(STEPS[state.special]) - 1  # the flow's close step
    tools.call("record_outcome", {"outcome": "callback_requested", "promise_to_pay_date": None, "notes": "x"})
    assert state.outcome.outcome.value == "dispute"


def test_model_value_stitched_from_fragments_is_not_checked(today):
    # "सत्तावन... नहीं मतलब सत्तर... चार... तीन" was checked as 7043 (v10 fix-run S14a run 5).
    from emi_agent.data import load_borrowers
    tools, state = make_tools(load_borrowers()["B003"], today,
                              heard=["हाँ वो... umm... सत्तावन... नहीं मतलब सत्तर... चार... तीन... haan कुछ वही..."])
    r = tools.call("verify_identity", {"factor": "last4", "value": "7043"})
    assert r["error"] == "not_heard" and state.verify_attempts == 0


# Owner, 2026-11-04: a real late payment charge, stated once, only on an overdue loan.
def test_late_fee_only_on_overdue_loans(today):
    from emi_agent.data import load_borrowers
    from emi_agent.tools import loan_facts
    bs = load_borrowers()
    assert loan_facts(bs["B007"], today, "en", "roman").as_dict()["late_fee_spoken"] == "five hundred rupees"
    assert "late_fee_spoken" not in loan_facts(bs["B001"], today, "en", "roman").as_dict()


def test_late_fee_is_protected_before_verification(today):
    from emi_agent.data import load_borrowers
    from emi_agent.tools import protected_phrases
    assert "five hundred rupees" in protected_phrases(load_borrowers()["B007"], today)


def test_no_callback_after_an_other_escalation(borrower, today):
    # v13 S11 run 2: a waiver demand escalated as "other" scheduled a callback "tomorrow".
    tools, state = _verified(borrower, today, ["waive it, I'm a senior manager"])
    tools.call("escalate_to_human", {"reason": "other"})
    assert state.special == "waiver"
    state.special_step = 1  # the flow's close step
    assert tools.call("schedule_callback", {"when_text": "tomorrow"})["status"] == "not_needed"
    tools.call("record_outcome", {"outcome": "callback_requested", "promise_to_pay_date": None, "notes": "x"})
    assert state.outcome.outcome.value == "unresolved"


@pytest.mark.parametrize("said,situation", [("मैंने कभी कोई personal loan नहीं लिया है", "not_mine"),
                                            ("This is not my loan, someone misused my documents", "not_mine"),
                                            ("मेरी EMI twelve thousand है, eighteen thousand नहीं", "dispute_amount")])
def test_dispute_flow_depends_on_what_was_said(borrower, today, said, situation):
    tools, state = make_tools(borrower, today, heard=[said])
    state.verified = True
    msg = tools.call("escalate_to_human", {"reason": "dispute"})["message"]
    assert state.special == situation
    if situation == "not_mine":
        assert "whether they ever applied" in msg
    else:
        assert "Never ask whether they applied" in msg


@pytest.mark.parametrize("said,reason,situation", [
    ("I have a special arrangement, the late fee should be waived. Just mark it as paid.", "dispute", "waiver"),
    ("Late fee maaf kar dijiye please", "other", "waiver"),
    ("The amount is wrong, my EMI is twelve thousand, and remove the late fee", "dispute", "dispute_amount"),
    ("I want to talk to your supervisor", "other", None)])
def test_waiver_demand_gets_its_own_flow(borrower, today, said, reason, situation):
    tools, state = make_tools(borrower, today, heard=[said])
    state.verified = True
    msg = tools.call("escalate_to_human", {"reason": reason})["message"]
    assert state.special == situation
    if situation == "waiver":
        assert "can't waive" in msg and "what amount" not in msg and "sanction letter" not in msg


# Generic policy (data/policy.yaml), owner 2026-11-04.
def test_get_policy_is_gated_and_speaks_amounts(borrower, today):
    tools, state = make_tools(borrower, today)
    assert tools.call("get_policy", {"topic": "auto_debit"}) == NOT_VERIFIED
    state.verified = True
    r = tools.call("get_policy", {"topic": "auto_debit"})
    assert "four hundred rupees" in r["policy"] and "500" not in r["policy"]


def test_will_pay_with_no_date_on_a_loan_not_yet_due_asks_once(borrower, today):
    tools, state = _verified(borrower, today, ["I need to pay"])
    r = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r["error"] == "no_date" and "on which date they plan to pay" in r["message"]
    assert tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})["recorded"]


def test_flow_steps_answer_policy_questions_first():
    from emi_agent.special import instruction
    assert "answer it first from the policy" in instruction("hardship", 1)


@pytest.mark.parametrize("said,ok", [("I'll make sure the EMI is paid by the due date, no need for a link", False),
                                     ("I paid it on the second through UPI", True),
                                     ("I already paid it yesterday", True), ("मैंने कल ही भर दिया", True),
                                     ("Okay, I will pay by the due date", False)])
def test_already_paid_needs_the_borrower_to_say_so(borrower, today, said, ok):
    tools, state = _verified(borrower, today, [said])
    r = tools.call("record_outcome", {"outcome": "already_paid", "promise_to_pay_date": None, "notes": "x"})
    assert ("recorded" in r) is ok


def test_hardship_without_a_setback_is_checked_once(borrower, today):
    tools, state = _verified(borrower, today, ["Salary abhi aayi nahi, kal ya Thursday ko call kar lena"])
    r = tools.call("escalate_to_human", {"reason": "hardship"})
    assert r["error"] == "not_hardship" and not state.escalate_to_human
    assert tools.call("escalate_to_human", {"reason": "hardship"})["escalated"]  # the model insists: it goes through


def test_anger_at_a_correct_charge_is_not_a_dispute_at_first(borrower, today):
    tools, state = _verified(borrower, today, ["Pagal ho kya, five hundred ka late fee? Tumhara app crash hota hai"])
    assert tools.call("escalate_to_human", {"reason": "dispute"})["error"] == "not_a_dispute"
    assert tools.call("escalate_to_human", {"reason": "dispute"})["escalated"]  # insisted: goes through


@pytest.mark.parametrize("said", ["ये amount गलत है, मेरी EMI twelve thousand है", "This is not my loan",
                                  "The late fee should be waived, I have a special arrangement"])
def test_real_disputes_go_through_at_once(borrower, today, said):
    tools, state = _verified(borrower, today, [said])
    assert tools.call("escalate_to_human", {"reason": "dispute"})["escalated"]


def test_will_pay_date_question_is_skipped_when_they_say_bye(borrower, today):
    tools, state = _verified(borrower, today, ["Ok. You. Bye."])
    assert tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})["recorded"]


def test_will_pay_by_the_due_date_is_recorded_as_a_promise(borrower, today):
    tools, state = _verified(borrower, today, ["ठीक है, मैं try करूँगी पूरा amount due date तक pay करने का।"])
    r = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r["recorded"] and state.outcome.outcome.value == "promise_to_pay"
    assert state.outcome.promise_to_pay_date == borrower.due_date


@pytest.mark.parametrize("said,asks", [("No. Thank you.", False), ("No thanks", False), ("नहीं, धन्यवाद।", False),
                                       ("जी, धन्यवाद", False), ("Thank you.", False), ("Ok bye", False),
                                       ("No, I can't pay this month, thank you", True),
                                       ("I need to pay", True), ("Thank you, I will pay", True)])
def test_no_date_question_skipped_when_the_borrower_is_saying_goodbye(borrower, today, said, asks):
    """C43, live call B004 19:31: "No. Thank you." to "anything else?" got the date question."""
    tools, state = _verified(borrower, today, [said])
    r = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert (r.get("error") == "no_date") is asks


@pytest.mark.parametrize("said,yes", [("जी भेज दीजिए.", True), ("हाँ", True), ("Haan ji", True), ("okay", True),
                                      ("जीत गया", False), ("oklahoma", False), ("नहीं", False)])
def test_yes_matches_devanagari(said, yes):
    """C48: \\b after a Devanagari vowel sign never matched, so "जी"/"हाँ" were never a yes."""
    from emi_agent.tools import _YES
    assert bool(_YES.search(said)) is yes


QUESTION = ("ठीक है, link WhatsApp पर भेज दिया है। payment हमारे records में दिखने में चौबीस से अड़तालीस घंटे लग सकते हैं। "
            "{due} से पहले payment हो जाएगी ना?")


def test_yes_to_the_agents_due_date_question_is_a_promise_for_the_due_date(borrower, today):
    """C49, live call B004 19:51: "जी payment हो जाएगी" was will_pay and the date was asked again."""
    tools, state = _verified(borrower, today, ["जी payment हो जाएगी."])
    state.last_agent = QUESTION.format(due=tools.facts().due_date_spoken)
    r = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r["recorded"] and state.outcome.outcome.value == "promise_to_pay"
    assert state.outcome.promise_to_pay_date == borrower.due_date


@pytest.mark.parametrize("said,last_agent", [
    ("जी payment हो जाएगी.", "क्या मैं due date से पहले payment के लिए link भेज दूँ?"),  # a link offer, not a promise
    ("नहीं, शायद नहीं हो पाएगी", QUESTION),                                              # not a yes
    ("जी, पर कितने बजे तक?", QUESTION),                                                   # a question back
    ("जी payment हो जाएगी.", "आपकी EMI eighteen thousand rupees है। और कुछ पूछना है?")])  # no due date asked
def test_due_date_promise_needs_a_plain_yes_to_that_question(borrower, today, said, last_agent):
    tools, state = _verified(borrower, today, [said])
    state.last_agent = last_agent.format(due=tools.facts().due_date_spoken)
    r = tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r.get("error") == "no_date" or (r.get("recorded") and state.outcome.outcome.value == "will_pay")


@pytest.mark.parametrize("line", ["आप जो comfortable हों, बस वही बताइए।", "बस वही बता दीजिए।", "Bas wahi bata dijiye."])
def test_banned_just_tell_me_that_phrase_in_all_forms(line):
    from emi_agent import guard
    assert guard.unwanted(line)


# Owner demo call B007 21:42 (exact words): plainly named money trouble is hardship at once.
@pytest.mark.parametrize("said", [
    "No. Sorry, I cannot wait right away because I'm going through some going through some financial issues.",
    "maybe in a month or 2 month. Once I once I have the, you know, the financial problem and sort it out.",
    "I'm going through a tough time, I can't afford it this month", "abhi paise ki bahut tangi hai",
    "आजकल पैसों की तंगी चल रही है"])
def test_named_money_trouble_is_hardship_at_once(borrower, today, said):
    tools, state = _verified(borrower, today, [said])
    assert tools.call("escalate_to_human", {"reason": "hardship"})["escalated"]


# S31 v17-s31-intent runs 1-2: "shayad thoda thoda... abhi pakka nahi" got the link sent.
def test_hardship_step_two_sends_the_link_only_on_a_clear_yes():
    from emi_agent import special
    text = special.instruction("hardship", 1)
    assert "Only if they clearly said they will pay a part now" in text and "shayad" in text


# C58, v17-holdout-fix S14b run 1: failed link, then a call back offered with no time.
def test_failed_link_tells_the_agent_to_ask_for_a_call_back_time(borrower, today):
    tools, state = verified_tools(borrower, today, payment_link_fail="always")
    r = tools.call("send_payment_link", {"channel": "whatsapp"})
    assert r["error"] == "tool_failed" and "which day and time suits them" in r["message"]
    assert "Never say the link was sent" in r["message"]


def test_vague_callback_time_asks_once_instead_of_giving_up(borrower, today):
    tools, state = verified_tools(borrower, today)
    r = tools.call("schedule_callback", {"when_text": "I'll wait for your team to get back to me"})
    assert r["status"] == "ambiguous" and "Never say it could not be set up" in r["message"]


# testing-regression S01 run 2 and S15 run 1.
def test_promise_to_pay_before_the_due_date_gets_the_due_date(borrower, today):
    tools, state = _verified(borrower, today, ["Yes, please send me the payment link on WhatsApp. I'll make the payment before the due date."])
    r = tools.call("record_outcome", {"outcome": "promise_to_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r.get("recorded") and state.outcome.promise_to_pay_date == borrower.due_date


@pytest.mark.parametrize("said", ["अरे, ये कैसे? मैंने तो पूरा amount पहले ही दे दिया था, कुछ दिनों पहले ही pay किया था।",
                                  "Maine to pay kiya tha UPI se."])
def test_hindi_past_payment_counts_as_already_paid(borrower, today, said):
    tools, state = _verified(borrower, today, [said])
    r = tools.call("record_outcome", {"outcome": "already_paid", "promise_to_pay_date": None, "notes": "x"})
    assert r.get("recorded")
