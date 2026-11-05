"""Brain: context assembly (G-01, G-02, G-10), disclosure log, stop, language, guard wiring."""
import asyncio
import json
from datetime import date

import pytest

from emi_agent import context
from emi_agent.brain import Brain, is_stop_request
from emi_agent.config import load
from emi_agent.data import load_borrowers
from emi_agent.outcome import Outcome
from emi_agent.speech_format import amount_to_words, date_to_words
from emi_agent.state import Stage
from emi_agent.tools import NOT_VERIFIED
from scripted_llm import ScriptedLLM, say

BORROWERS = load_borrowers()
VERIFY_OK = ("verify_identity", {"factor": "last4", "value": "4821"})


def secrets(b, today):
    """Every way B's loan or verification data could appear in model input."""
    out = {str(b.emi_amount), b.loan_type, b.due_date.isoformat(), b.dob.isoformat(), b.last4,
           amount_to_words(b.emi_amount, "en"), amount_to_words(b.emi_amount, "hi"),
           date_to_words(b.due_date, today, "en"), b.dob.strftime("%d/%m/%Y")}
    # "today"/"tomorrow" are everyday words (prompt v12: "clear it today"), as in evals/score.py.
    return {s for s in out if len(s) > 3 and s not in {"today", "tomorrow"}}


def dump(messages):
    return json.dumps(messages, ensure_ascii=False)


def make(llm, today, tmp_path, borrower_id="B001"):
    return Brain(load(), BORROWERS[borrower_id], today, llm, calls_path=tmp_path / "calls.jsonl")


async def run(brain, text, lang_hint=None):
    return [s async for s in brain.respond(text, lang_hint)]


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17"])
@pytest.mark.parametrize("bid", sorted(BORROWERS))
def test_system_prompt_has_no_loan_or_verification_data(bid, version, today, tmp_path):
    b = BORROWERS[bid]
    cfg = load(overrides=[f"behavior.prompt_version={version}"])
    brain = Brain(cfg, b, today, ScriptedLLM([]), calls_path=tmp_path / "calls.jsonl")
    for s in secrets(b, today):
        assert s not in brain.system, s
        assert s not in brain.greeting, s
    assert b.first_name in brain.system


async def test_no_loan_data_reaches_model_before_verification(today, tmp_path):
    b = BORROWERS["B001"]
    llm = ScriptedLLM([
        say("Thanks Arjun. Could you tell me the last four digits of your registered number?"),
        say("", ("verify_identity", {"factor": "last4", "value": "4821"})),
        say("Thank you. Your EMI of twelve thousand five hundred rupees is due the day after tomorrow."),
    ])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    assert brain.state.verified is False
    await run(brain, "It's 4821.")  # code verifies this before the model runs (D-25)
    loan_only = secrets(b, today) - {b.last4}  # the borrower said 4821 themselves
    system, messages = llm.requests[0]  # the only request made before verification passed
    for s in loan_only:
        assert s not in system + dump(messages)
    assert [c["by"] for c in brain.state.tool_calls if c.get("by")] == ["code"]
    after_system, after_messages = llm.requests[1]
    assert "twelve thousand five hundred rupees" in dump(after_messages)
    assert brain.state.disclosure_log and all(d.verified for d in brain.state.disclosure_log)
    assert brain.state.stage is Stage.INTENT


async def test_gated_tool_before_verification_returns_fixed_refusal(today, tmp_path):
    llm = ScriptedLLM([
        say("", ("get_loan_details", {})),
        say("Before I share anything, may I have your date of birth?"),
    ])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes. How much do I owe?")
    _, messages = llm.requests[1]
    tool_result = messages[-1]["content"][0]
    assert json.loads(tool_result["content"]) == NOT_VERIFIED
    assert brain.state.disclosure_log == []


async def test_guard_replaces_unverified_disclosure_and_tells_the_model(today, tmp_path):
    llm = ScriptedLLM([
        say("Hi Arjun. Your EMI of 12500 rupees is due."),
        say("Sorry about that. May I have your date of birth?"),
    ])
    brain = make(llm, today, tmp_path)
    out = await run(brain, "Yes, this is Arjun.")
    assert out[-1].guard == "unverified_disclosure"
    assert "12500" not in out[-1].text
    assert brain.state.guard_hits
    await run(brain, "Okay.")
    _, messages = llm.requests[1]
    assert any(m["role"] == "system" and "not spoken" in m["content"] for m in messages)


async def test_stop_request_ends_call_even_if_model_forgets_to_record(today, tmp_path):
    llm = ScriptedLLM([say("Of course, I'm sorry to bother you. Goodbye.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Please stop calling me.")
    _, messages = llm.requests[0]
    assert messages[-1]["role"] == "system" and "stop" in messages[-1]["content"].lower()
    assert brain.should_hang_up
    record = brain.finish()
    assert record.outcome is Outcome.stop_requested
    assert brain.state.stage is Stage.ENDED
    assert await run(brain, "hello?") == []


@pytest.mark.parametrize("text", ["stop calling me", "Don't call me again", "call mat karo",
                                  "mujhe phone mat karna", "कॉल मत करो", "Stop.",
                                  "I don't want any more calls", "no more calls please",
                                  "Stop bothering me", "don't bother me again",
                                  "Do not contact me", "never contact me again",
                                  "mujhe pareshan mat karo", "pareshan mat kijiye",
                                  "dobara call mat karna", "मुझे परेशान मत करो",
                                  "Don’t call me again", "please call mat kijiye",
                                  # a time or a payment does not soften a permanent stop
                                  "Don't call me in the morning or ever again",
                                  "abhi aur kabhi bhi call mat karna",
                                  "I'll pay, but stop calling me",
                                  "I am telling you, don't call me",
                                  "stop it now", "stop this now",
                                  "I won't pay, don't call me",
                                  "main pay nahi karunga, call mat karo",
                                  "I can't pay anything, do not call",
                                  "bas karo, dobara call mat karna",
                                  "कल से फोन मत करना, कभी नहीं"])
def test_stop_phrases(text):
    assert is_stop_request(text)


@pytest.mark.parametrize("text", ["I'll stop by the bank", "don't worry, I'll pay", "yes",
                                  "no more questions, I'll pay tomorrow",
                                  "don't bother, I already paid",
                                  # a weak phrase with a time or a payment is not a stop (D-24)
                                  "Don't call me in the morning, call in the evening",
                                  "abhi call mat karo, shaam ko karna",
                                  "don't call before 6",
                                  "don't call me now, I'm driving",
                                  "abhi phone mat karo, meeting mein hoon",
                                  "bas karo, main pay kar dunga",
                                  "band karo yeh baat, I will pay",
                                  "don't call me every day, I'll pay on Friday"])
def test_not_stop_phrases(text):
    assert not is_stop_request(text)


async def test_borrower_words_are_kept_for_grounding(today, tmp_path):
    llm = ScriptedLLM([say("", VERIFY_OK), say("Thank you, Arjun.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Last four are 4821.")
    assert brain.state.heard == ["Last four are 4821."]
    assert brain.state.verified


async def test_language_switch_after_two_turns_re_speaks_facts(today, tmp_path):
    llm = ScriptedLLM([
        say("", VERIFY_OK),
        say("Thanks. Your EMI of twelve thousand five hundred rupees is due the day after tomorrow."),
        say("Ji, bilkul."),
        say("Ji, main samajh gayi."),
    ])
    brain = make(llm, today, tmp_path)
    await run(brain, "Last four are 4821.")
    await run(brain, "haan theek hai")  # short: needs a second Hindi turn (full sentences switch at once)
    assert brain.state.reply_language == "en"
    await run(brain, "kal tak kar dunga payment")
    assert brain.state.reply_language == "hinglish"
    _, messages = llm.requests[-1]
    note = messages[-1]
    assert note["role"] == "system" and "Hinglish" in note["content"]
    assert "twelve thousand five hundred rupees" in note["content"]  # Hinglish keeps English numbers


async def test_promise_flow_writes_valid_record(today, tmp_path):
    llm = ScriptedLLM([
        say("", VERIFY_OK),
        say("Your EMI of twelve thousand five hundred rupees is due the day after tomorrow."),
        say("", ("record_outcome", {"outcome": "promise_to_pay",
                                    "promise_to_pay_date": "next Friday", "notes": ""})),
        say("Great, I've noted the thirteenth of November. Thank you, goodbye."),
    ])
    brain = make(llm, today, tmp_path)
    await run(brain, "4821")
    await run(brain, "I'll pay next Friday.")
    assert brain.should_hang_up
    record = brain.finish()
    assert record.promise_to_pay_date == date(2026, 11, 13)
    line = json.loads((tmp_path / "calls.jsonl").read_text().strip())
    assert line["outcome"] == "promise_to_pay" and line["verified"] is True


@pytest.fixture
def no_hold_wait(monkeypatch):
    monkeypatch.setattr("emi_agent.brain.HOLD_RETRY_SECS", 0)


async def test_llm_failure_gives_cached_apology_and_unresolved(today, tmp_path, no_hold_wait):
    """Fails, says the hold line, retries once, fails again: apology (no 'goodbye'), hang up."""
    brain = make(ScriptedLLM([say(fail=True), say(fail=True)]), today, tmp_path)
    out = await run(brain, "Yes, speaking.")
    assert [s.text for s in out] == [context.hold("en", "roman"), context.apology("en", "roman")]
    assert "goodbye" not in out[1].text.lower()
    assert brain.should_hang_up
    assert brain.finish().outcome is Outcome.unresolved


async def test_system_messages_only_follow_user_messages(today, tmp_path):
    llm = ScriptedLLM([
        say("Hi. Your EMI is 12500."),  # guard hit -> note next turn
        say("Thanks."),
        say("Okay, goodbye."),
    ])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "haan ji bolo, kya baat hai")
    await run(brain, "stop calling me")
    for _, messages in llm.requests:
        assert messages[0]["role"] == "user"
        for prev, cur in zip(messages, messages[1:]):
            if cur["role"] == "system":
                assert prev["role"] == "user"


async def test_history_is_append_only(today, tmp_path):
    llm = ScriptedLLM([say("", VERIFY_OK), say("Your EMI is due."), say("Sure.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "4821")
    await run(brain, "ok")
    systems = {system for system, _ in llm.requests}
    assert len(systems) == 1  # system prompt never changes during a call
    for (_, earlier), (_, later) in zip(llm.requests, llm.requests[1:]):
        assert later[:len(earlier)] == earlier


async def test_finish_reflects_actions_after_recording(today, tmp_path):
    llm = ScriptedLLM([
        say("", VERIFY_OK),
        say("", ("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": ""}),
            ("send_payment_link", {"channel": "sms"})),
        say("I've sent the link by SMS. Thank you, goodbye."),
    ])
    brain = make(llm, today, tmp_path)
    await run(brain, "4821, and yes I'll pay today, send me the link")
    assert brain.finish().payment_link_sent is True


async def test_barge_in_keeps_cut_turn_in_history_and_transcript(today, tmp_path):
    """Voice cancels respond() mid-reply (LLD 9): what was said is kept, marked as cut off."""
    llm = ScriptedLLM([
        say("Thank you Arjun. Before I go on, I need to verify you. What is your date of birth?"),
        say("No problem."),
    ])
    brain = make(llm, today, tmp_path)
    gen = brain.respond("Haan, main Arjun bol raha hoon")
    first = await gen.__anext__()
    await gen.aclose()  # barge-in after the first sentence
    assert first.text == "Thank you Arjun."
    assert brain.history[-1] == {"role": "assistant", "content": "Thank you Arjun. [cut off by the borrower]"}
    assert brain.transcript[-1] == ("agent", "Thank you Arjun. [cut off by the borrower]")
    await run(brain, "sorry, kya bola?")  # next turn still works
    assert brain.transcript[-1] == ("agent", "No problem.")


async def test_stop_request_hangs_up_even_if_reply_is_cut(today, tmp_path):
    brain = make(ScriptedLLM([say("Okay, I understand. I will not call again. Goodbye.")]), today, tmp_path)
    gen = brain.respond("stop calling me")
    await gen.__anext__()
    await gen.aclose()
    assert brain.should_hang_up


# ---- code-driven verification (D-25) ----

ASK_LAST4 = "Could you tell me the last four digits of your registered mobile number?"
ASK_DOB = "Could you tell me your date of birth?"


async def test_code_verifies_last4_so_model_needs_one_request(today, tmp_path):
    llm = ScriptedLLM([say(ASK_LAST4), say("Thank you, Arjun. Your EMI is due soon.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "four eight two one")
    assert brain.state.verified and len(llm.requests) == 2  # no verify_identity round trip
    note = llm.requests[1][1][-1]
    assert note["role"] == "system" and "has verified" in note["content"]
    assert "twelve thousand five hundred rupees" in note["content"]


async def test_code_wrong_last4_uses_attempt_and_says_only_mismatch(today, tmp_path):
    llm = ScriptedLLM([say(ASK_LAST4), say("Sorry, that did not match. Could you try again?")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "6054")
    assert not brain.state.verified and brain.state.verify_attempts == 1
    note = llm.requests[1][1][-1]["content"]
    assert "does not match" in note and "Attempts left: 2" in note
    for s in secrets(BORROWERS["B001"], today):
        assert s not in dump(llm.requests[1][1])


async def test_code_partial_dob_asks_for_missing_part_without_attempt(today, tmp_path):
    llm = ScriptedLLM([say(ASK_DOB), say("And the day and month?")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "Nineteen eighty five.")
    assert brain.state.verify_attempts == 0 and not brain.state.tool_calls
    assert "day and month is still missing" in llm.requests[1][1][-1]["content"]


async def test_code_joins_dob_said_over_two_turns(today, tmp_path):
    b = BORROWERS["B001"]
    llm = ScriptedLLM([say(ASK_DOB), say("And the day and month?"), say("Thank you.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, str(b.dob.year))
    await run(brain, f"{b.dob.strftime('%B')} {b.dob.day}")
    assert brain.state.verified


async def test_code_never_rejoins_a_rejected_date(today, tmp_path):
    b = BORROWERS["B001"]
    wrong = b.dob.replace(day=1 if b.dob.day != 1 else 2)
    llm = ScriptedLLM([say(ASK_DOB), say("That did not match. " + ASK_DOB), say("Which day?")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, wrong.isoformat())
    await run(brain, "9")
    assert brain.state.verify_attempts == 1  # "9" alone is not joined with the rejected date


async def test_code_skips_lone_year_when_both_factors_offered(today, tmp_path):
    llm = ScriptedLLM([say("May I have your date of birth or the last four digits of your mobile?"),
                       say("Is that the year you were born, or the last four digits?")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "1985")
    assert not brain.state.tool_calls  # ambiguous: left to the model


async def test_code_does_not_verify_when_nothing_was_asked(today, tmp_path):
    llm = ScriptedLLM([say("How are you today?"), say("Good to hear.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "4821, by the way")
    assert not brain.state.verified and not brain.state.tool_calls


async def test_code_verified_note_overrides_stale_partial_note(today, tmp_path):
    """Voice: a fragment turn ('... unnis') leaves a 'year missing' note, then 'sau pachasi' completes it."""
    b = BORROWERS["B001"]
    llm = ScriptedLLM([say(ASK_DOB), say("And the year?"), say("Thank you.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, f"{b.dob.day} {b.dob.strftime('%B')}")
    await run(brain, str(b.dob.year))
    assert brain.state.verified
    last_note = llm.requests[2][1][-1]["content"]
    assert "replaces any earlier note" in last_note and "do not ask for it again" in last_note


async def test_llm_failure_once_holds_then_answers(today, tmp_path, no_hold_wait):
    brain = make(ScriptedLLM([say(fail=True), say("Ji, boliye.")]), today, tmp_path)
    out = await run(brain, "Yes, speaking.")
    assert [s.text for s in out] == [context.hold("en", "roman"), "Ji, boliye."]
    assert not brain.should_hang_up and "llm_failed" not in brain.state.flags


STOP_OUTCOME = ("record_outcome", {"outcome": "stop_requested", "promise_to_pay_date": None,
                                   "notes": "asked to stop"})


async def test_closing_turn_with_no_words_gets_a_closing_line(today, tmp_path):
    brain = make(ScriptedLLM([say("", STOP_OUTCOME), say("")]), today, tmp_path)
    out = await run(brain, "stop calling me")
    assert [s.text for s in out] == [context.closing("en", "roman", None)]
    assert brain.should_hang_up


async def test_closing_cut_off_is_said_on_the_next_turn_once(today, tmp_path):
    """Live call 2026-11-03: the closing turn was cancelled, then 'Are you there?' got silence."""
    brain = make(ScriptedLLM([say("Theek hai, main aapko dobara call nahi karungi.", STOP_OUTCOME)]),
                 today, tmp_path)
    gen = brain.respond("stop calling me")
    await gen.__anext__()
    await gen.aclose()  # barge-in cancels the turn
    assert brain.should_hang_up
    out = await run(brain, "Are you there?")
    assert [s.text for s in out] == [context.closing("en", "roman", None)]
    assert await run(brain, "Hello?") == []


async def test_llm_failure_after_outcome_recorded_thanks_not_apology(today, tmp_path, no_hold_wait):
    """Live call 2026-11-03: promise recorded, then rate limits -> 'technical problem'."""
    brain = make(ScriptedLLM([say("", STOP_OUTCOME), say(fail=True), say(fail=True)]), today, tmp_path)
    out = await run(brain, "stop calling me")
    assert out[-1].text == context.closing("en", "roman", None)
    assert brain.should_hang_up


async def test_code_joins_last4_split_across_turns(today, tmp_path):
    """Owner call 2026-11-03: 'It's double' | '7 4 3.' (B001's last four are 4821)."""
    llm = ScriptedLLM([say(ASK_LAST4), say("I got only part of it."), say("Thank you, Arjun.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "It's four")
    assert not brain.state.verified and brain.state.verify_attempts == 0
    await run(brain, "8 2 1.")
    assert brain.state.verified and brain.state.tool_calls[-1]["by"] == "code"


async def test_code_never_joins_onto_a_rejected_last4(today, tmp_path):
    llm = ScriptedLLM([say(ASK_LAST4), say("That did not match."), say("Okay.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "1 2 3 4")             # full answer, wrong: one attempt used
    await run(brain, "8 2 1")               # must not become 4 + 821 or similar
    assert not brain.state.verified and brain.state.verify_attempts == 1


async def test_escalation_without_recorded_outcome_records_the_reason(today, tmp_path):
    """S03 v6/v7 evals: escalate_to_human(hardship) then a close with no record_outcome."""
    for why in ("hardship", "dispute"):
        brain = make(ScriptedLLM([say("Thank you.")]), today, tmp_path)
        brain.state.verified = True
        brain.state.heard.append("I lost my job and this amount is wrong")
        assert brain.tools.call("escalate_to_human", {"reason": why})["escalated"] is True
        record = brain.finish()
        assert record.outcome is Outcome(why) and record.escalate_to_human


async def test_requested_escalation_without_outcome_stays_unresolved(today, tmp_path):
    brain = make(ScriptedLLM([say("Thank you.")]), today, tmp_path)
    brain.state.verified = True
    brain.state.flags.add("escalate:requested")
    brain.state.escalate_to_human = True
    assert brain.finish().outcome is Outcome.unresolved


async def test_borrower_saying_hindi_numbers_switches_number_style(today, tmp_path):
    brain = make(ScriptedLLM([say("ठीक है।")]), today, tmp_path, borrower_id="B004")
    assert brain.state.numbers == "english"
    assert "rupees" in brain.tools.facts().amount_spoken  # Hindi call, English numbers by default
    brain.state.verified = True
    brain._add_turn_notes("मैं पच्चीस तारीख को दे दूँगी", None)
    assert brain.state.numbers == "hindi"
    assert "रुपये" in brain.tools.facts().amount_spoken
    assert any("Hindi words" in n for n in brain._pending_notes)


ASK = "क्या आप अपनी जन्म तिथि या registered mobile number के आखिरी चार अंक बता सकती हैं?"


async def test_replay_live_call_dob_with_short_i_verifies_by_code(today, tmp_path):
    """Live call 2026-11-04, B002 (DOB 22 July 1994): first answer as Deepgram wrote it."""
    brain = make(ScriptedLLM([say(ASK), say("धन्यवाद प्रिया जी।")]), today, tmp_path, borrower_id="B002")
    await run(brain, "हां जी, मैं प्रिया शर्मा बोल रही हूं.")
    await run(brain, "मेरा जन्म तिथि बाइस July 19 94 है.")
    assert brain.state.verified and brain.state.verify_attempts == 0


async def test_replay_live_call_garbled_dob_keeps_hindi(today, tmp_path):
    brain = make(ScriptedLLM([say(ASK), say("जुलाई की कौन सी तारीख?"), say("कौन सी तारीख?")]),
                 today, tmp_path, borrower_id="B002")
    await run(brain, "हां जी, मैं प्रिया शर्मा बोल रही हूं.")
    # "Buys" is now read as 22 (heard._MISHEARD_22); "Twice" is the garbled form still left unread.
    await run(brain, "Twice July 19 94.")
    await run(brain, "Twice July")
    assert brain.state.reply_language == "hi" and not brain.state.verified
    assert brain.state.verify_attempts == 0


async def test_greeting_cut_tells_the_model_to_ask_identity_again(today, tmp_path):
    """Live call 2026-11-04: a false barge-in cut the greeting; the model went straight to DOB."""
    brain = make(ScriptedLLM([say("Ji?")]), today, tmp_path, borrower_id="B002")
    brain.greeting_cut()
    assert any("ask again whether you are speaking with Priya" in n for n in brain._pending_notes)
    # Silent after the cut: code repeats the greeting itself, and the note is no longer needed.
    assert brain.silence_line(close=False) == brain.greeting
    assert not any("cut off" in n for n in brain._pending_notes)


async def test_silence_lines_nudge_then_close(today, tmp_path):
    brain = make(ScriptedLLM([say("Ji?")]), today, tmp_path, borrower_id="B002")
    assert brain.silence_line(close=False) == context.nudge("hi", brain.script)
    assert any("borrower was silent" in n for n in brain._pending_notes)
    assert brain.silence_line(close=True) == context.silent_close("hi", brain.script)
    assert brain.should_hang_up and brain.finish().outcome is Outcome.unresolved


# Live call 2026-11-04, B002 (DOB 22 July 1994).
async def test_ordinal_dob_is_verified_by_code(today, tmp_path):
    llm = ScriptedLLM([say(ASK_DOB), say("Thank you.")])
    brain = make(llm, today, tmp_path, "B002")
    await run(brain, "Yes, speaking.")
    await run(brain, "22nd July 19 94.")
    assert brain.state.verified and len(llm.requests) == 2


async def test_model_dob_guess_on_partial_answer_is_not_checked(today, tmp_path):
    # "Twice July 19 94" (live, बाईस misheard): code finds no day; the model guessing 19 July must not use an attempt.
    llm = ScriptedLLM([say(ASK_DOB),
                       say("", ("verify_identity", {"factor": "dob", "value": "1994-07-19"})),
                       say("Which day in July?")])
    brain = make(llm, today, tmp_path, "B002")
    await run(brain, "Yes, speaking.")
    await run(brain, "Date of birth, twice, July 19 94.")
    assert brain.state.verify_attempts == 0
    assert brain.state.tool_calls[-1]["by"] == "blocked"


async def test_invented_mismatch_is_replaced_and_rest_dropped(today, tmp_path):
    llm = ScriptedLLM([say(ASK_DOB), say("क्षमा चाहती हूँ, यह जानकारी भी मेल नहीं खा रही। "
                                         "आपके पास एक आखिरी मौका है। या तो सही date of birth बता दीजिए।")])
    brain = make(llm, today, tmp_path, "B002")
    await run(brain, "Yes, speaking.")
    out = await run(brain, "July 19 94.")
    assert [s.guard for s in out] == ["invented_verify"]
    assert brain.state.verify_attempts == 0
    assert brain.state.guard_hits[-1]["category"] == "invented_verify"


async def test_real_mismatch_may_be_stated(today, tmp_path):
    llm = ScriptedLLM([say(ASK_DOB), say("Sorry, that doesn't match. Could you try again?")])
    brain = make(llm, today, tmp_path, "B002")
    await run(brain, "Yes, speaking.")
    out = await run(brain, "21 July 1994")
    assert brain.state.verify_attempts == 1 and all(s.guard is None for s in out)


async def test_code_wrong_last4_asks_for_dob_next(today, tmp_path):
    # Owner, 2026-11-04: last four digits first; if they do not match, the date of birth.
    llm = ScriptedLLM([say(ASK_LAST4), say("Sorry, that did not match. Could you tell me your date of birth?")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    await run(brain, "6054")
    note = llm.requests[1][1][-1]["content"]
    assert "date of birth instead" in note and "do not ask for the digits again" in note


def test_v9_prompt_asks_last4_first():
    from emi_agent.context import system_prompt
    cfg = load()
    assert cfg["behavior"]["prompt_version"] in ("v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17")
    text = system_prompt(BORROWERS["B001"], cfg, "en", "Hello")
    assert "Ask first for only the last four digits" in text


def test_v10_prompt_dispute_says_concerned_team():
    from emi_agent.context import system_prompt
    cfg = load(overrides=["behavior.prompt_version=v10"])
    text = system_prompt(BORROWERS["B001"], cfg, "en", "Hello")
    assert "the concerned team will look into it and arrange a callback" in text
    assert "a colleague will check it and call them back" not in text
    assert "Ask first for only the last four digits" in text  # v9 change kept


async def test_sentence_said_twice_in_one_turn_is_spoken_once(today, tmp_path):
    # v10 eval S04 run 2: closing written, record_outcome called, closing written again.
    close = "समय देने के लिए धन्यवाद, आपका दिन शुभ हो।"
    llm = ScriptedLLM([say("Hello.", ("record_outcome", {"outcome": "unresolved", "promise_to_pay_date": None,
                                                          "notes": "x"})), say(close + " " + close)])
    brain = make(llm, today, tmp_path)
    out = await run(brain, "ok")
    assert [s.text for s in out].count(close) == 1


# No dead air on tool turns (owner call 2026-11-04 13:46: tool turns 1.8-2.4 s to first audio).
async def test_bridge_line_while_a_tool_runs_then_the_reply(today, tmp_path):
    llm = ScriptedLLM([say("", ("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None,
                                                    "notes": "x"})), say("Thanks, noted.")], tool_start=True)
    brain = make(llm, today, tmp_path)
    brain.state.verified = True
    out = await run(brain, "I'll pay on time.")
    assert out[0].bridge and out[0].text == context.bridge("record_outcome", "en", brain.script)
    assert any("already heard you say" in m["content"] for m in llm.requests[1][1] if m["role"] == "system")


async def test_no_bridge_when_the_model_already_spoke(today, tmp_path):
    llm = ScriptedLLM([say("Thank you.", ("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None,
                                                              "notes": "x"})), say("Have a good day.")], tool_start=True)
    brain = make(llm, today, tmp_path)
    brain.state.verified = True
    out = await run(brain, "I'll pay.")
    assert not any(s.bridge for s in out)


async def test_bridge_alone_does_not_replace_the_closing(today, tmp_path):
    # Model records the outcome and says nothing more: the closing must still be said.
    llm = ScriptedLLM([say("", ("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None,
                                                    "notes": "x"})), say("")], tool_start=True)
    brain = make(llm, today, tmp_path)
    brain.state.verified = True
    brain.state.flags.add("asked_exact_date")  # the "by the due date?" question was already asked
    out = await run(brain, "Okay bye.")
    assert out[0].bridge and out[-1].text == context.closing("en", brain.script, "Arjun")


# Owner, 2026-11-04 (labelling): "What's this about?" before verification is answered first.
async def test_purpose_question_gets_the_account_note(today, tmp_path):
    for i, asked in enumerate(["Yes, this is Arjun. What's this about?", "हाँ, बोलिए, किस बारे में call किया है?",
                               "haan ji, kya baat hai?"]):
        llm = ScriptedLLM([say("It's regarding your account with Lumen Finance.")])
        brain = make(llm, today, tmp_path / str(i))
        await run(brain, asked)
        notes = [m["content"] for m in llm.requests[0][1] if m["role"] == "system"]
        assert any("asked what this call is about" in n and "never say loan, EMI" in n and "personal matter for Arjun" in n for n in notes), asked


async def test_no_purpose_note_without_the_question(today, tmp_path):
    llm = ScriptedLLM([say("Thank you.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Yes, speaking.")
    assert not any("asked what this call is about" in str(m["content"]) for m in llm.requests[0][1])


def test_v12_prompt_states_the_fee_once_and_asks_to_pay_online():
    from emi_agent.context import system_prompt
    text = system_prompt(BORROWERS["B007"], load(overrides=["behavior.prompt_version=v12"]), "en", "Hello")
    assert "late payment charge" in text and "never repeat it" in text and "after they describe hardship" in text
    assert "secure payment link on SMS or WhatsApp" in text and "how they would like to proceed" not in text


async def test_invented_notice_is_dropped(today, tmp_path):
    llm = ScriptedLLM([say("Thank you. Just a reminder, this call is being recorded. How can I help?")])
    brain = make(llm, today, tmp_path)
    out = await run(brain, "Yes.")
    assert [s.text for s in out] == ["Thank you.", "How can I help?"]
    assert brain.state.guard_hits[-1]["category"] == "invented_notice"


def test_v13_prompt_hardship_is_caring_with_one_handoff():
    from emi_agent.context import system_prompt
    text = system_prompt(BORROWERS["B007"], load(overrides=["behavior.prompt_version=v13"]), "en", "Hello")
    assert "a colleague from our team" in text and "read back exactly what you noted" in text


def test_v14_prompt_helps_first_and_never_says_colleague():
    from emi_agent.context import system_prompt
    text = system_prompt(BORROWERS["B004"], load(overrides=["behavior.prompt_version=v14"]), "en", "Hello")
    assert "our internal team will check this and arrange a call back" in text
    assert "flagged it for review" in text and "ask what amount they expected" in text
    assert "a colleague will" not in text.lower() and 'Never say "a colleague"' in text


async def test_hardship_drops_anything_else_and_repeated_warm_close(today, tmp_path):
    llm = ScriptedLLM([say("I'm so sorry, Deepa. A colleague from our team will call you. Is there anything else I can help with?"),
                       say("I understand. Thank you for talking with me, Deepa. Have a good day."),
                       say("I've noted it. Thank you for talking with me, Deepa. Have a good day.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.flags.add("escalate:hardship")
    first = [s.text for s in await run(brain, "I lost my job.")]
    second = [s.text for s in await run(brain, "When will they call?")]
    third = [s.text for s in await run(brain, "Please note it.")]
    assert not any("anything else" in x for x in first)
    assert any("good day" in x for x in second) and not any("good day" in x for x in third)


async def test_comfortable_and_take_care_lines_never_spoken(today, tmp_path):
    llm = ScriptedLLM([say("यह सुनकर सच में दुख हुआ, Deepa जी। क्या यह कुछ समय की बात है? आप जो comfortable हों, बस वही बताइए।"),
                       say("जी, मैंने note कर लिया है। आप का ध्यान रखें। आपका दिन शुभ हो।")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    first = [s.text for s in await run(brain, "मेरी job चली गई है")]
    second = [s.text for s in await run(brain, "शायद दो महीने")]
    assert not any("comfortable" in x for x in first) and any("कुछ समय" in x for x in first)
    assert not any("ध्यान रखें" in x for x in second) and any("दिन शुभ हो" in x for x in second)


async def test_hand_off_is_said_once_per_call(today, tmp_path):
    llm = ScriptedLLM([say("I'm sorry. Our internal team will check this and arrange a call back for you."),
                       say("I understand. Our internal team will check this and arrange a call back for you.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    first = [s.text for s in await run(brain, "I lost my job.")]
    second = [s.text for s in await run(brain, "When will they call?")]
    assert any("internal team" in x for x in first) and second == ["I understand."]


# Live hardship call 2026-11-04 16:03.
async def test_reply_cut_at_once_is_marked_unheard_and_note_added(today, tmp_path):
    import asyncio
    llm = ScriptedLLM([say("मुझे सच में बहुत अफ़सोस है Deepa जी। क्या यह short-term है?"), say("Okay.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    gen = brain.respond("मेरा job चला गया है")
    await gen.__anext__()          # first sentence produced, then the borrower cuts in at once
    brain.reply_unheard()
    await gen.aclose()
    assert "Not heard" in brain.history[-1]["content"]
    await run(brain, "So is there any way")
    assert any("did not hear it" in str(m["content"]) for m in llm.requests[1][1] if m["role"] == "system")


async def test_contact_question_asked_once(today, tmp_path):
    llm = ScriptedLLM([say("Noted. Is this number the best one to reach you on?"),
                       say("Okay. और कुछ बताइए, क्या यह number ही best है?")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    await run(brain, "I lost my job.")
    second = [s.text for s in await run(brain, "After 4 PM.")]
    assert second == ["Okay."]


async def test_guard_safe_line_counts_as_the_hand_off(today, tmp_path):
    llm = ScriptedLLM([say("I can't promise or hint at any waiver or reduction myself, but I've noted it.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    out = await run(brain, "Can the fee be reduced?")
    assert not any(s.guard == "promise" for s in out)  # a refusal is not a promise


def test_sentence_keeps_bridge_and_speech_apart():
    from emi_agent.brain import Sentence
    s = Sentence("x", None, speech=("number_style",))
    assert s.bridge is False and s.speech == ("number_style",)


async def test_apology_once_but_again_if_the_first_was_not_heard(today, tmp_path):
    sorry = "I'm really sorry to hear about your job, Deepa."
    llm = ScriptedLLM([say(sorry), say(sorry + " Is it short-term?"), say(sorry + " Noted.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    gen = brain.respond("I lost my job")
    await gen.__anext__()
    brain.reply_unheard()            # cut 250 ms in: never heard
    await gen.aclose()
    second = [s.text for s in await run(brain, "So is there any way")]
    third = [s.text for s in await run(brain, "the fee can be reduced?")]
    assert sorry in second and sorry not in third


async def test_language_of_a_sentence_split_by_barge_ins(today, tmp_path):
    llm = ScriptedLLM([say("जी।"), say("जी।"), say("जी।")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.reply_language = brain.lang.current = "hi"
    for text in ["Ok. I know it's overdue. Actually, मेरा अभी job चला गया है and मैं अभी pay नहीं कर सकती हूं.",
                 "So is there any way"]:
        gen = brain.respond(text)
        await gen.__anext__()
        await gen.aclose()
    await run(brain, "the EMI or the late fee can be reduced?")
    assert brain.state.reply_language == "hi"


def test_v15_prompt_closes_with_good_day_and_no_comfortable_line():
    from emi_agent.context import system_prompt
    text = system_prompt(BORROWERS["B007"], load(overrides=["behavior.prompt_version=v15"]), "en", "Hello")
    assert "आपका दिन शुभ हो" in text and "what is comfortable;" not in text
    assert "Please take care, <name>" not in text and "our internal team will check this" in text


# Prompt v16: hardship as a code-owned flow (special.py). Code says which step comes next.
def _notes(llm, i):
    return " ".join(str(m["content"]) for m in llm.requests[i][1] if m["role"] == "system")


async def test_hardship_flow_one_step_per_turn_then_hang_up(today, tmp_path):
    llm = ScriptedLLM([say("", ("escalate_to_human", {"reason": "hardship"})),
                       say("यह सुनकर सच में दुख हुआ, Deepa जी। क्या यह कुछ समय की बात है, या अभी कहना मुश्किल है?"),
                       say("जी, मैंने note कर लिया है कि job चली गई है और दो-तीन महीने लग सकते हैं। हमारी internal team "
                           "आपकी बात check करके आपको callback arrange करेगी। क्या यह number आपसे बात करने के लिए ठीक रहेगा?"),
                       say("", ("record_outcome", {"outcome": "hardship", "promise_to_pay_date": None, "notes": "job loss"})),
                       say("जी, शाम का समय note कर लिया। बात करने के लिए धन्यवाद, Deepa जी। आपका दिन शुभ हो।")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    await run(brain, "मेरी job चली गई है")
    assert brain.hardship_voice and brain.state.special == "hardship" and brain.state.special_step == 1
    await run(brain, "शायद दो-तीन महीने")
    assert "step 2 of 3" in _notes(llm, 2) and brain.state.special_step == 2
    last = [s.text for s in await run(brain, "हाँ, शाम को call कीजिए")]
    assert "step 3 of 3" in _notes(llm, 3) and any("दिन शुभ हो" in x for x in last)
    assert brain.should_hang_up and brain.state.outcome.outcome is Outcome.hardship


async def test_hardship_step_is_given_again_when_the_reply_was_not_heard(today, tmp_path):
    llm = ScriptedLLM([say("", ("escalate_to_human", {"reason": "hardship"})),
                       say("यह सुनकर सच में दुख हुआ, Deepa जी। क्या यह कुछ समय की बात है?"),
                       say("यह सुनकर सच में दुख हुआ। क्या यह कुछ समय की बात है?")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    gen = brain.respond("मेरी job चली गई है")
    await gen.__anext__()          # bridge line out, then the borrower cuts in at once
    brain.reply_unheard()
    await gen.aclose()
    assert brain.state.special_step == 0
    await run(brain, "मतलब अभी पैसे का problem है")
    assert "step 1 of 3" in _notes(llm, 2) and brain.state.special_step == 1


async def test_close_step_hangs_up_even_without_record_outcome(today, tmp_path):
    llm = ScriptedLLM([say("Thank you for talking with me, Deepa. Have a good day.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.special, brain.state.special_step = "hardship", 2
    brain.state.flags.add("escalate:hardship")
    await run(brain, "Okay, evenings are better.")
    assert brain.should_hang_up
    assert brain.finish().outcome is Outcome.hardship


async def test_stop_request_skips_the_flow_step(today, tmp_path):
    llm = ScriptedLLM([say("Okay, thank you.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.special, brain.state.special_step = "hardship", 1
    await run(brain, "Please stop calling me.")
    assert "Special situation" not in _notes(llm, 0)


@pytest.mark.parametrize("text,cue", [("मेरी job चली गई है", True), ("I lost my job last month", True),
                                      ("Papa hospital mein admit hain", True), ("मेरी तबीयत ठीक नहीं है", True),
                                      ("salary nahi aayi abhi tak", False), ("Haan, main pay kar dungi", False),
                                      ("I'll pay on the fifth", False)])
def test_hardship_cue(text, cue):
    from emi_agent.special import hardship_cue
    assert hardship_cue(text) is cue


def test_v16_prompt_leaves_the_steps_to_the_call_system():
    from emi_agent.context import system_prompt
    text = system_prompt(BORROWERS["B007"], load(overrides=["behavior.prompt_version=v16"]), "en", "Hello")
    assert "one step at a time" in text and "our internal team will check this and arrange a call back" in text
    assert "Is this something short-term" not in text and 'Never say "a colleague"' in text


async def test_flow_step_says_what_was_already_said(today, tmp_path):
    llm = ScriptedLLM([say("I'm really sorry to hear that. I can't change the amount myself, but I've noted it. "
                           "Is this something short-term?"), say("Noted.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.special, brain.state.special_step = "hardship", 0
    await run(brain, "I lost my job, can you reduce the EMI?")
    await run(brain, "Hard to say.")
    note = _notes(llm, 1)
    assert "step 2 of 3" in note and "do not say again: that you are sorry; that you can't change it yourself" in note


async def test_flow_close_bridge_is_not_a_bare_okay(today, tmp_path):
    llm = ScriptedLLM([say("", ("record_outcome", {"outcome": "hardship", "promise_to_pay_date": None, "notes": "x"})),
                       say("I've noted that you want a person to confirm it. Thank you for talking with me, Deepa. "
                           "Have a good day.")], tool_start=True)
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.special, brain.state.special_step = "hardship", 2
    out = [s.text for s in await run(brain, "Please make sure a person confirms it.")]
    assert out[0] == "I understand." and "Okay." not in out
    result = [m for m in llm.requests[1][1] if m["role"] == "user" and isinstance(m["content"], list)][-1]
    assert "name what they asked" in result["content"][0]["content"]


async def test_flow_question_ends_the_reply(today, tmp_path):
    llm = ScriptedLLM([say("I'm really sorry to hear that, Deepa. Is this something short-term, or hard to say "
                           "right now? Our internal team will check this and arrange a call back for you.")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.special, brain.state.special_step = "hardship", 0
    out = [s.text for s in await run(brain, "I lost my job.")]
    assert out[-1].endswith("?") and not any("internal team" in x for x in out)


# Live call B004 (2026-11-04): a loan due on 8 November with no charge.
async def test_b004_invented_late_fee_is_replaced_by_the_true_policy(today, tmp_path):
    llm = ScriptedLLM([say("आपके loan terms के अनुसार, five hundred rupees का late payment charge लग गया है। "
                           "क्या आज payment हो पाएगा?")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    out = await run(brain, "क्या आप मुझे बता सकते हैं कि what is the late payment?", "hi")
    assert out[0].guard == "fee_not_added" and "नहीं लगा" in out[0].text and "five hundred rupees" in out[0].text


async def test_amount_not_given_by_code_is_never_spoken(today, tmp_path):
    llm = ScriptedLLM([say("The late payment charge is seven hundred rupees.")])
    brain = make(llm, today, tmp_path, "B001")
    brain.state.verified = True
    out = await run(brain, "What is the late fee?")
    assert out[0].guard == "unknown_amount" and "seven hundred" not in out[0].text


async def test_split_sentence_is_given_to_the_model_whole(today, tmp_path):
    llm = ScriptedLLM([say("Sure, one moment please. The late fee is"), say("Okay.")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    gen = brain.respond("But can you tell me what is the late payment")
    await gen.__anext__()          # the borrower goes on talking: the reply is cut (heard, not at once)
    await gen.aclose()
    await run(brain, "I need to pay")
    assert "All together they said: \"But can you tell me what is the late payment I need to pay\"" in _notes(llm, 1)


def test_verified_note_says_whether_the_emi_is_due_or_overdue(today):
    from emi_agent.tools import loan_facts
    upcoming = context.note_verified(loan_facts(BORROWERS["B004"], today, "hi-en", "devanagari"))
    overdue = context.note_verified(loan_facts(BORROWERS["B007"], today, "en", "roman"))
    assert "not yet due" in upcoming and "No late charge has been added" in upcoming
    assert "overdue by" in overdue and "clear it today" in overdue


# Owner, 2026-11-04 (v17): generic policy in the prompt, no "privacy", no verbatim example lines.
def test_v17_prompt_has_the_policy_and_no_privacy_or_scripted_lines():
    from emi_agent.context import system_prompt
    text = system_prompt(BORROWERS["B004"], load(), "hi", "Namaste")
    assert "a late payment charge of five hundred rupees applies" in text and "get_policy" in text
    assert "For your privacy" not in text and "आपकी privacy" not in text and "account verification" in text
    assert "Would you be able to clear it today? I can send you" not in text and "बता सकती हैं?" not in text
    assert "{policy}" not in text


def test_greeting_says_ai_assistant(today):
    from emi_agent.context import greeting
    assert "AI assistant" in greeting(BORROWERS["B004"], "Lumen Finance", "hi", "devanagari")
    assert greeting(BORROWERS["B001"], "Lumen Finance", "en", "roman").startswith("Hello, I'm an AI assistant")


def test_policy_bridge_rotates():
    lines = [context.bridge("get_policy", "en", "roman", n=n) for n in range(3)]
    assert len(set(lines)) == 3 and context.bridge("get_policy", "en", "roman", n=3) == lines[0]


async def test_borrower_amount_can_be_read_back_in_another_language(today, tmp_path):
    llm = ScriptedLLM([say("मैंने note कर लिया है कि आपको बारह हज़ार रुपये की उम्मीद है, जबकि हमारे records में "
                           "eighteen thousand rupees हैं।")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    out = await run(brain, "मेरी EMI twelve thousand rupees है, eighteen thousand नहीं।", "hi")
    assert out[0].guard is None and "बारह हज़ार" in out[0].text


# v17-dev2 S05 run 2: "Close politely.</think>" was in the agent's reply (the judge found it).
_LEAKS = ["Close politely.</think> क्षमा चाहती हूँ, शायद गलत number मिल गया। समय देने के लिए धन्यवाद।",
          "<think>The user says wrong number. Close politely.</think> क्षमा चाहती हूँ, शायद गलत number मिल गया।"]


async def test_leaked_reasoning_is_never_spoken(today, tmp_path):
    for leak in _LEAKS:
        brain = make(ScriptedLLM([say(leak)]), today, tmp_path, "B006")
        out = [s.text for s in await run(brain, "ये तो गलत नंबर है, मैं कोई Amit नहीं हूँ।", "hi")]
        assert out and not any("think" in x or "politely" in x for x in out) and out[0].startswith("क्षमा")
        assert "think>" not in json.dumps(brain.history, ensure_ascii=False)


async def test_normal_english_first_sentence_is_not_held_back(today, tmp_path):
    brain = make(ScriptedLLM([say("Thank you, Arjun. Your EMI is due soon.")]), today, tmp_path)
    out = [s.text for s in await run(brain, "Yes, this is Arjun.")]
    assert out[0] == "Thank you, Arjun."


async def test_held_instruction_like_sentence_is_said_when_no_reasoning_follows(today, tmp_path):
    brain = make(ScriptedLLM([say("Now, about your EMI. It is due on the fifth.")]), today, tmp_path)
    brain.state.verified = True
    out = [s.text for s in await run(brain, "Okay.")]
    assert out[0] == "Now, about your EMI." and len(out) == 2


@pytest.mark.parametrize("text", ["8834. Aur suniye, is topic pe mujhe call mat karo.",
                                  "Nahi, koi payment link bhejne ki zaroorat nahi hai. Maine bola na, mujhe is bare mein call mat karo."])
def test_stop_request_is_judged_per_sentence(text):
    assert is_stop_request(text)  # v17-dev3 S12 run 2


def test_not_now_in_the_same_sentence_still_softens_a_stop():
    assert not is_stop_request("Abhi call mat karo, shaam ko 6 baje karna.")


# Owner, 2026-11-04 (labelling S12 run 1): apologise, one gentle reminder line, then stop.
def test_stop_note_names_the_emi_only_when_verified(today):
    from emi_agent.tools import loan_facts
    facts = loan_facts(BORROWERS["B006"], today, "hi-en", "devanagari")
    v = context.note_stop(True, facts)
    assert "apologise" in v and facts.due_date_spoken in v and "no late fee" in v and "no request to pay" in v
    u = context.note_stop(False)
    assert "never mention a loan" in u and "late fee" not in u


async def test_stop_after_verification_gets_the_gentle_note(today, tmp_path):
    llm = ScriptedLLM([say("माफ़ कीजिए, आपको परेशान किया।")])
    brain = make(llm, today, tmp_path, "B006")
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    await run(brain, "mujhe call mat karo, is bare mein.", "hi")
    assert "gentle reminder" in _notes(llm, 0) and "stop_requested" in _notes(llm, 0)


def test_mixed_script_stop_request():
    assert is_stop_request("देखो, मुझे इस बारे में call मत करो. Payment मैं खुद कर दूँगा.")


def test_stop_outcome_result_carries_the_closing_words(today, tmp_path):
    brain = make(ScriptedLLM([]), today, tmp_path, "B006")
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    brain.state.flags.add("stop_requested")
    r = brain.tools.call("record_outcome", {"outcome": "stop_requested", "promise_to_pay_date": None, "notes": "x"})
    assert "apologise briefly" in r["message"] and "gentle reminder" in r["message"]


# C45, live call B004 19:31: the fixed goodbye cost one more model request after record_outcome.
RECORD_WILL_PAY = ("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})


async def test_plain_record_closes_with_the_code_line_and_no_more_model_requests(today, tmp_path):
    llm = ScriptedLLM([say("", RECORD_WILL_PAY)], tool_start=True)  # a second request would fail the script
    brain = make(llm, today, tmp_path)
    brain.state.verified = True
    out = await run(brain, "No. Thank you.")
    assert len(llm.requests) == 1
    assert out[0].bridge and out[-1].text == context.closing("en", brain.script, "Arjun")
    assert brain.should_hang_up and "code_closed" in brain.state.flags


async def test_record_with_something_still_owed_keeps_the_model_round(today, tmp_path):
    for owed in ["question", "model_spoke", "promise", "stop"]:
        words, first = "No. Thank you.", say("", RECORD_WILL_PAY)
        if owed == "question":
            words = "Will the link work on UPI?"
        elif owed == "model_spoke":
            first = say("Sure, the link works with UPI.", RECORD_WILL_PAY)
        elif owed == "promise":
            first = say("", ("record_outcome", {"outcome": "promise_to_pay", "promise_to_pay_date": "5 November",
                                                "notes": "x"}))
        elif owed == "stop":
            words = "Please stop calling me."
            first = say("", ("record_outcome", {"outcome": "stop_requested", "promise_to_pay_date": None,
                                                "notes": "x"}))
        llm = ScriptedLLM([first, say("Thank you for your time, have a good day.")], tool_start=True)
        brain = make(llm, today, tmp_path / owed)
        brain.state.verified = True
        brain.state.flags.add("asked_exact_date")
        await run(brain, words)
        assert brain.state.tool_calls[-1]["result"].get("recorded"), owed  # recorded, so only the rule held it
        assert len(llm.requests) == 2, owed
        assert "code_closed" not in brain.state.flags, owed


# C44, live call B004 19:31: one sentence mixed number styles.
async def test_hindi_ordinal_due_date_is_spoken_in_the_calls_number_style(today, tmp_path):
    llm = ScriptedLLM([say("Sunita जी, आपकी personal loan की EMI अठारह हज़ार रुपये है, जो आठवीं November को due है।")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    out = await run(brain, "जी, बताइए")
    assert "eighteen thousand rupees" in out[0].text and "eighth November" in out[0].text
    assert "आठवीं" not in out[0].text and "अठारह" not in out[0].text


async def test_overdue_due_date_in_hindi_is_mapped_too(today, tmp_path):
    b = BORROWERS["B002"]
    from emi_agent.speech_format import hindi_date_forms
    llm = ScriptedLLM([say(f"आपकी EMI {hindi_date_forms(b.due_date)[0]} को due थी।")])
    brain = make(llm, today, tmp_path, "B002")
    brain.state.verified = True
    out = await run(brain, "जी")
    assert date_to_words(b.due_date, today, "hi-en", brain.script) in out[0].text


async def test_borrower_can_ask_for_numbers_in_hindi_then_english(today, tmp_path):
    llm = ScriptedLLM([say("जी।"), say("जी।"), say("जी।")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    await run(brain, "Amount हिंदी में बताइए")
    note = [m["content"] for m in llm.requests[0][1] if m["role"] == "system"][-1]
    assert brain.state.numbers == "hindi" and "asked for numbers in Hindi" in note and "अठारह हज़ार रुपये" in note
    await run(brain, "English mein bataiye please")
    note = [m["content"] for m in llm.requests[1][1] if m["role"] == "system"][-1]
    assert brain.state.numbers == "english" and "repeat it now" in note and "eighteen thousand rupees" in note
    await run(brain, "मैं पाँच तारीख को दूँगी")  # their own Hindi number words no longer flip the choice
    assert brain.state.numbers == "english"


async def test_hindi_number_words_still_switch_when_nothing_was_asked(today, tmp_path):
    llm = ScriptedLLM([say("जी।")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    await run(brain, "मैं पाँच तारीख को दूँगी")
    assert brain.state.numbers == "hindi"


# C47: the slow-model filler (said by bot.py) is part of the turn, and nothing repeats it.
class HeldLLM:
    """The model answers only after `release`: like a slow request, so bot.py's filler comes first."""
    def __init__(self, inner):
        self.inner, self.release = inner, asyncio.Event()

    async def stream(self, system, messages, tools):
        await self.release.wait()
        async for item in self.inner.stream(system, messages, tools):
            yield item


async def _turn_with_filler(brain, llm, words):
    """Run one turn; the filler fires while the model is still silent. Returns (filler, sentences)."""
    task = asyncio.ensure_future(run(brain, words))
    await asyncio.sleep(0.01)  # the turn has started and waits for the model
    filler = brain.slow_filler()
    assert brain.slow_filler() is None  # once per turn
    llm.release.set()
    return filler, await task


async def test_after_a_filler_no_tool_bridge_and_it_is_in_the_transcript(today, tmp_path):
    llm = HeldLLM(ScriptedLLM([say("", ("send_payment_link", {"channel": "whatsapp"})), say("Sent it on WhatsApp.")],
                              tool_start=True))
    brain = make(llm, today, tmp_path)
    brain.state.verified = True
    filler, out = await _turn_with_filler(brain, llm, "Yes, send it.")
    assert filler == "One moment."
    assert not any(s.bridge for s in out) and [s.text for s in out] == ["Sent it on WhatsApp."]
    assert brain.transcript[-1] == ("agent", "One moment. Sent it on WhatsApp.")


async def test_after_a_filler_a_failed_request_gets_no_hold_line(today, tmp_path, monkeypatch):
    import emi_agent.brain as brain_mod
    monkeypatch.setattr(brain_mod, "HOLD_RETRY_SECS", 0)
    llm = HeldLLM(ScriptedLLM([say(fail=True), say("Thank you.")]))
    brain = make(llm, today, tmp_path)
    _, out = await _turn_with_filler(brain, llm, "Yes, speaking.")
    assert [s.text for s in out] == ["Thank you."]  # not "One moment, please." after "One moment."


async def test_fillers_rotate_and_reset_each_turn(today, tmp_path):
    seen = []
    inner = ScriptedLLM([say("Okay."), say("Okay.")])
    brain = make(inner, today, tmp_path)
    for _ in range(2):
        llm = HeldLLM(inner)
        brain.llm = llm
        filler, _ = await _turn_with_filler(brain, llm, "Yes.")
        seen.append(filler)
    assert seen == ["One moment.", "Just a second."]


# C48, live call B004 19:51: the channel question was asked four times.
OFFER_HI = "due date से पहले payment करने के लिए क्या मैं link भेज दूँ SMS या WhatsApp पर?"


async def _after_offer(today, tmp_path, said, reply="जी, link WhatsApp पर भेज दिया है।"):
    llm = ScriptedLLM([say(reply)])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    brain.state.last_agent = OFFER_HI
    out = await run(brain, said)
    return brain, llm, out


async def test_plain_yes_to_the_link_offer_sends_it_on_whatsapp(today, tmp_path):
    for i, said in enumerate(["जी भेज दीजिए.", "हाँ जी", "Yes please", "भेज दीजिए", "okay send it"]):
        brain, llm, _ = await _after_offer(today, tmp_path / str(i), said)
        sent = [c for c in brain.state.tool_calls if c["name"] == "send_payment_link"]
        assert sent and sent[0]["args"] == {"channel": "whatsapp"} and brain.state.payment_link_sent, said
        notes = [m["content"] for m in llm.requests[0][1] if m["role"] == "system"]
        assert any("just sent the payment link on WhatsApp" in n for n in notes), said


async def test_no_auto_send_when_a_channel_is_named_or_something_is_asked(today, tmp_path):
    for i, said in enumerate(["SMS pe bhej dijiye", "WhatsApp.", "What's it?", "जी, पर fee कितनी है?",
                              "नहीं, अभी नहीं", "No, not now"]):
        brain, _, _ = await _after_offer(today, tmp_path / str(i), said, reply="ठीक है।")
        assert not brain.state.payment_link_sent, said


async def test_no_auto_send_without_an_offer_or_before_verification(today, tmp_path):
    brain, _, _ = await _after_offer(today, tmp_path / "a", "जी")
    assert brain.state.payment_link_sent  # control: the offer was there
    llm = ScriptedLLM([say("ठीक है।")])
    brain = make(llm, today, tmp_path / "b", "B004")
    brain.state.verified = True
    brain.state.last_agent = "आपकी EMI eighteen thousand rupees है।"
    await run(brain, "जी")
    assert not brain.state.payment_link_sent
    brain = make(ScriptedLLM([say("ठीक है।")]), today, tmp_path / "c", "B004")
    brain.state.last_agent = OFFER_HI  # not verified
    await run(brain, "जी भेज दीजिए")
    assert not brain.state.payment_link_sent


async def test_channel_question_is_not_asked_again(today, tmp_path):
    # Asked in the previous turn: the repeat is dropped, the rest of the reply stays.
    brain, _, out = await _after_offer(today, tmp_path / "a", "Can you please tell me the EMI amount in English?",
                                       reply="EMI amount eighteen thousand rupees है। क्या link भेज दूँ SMS या WhatsApp पर?")
    assert [s.text for s in out] == ["EMI amount eighteen thousand rupees है।"]
    assert brain.state.guard_hits[-1]["category"] == "channel_again"
    # After the link went: never.
    llm = ScriptedLLM([say("आपको SMS चाहिए या WhatsApp?")])
    brain = make(llm, today, tmp_path / "b", "B004")
    brain.state.verified, brain.state.payment_link_sent = True, True
    out = [s async for s in brain.respond("ठीक है")]
    assert not any("SMS" in s.text for s in out)
    assert brain.state.guard_hits and brain.state.guard_hits[-1]["category"] == "channel_again"


async def test_first_channel_question_is_kept(today, tmp_path):
    llm = ScriptedLLM([say(OFFER_HI)])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    out = await run(brain, "जी, बताइए")
    assert any("SMS" in s.text for s in out)


# C51, live call B004 20:13: invited more questions, then said goodbye and hung up in one breath.
RECORD_PROMISE = ("record_outcome", {"outcome": "promise_to_pay", "promise_to_pay_date": "8 November", "notes": "x"})
CLOSE_WITH_INVITE = ("समझ गई, eighth November को payment कर देंगी। यदि कोई और सवाल हो तो बताइए। "
                     "समय देने के लिए धन्यवाद, आपका दिन शुभ हो।")


async def test_invitation_at_the_close_waits_for_the_answer(today, tmp_path):
    llm = ScriptedLLM([say("", RECORD_PROMISE), say(CLOSE_WITH_INVITE),
                       say("जी, समय देने के लिए धन्यवाद, आपका दिन शुभ हो।")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    out = await run(brain, "No. No. I said 8th November.")
    assert [s.text for s in out][-1] == "यदि कोई और सवाल हो तो बताइए।"  # stops after the invitation
    assert not brain.should_hang_up                                     # and waits
    out = await run(brain, "नहीं, धन्यवाद")
    assert "दिन शुभ हो" in out[-1].text and brain.should_hang_up


async def test_second_invitation_is_dropped_and_the_call_closes(today, tmp_path):
    llm = ScriptedLLM([say("", RECORD_PROMISE), say(CLOSE_WITH_INVITE),
                       say("और कुछ पूछना है आपको? समय देने के लिए धन्यवाद, आपका दिन शुभ हो।")])
    brain = make(llm, today, tmp_path, "B004")
    brain.state.verified = True
    await run(brain, "8th November.")
    out = await run(brain, "ठीक है")
    assert [s.text for s in out] == ["समय देने के लिए धन्यवाद, आपका दिन शुभ हो।"] and brain.should_hang_up


async def test_stop_request_never_waits_on_an_invitation(today, tmp_path):
    llm = ScriptedLLM([say("Sorry to disturb you. Any other questions? Thank you, have a good day.")])
    brain = make(llm, today, tmp_path)
    await run(brain, "Please stop calling me.")
    assert brain.should_hang_up


# v17 hold-out S14b run 1 (C57): a failed tool is never followed by the code close.
async def test_failed_link_retry_keeps_the_model_round(today, tmp_path):
    llm = ScriptedLLM([say("", ("send_payment_link", {"channel": "whatsapp"})), say("", RECORD_WILL_PAY),
                       say("Sorry, the link still did not go through.")], tool_start=True)
    brain = make(llm, today, tmp_path)
    brain.config["faults"]["payment_link_fail"] = "always"
    brain.state.verified = True
    out = await run(brain, "Could you try sending it once more?")
    assert len(llm.requests) == 3 and "code_closed" not in brain.state.flags
    assert out[-1].text == "Sorry, the link still did not go through."


# v17 hold-out S24 (C54): a callback asked for but never recorded by the model.
async def test_callback_asked_in_words_is_the_outcome_when_the_model_records_nothing(tmp_path):
    today = date(2026, 11, 3)
    brain = make(ScriptedLLM([say("Ji, main do ghante baad call kar lungi.")]), today, tmp_path, "B008")
    await run(brain, "Main driving kar raha hoon, do ghante baad call kar lijiye.")
    record = brain.finish()
    assert record.outcome is Outcome.callback_requested
    assert record.callback_time.startswith("2026-11-03")


async def test_no_callback_request_still_falls_back_to_unresolved(today, tmp_path):
    brain = make(ScriptedLLM([say("Ji, theek hai.")]), today, tmp_path)
    await run(brain, "Haan, kaun?")
    assert brain.finish().outcome is Outcome.unresolved


# v17 hold-out S27 (C55): before verification the close names nobody.
async def test_closing_names_the_borrower_only_once_verified(today, tmp_path):
    brain = make(ScriptedLLM([]), today, tmp_path, "B004")
    assert brain._close_name() is None
    brain.state.verified = True
    assert brain._close_name() == "Sunita"
    assert "Sunita" not in context.closing("hi", "devanagari", None)
    assert "जी" not in context.closing("hi", "devanagari", None)


# v17 hold-out S30 run 1 (C56): a stop with "before that" and a payment date is a promise.
@pytest.mark.parametrize("text", [
    "Now listen, I will pay on 20th November, so stop calling me before that.",
    "20 tarikh ko pay kar dunga, tab tak pareshan mat karo",
    "I'll pay on the 15th, don't bother me until then",
])
def test_conditional_stop_is_not_a_stop(text):
    assert not is_stop_request(text)


def test_stop_with_never_again_and_until_is_still_a_stop():
    assert is_stop_request("Stop calling me, never again, not even before that")


# Live call B001 21:27: "Time given for your time, have a good day." (model's word-for-word translation).
async def test_garbled_goodbye_is_replaced_by_the_fixed_line(today, tmp_path):
    brain = make(ScriptedLLM([say("", RECORD_WILL_PAY), say("Thank you Arjun. Time given for your time, have a good day.")]),
                 today, tmp_path)
    brain.state.verified = True
    brain.state.flags.add("asked_exact_date")
    out = await run(brain, "Yeah, I'll do it tomorrow by end of the day?")
    texts = [s.text for s in out]
    assert texts[-2:] == ["Thank you Arjun.", "Have a good day."]
    assert any(h["category"] == "fixed_close" for h in brain.state.guard_hits)


async def test_garbled_goodbye_without_thanks_gets_the_full_closing(today, tmp_path):
    brain = make(ScriptedLLM([say("Time given for your time, have a good day.")]), today, tmp_path)
    brain.state.verified = True
    out = await run(brain, "ok")
    assert out[-1].text == context.closing("en", brain.script, "Arjun")


async def test_good_or_informative_goodbyes_are_kept(today, tmp_path):
    for i, line in enumerate(["Thank you for your time, have a good day.", "Thank you Arjun ji, have a nice day.",
                              "आपके समय के लिए धन्यवाद, Arjun जी। आपका दिन शुभ हो।",
                              "समय देने के लिए धन्यवाद, आपका दिन शुभ हो।",
                              "Okay, I've noted that you will pay by the fifth of November, have a good day."]):
        brain = make(ScriptedLLM([say(line)]), today, tmp_path / str(i))
        brain.state.verified = True
        await run(brain, "ok")
        assert not any(h["category"] == "fixed_close" for h in brain.state.guard_hits), line


def test_date_question_says_by_not_before(today, tmp_path):
    brain = make(ScriptedLLM([]), today, tmp_path)
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    r = brain.tools.call("record_outcome", {"outcome": "will_pay", "promise_to_pay_date": None, "notes": "x"})
    assert r["error"] == "no_date" and "never 'before'" in r["message"]


# Owner demo call B007 21:43: no fixed code goodbye once money trouble came up; the model words the close.
async def test_no_code_close_after_money_trouble(today, tmp_path):
    llm = ScriptedLLM([say("", RECORD_WILL_PAY), say("I understand, Deepa. Thank you for telling me.")], tool_start=True)
    brain = make(llm, today, tmp_path)
    brain.state.verified = True
    brain.state.flags.add("asked_exact_date")
    await run(brain, "Maybe in a month or two, once I sort out the financial problem.")
    assert len(llm.requests) == 2 and "code_closed" not in brain.state.flags


# v17-hardship-practical S03 run 1 (C65): no link sent when the question was not a link offer.
async def test_please_to_a_call_back_question_sends_no_link(today, tmp_path):
    brain = make(ScriptedLLM([say("Okay, I'll ask the team.")]), today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    brain.state.last_agent = ("Any amount paid through the link goes towards this EMI. Is a part payment possible "
                              "for now, or should I ask the team to call you?")
    await run(brain, "Please ask the team to call me.")
    assert not brain.state.payment_link_sent


async def test_plain_yes_to_a_link_offer_still_sends_it(today, tmp_path):
    brain = make(ScriptedLLM([say("Done, it's on WhatsApp.")]), today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    brain.state.last_agent = "Should I send you the secure payment link on WhatsApp?"
    await run(brain, "Yes please.")
    assert brain.state.payment_link_sent


# Owner demo call B007 22:03 (C67): the practical reply was cut 1.9 s in (not "at once"), so its facts and
# question were never heard; the flow must give the step again, briefly, after answering the borrower.
async def test_hardship_step_cut_partway_is_given_again(today, tmp_path):
    llm = ScriptedLLM([say("", ("escalate_to_human", {"reason": "hardship"})),
                       say("समझ सकती हूँ। यह late charge one-time है। अभी क्या part payment possible है?"),
                       say("जी, बाद में भी same link से कर सकती हैं। क्या अभी part payment possible है, या team call करे?")])
    brain = make(llm, today, tmp_path, "B007")
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    gen = brain.respond("I'm going through some financial issues")
    await gen.__anext__()
    await gen.__anext__()          # played for a while (not "at once"), then the borrower cuts in
    await gen.aclose()
    assert brain.state.special_step == 0
    await run(brain, "can I pay later?")
    _, messages = llm.requests[-1]
    notes = " ".join(m["content"] for m in messages if m["role"] == "system")
    assert "cut in partway" in notes and "step 1 of 3" in notes
    assert brain.state.special_step == 1  # heard in full this time


def test_flow_rules_answer_pay_later_with_true_facts_only():
    from emi_agent import special
    text = special.instruction("hardship", 1)
    assert "pay later" in text and "same payment link" in text and "Never promise a new due date" in text


# Live call B003 22:25 (C67): the reply was fully written (turn ended normally) but its audio was cut.
async def test_playback_cut_of_a_written_flow_step_gives_it_again(today, tmp_path):
    llm = ScriptedLLM([say("", ("escalate_to_human", {"reason": "hardship"})),
                       say("समझ सकती हूँ। late charge one-time है। अभी part payment possible है, या team call करे?"),
                       say("जी। late charge one-time है। अभी part payment possible है, या team call करे?")])
    brain = make(llm, today, tmp_path, "B003")
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    await run(brain, "मेरी नौकरी चली गई है, अभी हालत सही नहीं है")
    assert brain.state.special_step == 1      # written in full: moved on
    brain.playback_cut()                      # ...but the borrower cut the audio
    assert brain.state.special_step == 0
    await run(brain, "बिल्कुल payment नहीं कर सकती हूं")
    _, messages = llm.requests[-1]
    notes = " ".join(m["content"] for m in messages if m["role"] == "system")
    assert "cut in partway" in notes and "step 1 of 3" in notes


def test_playback_cut_outside_a_flow_changes_nothing(today, tmp_path):
    brain = make(ScriptedLLM([]), today, tmp_path)
    brain.playback_cut()
    assert brain.state.special is None and brain._pending_notes == []


# testing-regression S08 run 1: "khelna band karo" (stop this game) is not a stop request.
@pytest.mark.parametrize("text,stop", [
    ("Aur yeh personal matter personal matter khelna band karo, seedha batao kitna paisa dena baaki hai.", False),
    ("Bas karo!", True), ("बस करो।", True), ("Yeh roz roz ke calls band karo.", True),
    ("band karo yeh baat, I will pay", False)])
def test_stop_it_words_count_only_when_short_or_about_calls(text, stop):
    assert is_stop_request(text) is stop
