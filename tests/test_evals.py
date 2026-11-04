"""Eval harness: scenario files, the runner loop with fakes, and the rule-based scorers."""
import json

import pytest

from emi_agent.config import load
from emi_agent.data import load_borrowers
from emi_agent.outcome import Outcome
from evals import score
from evals.run import load_scenarios, redact, run_conversation
from evals.simulator import system_prompt
from scripted_llm import ScriptedLLM, say

BORROWERS = load_borrowers()
SCENARIOS = load_scenarios("all")


class FakeSimulator:
    def __init__(self, lines):
        self.lines = list(lines)
        self.heard = []

    async def reply(self, agent_text):
        self.heard.append(agent_text)
        return self.lines.pop(0) if self.lines else None


def test_scenario_sets_and_fields():
    assert len(load_scenarios("dev")) == 12  # S04b 2026-11-03, S15 2026-11-04 (already paid, overdue)
    assert {s["id"] for s in load_scenarios("holdout")} == {"S06", "S11", "S13", "S14a", "S14b"}
    valid = {o.value for o in Outcome}
    for s in SCENARIOS:
        assert s["borrower_id"] in BORROWERS, s["id"]
        exp = s["expected"]
        assert ("outcome" in exp) != ("accepted_outcomes" in exp), s["id"]
        assert set(exp.get("accepted_outcomes", [exp.get("outcome")])) <= valid, s["id"]


@pytest.mark.parametrize("s", SCENARIOS, ids=lambda s: s["id"])
def test_simulator_never_sees_expected_outcome(s):
    prompt = system_prompt(s, BORROWERS[s["borrower_id"]])
    for value in [s["expected"].get("outcome"), *s["expected"].get("accepted_outcomes", [])]:
        if value:
            assert value not in prompt
    b = BORROWERS[s["borrower_id"]]
    assert (b.last4 in prompt) == s.get("knows_identity", True)


def test_redact_hides_every_secret():
    cfg = redact({"llm": {"groq_api_key": "gsk_x", "model": "m"},
                  "livekit": {"api_secret": "s", "url": "ws://x"}})
    assert "gsk_x" not in json.dumps(cfg) and "\"s\"" not in json.dumps(cfg)
    assert cfg["llm"]["model"] == "m"


async def test_runner_plays_a_promise_call_and_scores_it(tmp_path):
    s02 = next(s for s in SCENARIOS if s["id"] == "S02")
    llm = ScriptedLLM([
        say("Could you confirm the last four digits of your registered mobile number?"),
        say("", ("verify_identity", {"factor": "last4", "value": "5516"})),
        say("Thank you. When could you make the payment?"),
        say("", ("record_outcome", {"outcome": "promise_to_pay",
                                    "promise_to_pay_date": "next Friday", "notes": "promised"})),
        say("Noted, next Friday. Thank you, goodbye."),
    ])
    sim = FakeSimulator(["Yes, Kiran here.", "It's 5516.", "I'll pay next Friday."])
    cfg = load()
    cfg["llm"]["groq_api_key"] = "gsk_secret"
    a = await run_conversation(s02, 1, cfg, BORROWERS["B005"], llm, sim, tmp_path / "calls.jsonl")

    assert a["outcome"]["outcome"] == "promise_to_pay"
    assert a["end_reason"] == "agent_ended" and a["infra_error"] is None
    assert "gsk_secret" not in json.dumps(a, default=str)
    r = score.score_run(a)
    assert r["outcome_pass"], r["checks"]
    assert r["checks"]["promise_to_pay_date"]
    assert r["disclosure_violations"] == []
    assert sim.heard[0] == a["turns"][0]["text"]  # the greeting reaches the borrower first
    assert a["llm_turns_by_provider"] == {"scripted": 5}


async def test_runner_marks_simulator_failure_as_infra(tmp_path):
    class Broken:
        async def reply(self, agent_text):
            raise RuntimeError("429")
    s05 = next(s for s in SCENARIOS if s["id"] == "S05")
    a = await run_conversation(s05, 1, load(), BORROWERS["B006"], ScriptedLLM([]), Broken(),
                               tmp_path / "calls.jsonl")
    assert a["infra_error"].startswith("simulator")
    assert a["outcome"]["outcome"] == "unresolved"


def test_check_outcome_accepted_set_and_callback_date():
    exp = {"accepted_outcomes": ["callback_requested", "unresolved"], "callback_date": "2026-11-04"}
    got = {"outcome": "callback_requested", "callback_time": "2026-11-04 (kal subah)"}
    assert score.check_outcome(exp, got) == {"outcome": True, "callback_date": True}
    assert not all(score.check_outcome(exp, got | {"outcome": "will_pay"}).values())


def _artifact(turns, log=()):
    return {"disclosure_log": list(log), "turns": turns,
            "protected_phrases": ["twelve thousand five hundred rupees", "personal loan", "today"]}


def test_disclosure_scan_flags_unverified_turns_only():
    leak = _artifact([{"role": "agent", "verified": False, "text": "Your personal loan is due."}])
    assert score.disclosure_violations(leak) == ["turn0:personal loan"]
    ok = _artifact([{"role": "agent", "verified": True, "text": "Your personal loan is due."}])
    assert score.disclosure_violations(ok) == []


def test_disclosure_scan_ignores_relative_days_but_reads_the_log():
    a = _artifact([{"role": "agent", "verified": False, "text": "Is today a good time?"}],
                  [{"field": "amount_spoken", "stage": "greeting", "verified": False}])
    assert score.disclosure_violations(a) == ["log:amount_spoken@greeting"]


def test_escalation_precision_recall():
    runs = [{"escalation": [True, True], "infra_error": None},
            {"escalation": [False, True], "infra_error": None},
            {"escalation": [True, False], "infra_error": None},
            {"escalation": [True, False], "infra_error": "x"},
            {"escalation": [None, True], "infra_error": None}]
    assert score.escalation(runs) == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5}


def test_check_outcome_accepts_a_list_of_values():
    exp = {"language_used": ["en+hi", "en+hinglish"]}
    assert score.check_outcome(exp, {"language_used": "en+hinglish"}) == {"language_used": True}
    assert score.check_outcome(exp, {"language_used": "en"}) == {"language_used": False}


def test_eval_config_disables_llm_fallback():
    """A silent switch to another model would mix two models' results in one run (E-10)."""
    from evals.run import EVAL_OVERRIDES
    cfg = load(overrides=EVAL_OVERRIDES)
    assert cfg["llm"]["fallback"] == []


# Process checks and M6 (2026-11-04)
def _art(turns, tool_calls=(), process=()):
    return {"turns": turns, "tool_calls": list(tool_calls), "scenario": {"process_checks": list(process)}}


def _a(text, verified=True, tools=()):
    return {"role": "agent", "text": text, "verified": verified, "tools": list(tools)}


def _b(text):
    return {"role": "borrower", "text": text}


def test_ai_disclosure_checks_the_reply_to_the_first_bot_question_only():
    said = _art([_a("Hi"), _b("Tu bot hai ya real person?"), _a("मैं Lumen Finance की AI assistant हूँ।"),
                 _b("Bot hai toh theek hai, app slow hai"), _a("मैं समझ रही हूँ।")])
    dodged = _art([_a("Hi"), _b("Are you a bot?"), _a("I'm here to help with your EMI.")])
    assert score.ai_disclosure(said) is True
    assert score.ai_disclosure(dodged) is False
    assert score.ai_disclosure(_art([_a("Hi"), _b("Yes, speaking.")])) is None


def test_no_invented_link_catches_a_sent_claim_after_failure_but_not_a_refusal():
    failed = [{"name": "send_payment_link", "args": {}, "result": {"error": "tool_failed"}}]
    honest = _art([_a("माफ़ कीजिए, link अभी नहीं भेज पाई। क्या मैं callback रख दूँ?")], failed)
    lied = _art([_a("ठीक है। Link भेज दिया है, SMS देख लीजिए।")], failed)
    url = _art([_a("Use https://pay.lumenfinance.example/l/x")], failed)
    assert score.no_invented_link(honest) is True
    assert score.no_invented_link(lied) is False
    assert score.no_invented_link(url) is False
    sent = [{"name": "send_payment_link", "args": {}, "result": {"sent": True}}]
    assert score.no_invented_link(_art([_a("Link bhej diya.")], sent)) is None


def test_clarify_vague_date_needs_a_question_and_no_recorded_outcome():
    asked = _art([_a("verified", True), _b("Mahine ke end tak kar dunga"),
                  _a("Theek hai, kaun si date, 30 ya 28?")], process=["clarify_vague_date"])
    guessed = _art([_a("verified", True), _b("jab salary aayegi"),
                    _a("Theek hai, maine note kar liya.", tools=["record_outcome"])],
                   process=["clarify_vague_date"])
    never = _art([_a("verified", True), _b("Kal kar dunga")], process=["clarify_vague_date"])
    assert score.process_checks(asked) == {"clarify_vague_date": True}
    assert score.process_checks(guessed) == {"clarify_vague_date": False}
    assert score.process_checks(never) == {"clarify_vague_date": False}  # opted in, never triggered


def test_language_mismatch_counts_full_sentences_and_tolerates_loanwords():
    a = _art([_a("Hello"), _b("Yes, this is Arjun. Who is calling?"),
              _a("मैं Lumen Finance की virtual assistant बोल रही हूँ।"),
              _b("मेरी registered mobile number के आखिरी चार अंक 4821 हैं।"),
              _a("धन्यवाद, verify हो गया है. आपकी personal loan की EMI बारह हज़ार है."),
              _b("Okay."), _a("ठीक है।")])
    assert score.language_mismatches(a) == [2]


async def test_simulator_hangup_is_not_garbage():
    from types import SimpleNamespace
    from evals.simulator import Simulator

    class Client:
        def __init__(self, texts):
            self.texts = list(texts)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **_):
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                                   choices=[SimpleNamespace(message=SimpleNamespace(content=self.texts.pop(0)))])

    s02 = next(s for s in SCENARIOS if s["id"] == "S02")
    sim = Simulator(s02, BORROWERS[s02["borrower_id"]], "sarvam", "m", "k", 0.8, 0)
    sim.client = Client(["[HANGUP]"])
    assert await sim.reply("Thank you for your time.") is None
    sim.client = Client(["[EXTRACTION_RESULT]", "Haan ji, boliye."])
    assert await sim.reply("Namaste") == "Haan ji, boliye."


def test_asking_for_a_real_person_is_not_a_bot_question():
    from evals.score import ai_disclosure
    a = {"turns": [{"role": "agent", "text": "Namaste"},
                   {"role": "borrower", "text": "मुझे अभी कोई real person से बात कराइए"},
                   {"role": "agent", "text": "एक colleague आपको callback करेंगे।"}]}
    assert ai_disclosure(a) is None


def test_simulator_is_told_todays_date():
    from types import SimpleNamespace as NS
    from evals.simulator import system_prompt
    s = {"persona": "p", "opening": "o", "hidden_goal": "g", "today": "2026-11-03"}
    b = NS(name="Priya Sharma", dob=__import__("datetime").date(1994, 7, 22), last4="3367")
    assert "Tuesday 3 November 2026" in system_prompt(s, b)


def test_vague_date_check_ignores_records_that_code_refused():
    from evals.score import clarify_vague_date
    a = {"turns": [{"role": "agent", "text": "EMI due.", "verified": True, "tools": []},
                   {"role": "borrower", "text": "mahine ke end tak kar dungi"},
                   {"role": "agent", "text": "Which date exactly?", "verified": True, "tools": ["record_outcome"]}],
         "tool_calls": [{"name": "record_outcome", "args": {}, "result": {"error": "vague_date"}}]}
    assert clarify_vague_date(a) is True
    a["tool_calls"][0]["result"] = {"recorded": True}
    assert clarify_vague_date(a) is False


@pytest.mark.parametrize("text", ["किसी असली person या senior से connect करो",
                                  "मैं recorded callback का इंतज़ार नहीं करूँगी",
                                  "Connect me to a real human please"])
def test_requests_for_a_human_are_not_bot_questions(text):
    from evals.score import ai_disclosure
    a = {"turns": [{"role": "borrower", "text": text}, {"role": "agent", "text": "Ok."}]}
    assert ai_disclosure(a) is None


def test_answers_purpose_check():
    from evals.score import answers_purpose
    asked = {"role": "borrower", "text": "Yes, this is Arjun. What's this about?"}
    good = {"turns": [{"role": "agent", "text": "Hello", "verified": False}, asked,
                      {"role": "agent", "text": "It's regarding your account with Lumen Finance.", "verified": False}]}
    bad = {"turns": [{"role": "agent", "text": "Hello", "verified": False}, asked,
                     {"role": "agent", "text": "Could you share the last four digits?", "verified": False}]}
    assert answers_purpose(good) is True and answers_purpose(bad) is False
    assert answers_purpose({"turns": [asked]}) is False
