"""Brain: context assembly (G-01, G-02, G-10), disclosure log, stop, language, guard wiring."""
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


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13"])
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
    assert [s.text for s in out] == [context.closing("en", "roman", "Arjun")]
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
    assert [s.text for s in out] == [context.closing("en", "roman", "Arjun")]
    assert await run(brain, "Hello?") == []


async def test_llm_failure_after_outcome_recorded_thanks_not_apology(today, tmp_path, no_hold_wait):
    """Live call 2026-11-03: promise recorded, then rate limits -> 'technical problem'."""
    brain = make(ScriptedLLM([say("", STOP_OUTCOME), say(fail=True), say(fail=True)]), today, tmp_path)
    out = await run(brain, "stop calling me")
    assert out[-1].text == context.closing("en", "roman", "Arjun")
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
    assert cfg["behavior"]["prompt_version"] in ("v9", "v10", "v11", "v12", "v13")
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
    text = system_prompt(BORROWERS["B007"], load(), "en", "Hello")
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
    text = system_prompt(BORROWERS["B007"], load(), "en", "Hello")
    assert "a colleague from our team" in text and "read back exactly what you noted" in text
    assert 'Don\'t ask "Is there anything else I can help with?"' in text
