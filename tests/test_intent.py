"""LLM intent check on hardship and dispute escalations the word lists did not confirm (intent.py)."""
import asyncio

import pytest

from emi_agent import intent
from emi_agent.brain import Brain
from emi_agent.config import load
from emi_agent.data import load_borrowers
from emi_agent.llm import LLMError, LLMTurn
from scripted_llm import ScriptedLLM, say

BORROWERS = load_borrowers()
HARDSHIP = ("escalate_to_human", {"reason": "hardship"})


class CheckLLM:
    """Plays one answer for the intent check and records what it was sent."""

    def __init__(self, answer='{"intent": "hardship", "why": "Says money is very tight at home."}',
                 fail=False, delay=0.0):
        self.answer, self.fail, self.delay, self.sent = answer, fail, delay, []

    async def stream(self, system, messages, tools):
        self.sent.append((system, messages, tools))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise LLMError("down")
        yield "text", self.answer
        yield "turn", LLMTurn(content=[], tool_calls=[], stop_reason="end_turn",
                              usage={"input": 150, "output": 20, "cache_read": 0}, provider="fake")


def test_digits_are_hidden():
    assert intent.redact("dob 18 December 1983, last four ६६०१") == "dob ## December ####, last four ####"


@pytest.mark.parametrize("text,label", [
    ('{"intent": "hardship", "why": "x"}', "hardship"),
    ('<think>hmm</think> {"intent": "LATE_SALARY", "why": "x"}', "late_salary"),
    ('{"intent": "waive_it", "why": "x"}', None),
    ("not json", None)])
def test_only_known_labels_are_read(text, label):
    r = intent._parse(text)
    assert (r["intent"] if r else None) == label


async def test_check_sends_only_the_borrowers_lines_without_digits():
    llm = CheckLLM()
    r = await intent.check(llm, ["a", "My dob is 18 12 1983", "ghar mein bahut problem chal rahi hai"], 1.5)
    system, messages, tools = llm.sent[0]
    assert r["intent"] == "hardship" and tools == []
    assert "1983" not in messages[0]["content"] and "ghar mein" in messages[0]["content"]


async def test_check_fails_open_on_error_and_timeout():
    assert (await intent.check(CheckLLM(fail=True), ["x"], 1.5))["intent"] is None
    assert (await intent.check(CheckLLM(delay=0.2), ["x"], 0.05))["intent"] is None


def _brain(today, tmp_path, agent_turns, checker):
    brain = Brain(load(), BORROWERS["B007"], today, ScriptedLLM(agent_turns), calls_path=tmp_path / "c.jsonl",
                  intent_llm=checker)
    brain.state.verified = True
    brain.state.loan_facts = brain.tools.facts()
    return brain


async def _say(brain, text):
    return [s async for s in brain.respond(text)]


# Owner idea, 2026-11-04: no word list covers "ghar mein abhi haath tang hai".
async def test_hardship_in_words_no_list_knows_goes_through(today, tmp_path):
    checker = CheckLLM()
    brain = _brain(today, tmp_path, [say("", HARDSHIP), say("I'm so sorry to hear that.")], checker)
    await _say(brain, "Ghar mein abhi haath bahut tang hai, kuch samajh nahi aa raha")
    assert brain.state.escalate_to_human and len(checker.sent) == 1
    assert any(c["name"] == "_intent_check" and c["result"]["intent"] == "hardship" for c in brain.state.tool_calls)
    assert "[Check: Says money is very tight at home.]" in brain.finish().notes


async def test_late_salary_is_refused_with_the_reason(today, tmp_path):
    checker = CheckLLM('{"intent": "late_salary", "why": "Salary comes on the 10th; no setback."}')
    brain = _brain(today, tmp_path, [say("", HARDSHIP), say("Okay, the 10th then?")], checker)
    await _say(brain, "Salary 10 tareekh ko aayegi, tab dekhta hoon")
    result = next(c for c in brain.state.tool_calls if c["name"] == "escalate_to_human")["result"]
    assert result["error"] == "not_hardship" and "Salary comes on the" in result["message"]
    assert not brain.state.escalate_to_human


async def test_failed_check_lets_the_escalation_through(today, tmp_path):
    brain = _brain(today, tmp_path, [say("", HARDSHIP), say("I'm sorry.")], CheckLLM(fail=True))
    await _say(brain, "Bahut mushkil chal raha hai")
    assert brain.state.escalate_to_human


async def test_listed_words_skip_the_check(today, tmp_path):
    checker = CheckLLM()
    brain = _brain(today, tmp_path, [say("", HARDSHIP), say("I'm so sorry.")], checker)
    await _say(brain, "I lost my job last month")
    assert brain.state.escalate_to_human and checker.sent == []


async def test_the_warm_line_goes_to_the_agent_not_the_borrower(today, tmp_path):
    checker = CheckLLM('{"intent": "hardship", "why": "Money trouble at home.", '
                       '"reply": "Yeh sun kar sach mein bura laga, aap fikar mat kijiye."}')
    agent = ScriptedLLM([say("", HARDSHIP), say("Yeh sun kar bura laga.")])
    brain = Brain(load(), BORROWERS["B007"], today, agent, calls_path=tmp_path / "c.jsonl", intent_llm=checker)
    brain.state.verified = True
    out = [s.text async for s in brain.respond("Ghar mein abhi haath bahut tang hai")]
    result = next(c for c in brain.state.tool_calls if c["name"] == "escalate_to_human")["result"]
    assert "Yeh sun kar sach mein bura laga" in result["message"]  # guidance for the agent
    assert "Yeh sun kar sach mein bura laga, aap fikar mat kijiye." not in out  # spoken only by the agent


def test_check_model_is_sarvam_with_no_groq():
    from emi_agent.llm import OpenAICompatLLM, build_intent_llm
    cfg = load()
    cfg["tts"]["sarvam_api_key"] = "x"
    m = build_intent_llm(cfg)
    assert isinstance(m, OpenAICompatLLM) and m.provider == "sarvam" and m.model == "sarvam-105b-conversations"


async def test_a_slow_answer_still_gives_its_label():
    class Slow(CheckLLM):
        async def stream(self, system, messages, tools):
            yield "text", '{"intent": "complaint", "why": "Angry at the fee'
            await asyncio.sleep(1)
            yield "text", '"}'
    r = await intent.check(Slow(), ["Pagal ho kya, late fee?"], 0.05)
    assert r["intent"] == "complaint"
