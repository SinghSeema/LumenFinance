"""bot.BrainProcessor turn-taking: when a reply starts, barge-in, duplicates. No network, fake Brain."""
import asyncio

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame, BotStoppedSpeakingFrame, TTSSpeakFrame,
    AggregatedTextFrame, InterimTranscriptionFrame, InterruptionFrame, LLMFullResponseEndFrame,
    LLMFullResponseStartFrame, TranscriptionFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

from emi_agent.bot import BrainProcessor, looks_unfinished
from emi_agent.brain import Sentence
from emi_agent.config import load


class FakeBrain:
    def __init__(self, reply="Theek hai.", delay=0.0):
        self.turns, self.reply, self.delay = [], reply, delay
        self.should_hang_up = False
        self.hardship_voice = False
        self.greeting = "Namaste, kya meri baat Priya ji se ho rahi hai?"
        self.cut = 0

    def greeting_cut(self):
        self.cut += 1

    def slow_filler(self):
        self.fillers = getattr(self, "fillers", 0) + 1
        return "जी, एक second।" if self.fillers == 1 else None

    def slow_filler_again(self):
        self.fillers_again = getattr(self, "fillers_again", 0) + 1
        return "बस एक moment और, देख रही हूँ।"

    def reply_unheard(self):
        self.unheard = getattr(self, "unheard", 0) + 1

    def playback_cut(self):
        self.playback_cuts = getattr(self, "playback_cuts", 0) + 1

    def silence_line(self, close):
        if close:
            self.should_hang_up = True
        return "CLOSE" if close else "NUDGE"

    async def respond(self, text, hint=None):
        self.turns.append(text)
        await asyncio.sleep(self.delay)
        yield Sentence(self.reply)


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(FrameProcessor, "process_frame", lambda self, f, d: _noop())

    def _make(brain, stt_wait=0.2, unfinished_wait=0.05, silence=(0.2, 0.3, 0.3), hardship_wait=0.05,
              filler_after=1.2, filler_again=2.0):
        cfg = load(overrides=[f"turn.stt_wait_secs={stt_wait}", f"turn.filler_after_secs={filler_after}",
                              f"turn.filler_again_after_secs={filler_again}",
                              f"turn.unfinished_wait_secs={unfinished_wait}",
                              f"turn.hardship_unfinished_wait_secs={hardship_wait}",
                              f"turn.silence_after_barge_in_secs={silence[0]}",
                              f"turn.silence_prompts_secs={silence[1]}",
                              f"turn.silence_close_secs={silence[2]}"])
        p = BrainProcessor(brain, cfg, tmp_path / "lat.jsonl")
        p.pushed = []

        async def push(frame, direction=None):
            p.pushed.append(frame)
        p.push_frame = push
        p.create_task = lambda coro, name=None: asyncio.get_running_loop().create_task(coro)

        async def cancel(task, timeout=1.0):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        p.cancel_task = cancel
        return p
    return _make


async def _noop():
    return None


def final(text):
    return TranscriptionFrame(text, "borrower", "t")


def interim(text):
    return InterimTranscriptionFrame(text, "borrower", "t")


async def feed(p, *frames, settle=0.05):
    for f in frames:
        await p.process_frame(f, None)
    await asyncio.sleep(settle)


async def test_final_before_vad_stop_replies_at_once_without_waiting(make):
    """The usual case: Deepgram finalized before VAD stopped; no fixed wait."""
    brain = FakeBrain()
    p = make(brain, stt_wait=5.0)
    await feed(p, VADUserStartedSpeakingFrame(), interim("haan main"), final("Haan main Rahul."),
               VADUserStoppedSpeakingFrame(), settle=0.05)
    assert brain.turns == ["Haan main Rahul."]


async def test_pending_words_at_vad_stop_wait_for_their_final(make):
    """'Yeah.' final, rest still interim at VAD stop: one turn with everything, no fragment turn."""
    brain = FakeBrain()
    p = make(brain, stt_wait=5.0)
    await feed(p, VADUserStartedSpeakingFrame(), final("Yeah."), interim("You're right"),
               VADUserStoppedSpeakingFrame())
    assert brain.turns == []
    await feed(p, final("You're right. 7th of November."))
    assert brain.turns == ["Yeah. You're right. 7th of November."]


async def test_final_arriving_after_vad_stop_starts_turn(make):
    """Slow STT: nothing at all by VAD stop, then the final arrives (the 40 s dead-air bug)."""
    brain = FakeBrain()
    p = make(brain, stt_wait=5.0)
    await feed(p, VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame())
    assert brain.turns == []
    await feed(p, final("Main Rahul Verma bol raha hoon."))
    assert brain.turns == ["Main Rahul Verma bol raha hoon."]


async def test_no_final_within_cap_uses_interim(make):
    brain = FakeBrain()
    p = make(brain, stt_wait=0.1)
    await feed(p, VADUserStartedSpeakingFrame(), interim("saat saat chaar teen"),
               VADUserStoppedSpeakingFrame(), settle=0.25)
    assert brain.turns == ["saat saat chaar teen"]


async def test_duplicate_final_is_dropped_not_a_barge_in(make):
    brain = FakeBrain(delay=0.3)
    p = make(brain)
    await feed(p, VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame(),
               final("Can you hear me?"), final("Can you hear me?"), settle=0.4)
    assert brain.turns == ["Can you hear me?"]
    assert not any(isinstance(f, InterruptionFrame) for f in p.pushed)


async def test_backchannel_while_agent_talks_is_ignored_but_month_interrupts(make):
    """A lone month interrupts only as an answer to the DOB question (2026-11-04 rule)."""
    brain = FakeBrain(delay=0.3)
    p = make(brain)
    p.last_agent_text = "Kya aap apni date of birth bata sakti hain?"
    await feed(p, final("Haan ji, boliye."), VADUserStoppedSpeakingFrame(), settle=0.01)
    await feed(p, VADUserStartedSpeakingFrame(), final("haan"), settle=0.01)  # agent still replying
    assert not any(isinstance(f, InterruptionFrame) for f in p.pushed)
    p.last_agent_text = "Kya aap apni date of birth bata sakti hain?"
    await feed(p, final("April"), settle=0.01)
    assert any(isinstance(f, InterruptionFrame) for f in p.pushed)
    await feed(p, VADUserStoppedSpeakingFrame(), settle=0.4)
    assert brain.turns == ["Haan ji, boliye.", "April"]


async def test_lone_number_without_a_number_question_does_not_interrupt(make):
    p = make(FakeBrain(delay=0.3))
    p.last_agent_text = "Aap kaise pay karna chahengi?"
    await feed(p, final("Haan ji."), VADUserStoppedSpeakingFrame(), settle=0.01)
    await feed(p, VADUserStartedSpeakingFrame(), interim("2"), settle=0.01)
    assert not any(isinstance(f, InterruptionFrame) for f in p.pushed)


async def test_replay_live_call_interim_without_speech_does_not_cut_the_greeting(make):
    """Live call 2026-11-04: 2 s into the greeting an interim (likely "2" for "hello") arrived with
    no VAD speech and stopped the greeting; then 31 s of silence."""
    brain = FakeBrain()
    p = make(brain)
    await p.greet()
    await feed(p, BotStartedSpeakingFrame(), interim("2"), interim("नमस्ते मैं"), settle=0.01)
    assert not any(isinstance(f, InterruptionFrame) for f in p.pushed) and brain.cut == 0


async def test_real_barge_in_on_the_greeting_tells_the_brain(make):
    brain = FakeBrain()
    p = make(brain)
    await p.greet()
    await feed(p, BotStartedSpeakingFrame(), VADUserStartedSpeakingFrame(), interim("hello hello"),
               settle=0.01)
    assert any(isinstance(f, InterruptionFrame) for f in p.pushed) and brain.cut == 1


async def test_stop_request_interrupts_even_without_vad(make):
    p = make(FakeBrain())
    await p.greet()
    await feed(p, BotStartedSpeakingFrame(), interim("stop calling me"), settle=0.01)
    assert any(isinstance(f, InterruptionFrame) for f in p.pushed)


def _spoken(p):
    return [f.text for f in p.pushed if isinstance(f, TTSSpeakFrame)]


async def test_silence_after_a_false_barge_in_prompts_then_closes(make):
    brain = FakeBrain()
    p = make(brain, silence=(0.1, 0.2, 0.2))
    ended = []

    async def end_call():
        ended.append(True)
    p.end_call = end_call
    await p.greet()
    await feed(p, BotStartedSpeakingFrame(), VADUserStartedSpeakingFrame(), interim("hello hello"),
               VADUserStoppedSpeakingFrame(), settle=0.01)  # barge-in, but no final ever comes
    # Deepgram never sends a final for this segment (live call): the interim goes stale.
    await asyncio.sleep(0.15)
    assert _spoken(p)[-1] == "NUDGE"
    await feed(p, BotStartedSpeakingFrame(), BotStoppedSpeakingFrame(), settle=1.5)  # close + 1 s quiet
    assert _spoken(p)[-1] == "CLOSE" and ended


async def test_borrower_speaking_cancels_the_silence_prompt(make):
    p = make(FakeBrain(), silence=(0.1, 0.1, 0.1))
    await feed(p, BotStartedSpeakingFrame(), BotStoppedSpeakingFrame(), settle=0.02)
    await feed(p, VADUserStartedSpeakingFrame(), settle=0.2)
    assert _spoken(p)[1:] == []  # nothing but the greeting-less start; no nudge while they talk


class TwoSentenceBrain(FakeBrain):
    async def respond(self, text, hint=None):
        self.turns.append(text)
        yield Sentence("**Namaste** ji.")
        await asyncio.sleep(self.delay)
        yield Sentence("Aapka din shubh ho.")


async def test_reply_is_one_tts_response_with_both_sentences(make):
    """One LLM-style response, so TTS speaks the sentences back to back (no per-sentence pause)."""
    p = make(TwoSentenceBrain())
    await feed(p, final("Hello?"), VADUserStoppedSpeakingFrame())
    kinds = [type(f).__name__ for f in p.pushed
             if isinstance(f, (LLMFullResponseStartFrame, AggregatedTextFrame, LLMFullResponseEndFrame))]
    assert kinds == ["LLMFullResponseStartFrame", "AggregatedTextFrame", "AggregatedTextFrame",
                     "LLMFullResponseEndFrame"]
    assert [f.text for f in p.pushed if isinstance(f, AggregatedTextFrame)] == [
        "Namaste ji. ", "Aapka din shubh ho. "]


async def test_barge_in_mid_reply_sends_no_response_end(make):
    p = make(TwoSentenceBrain(delay=0.5))
    await feed(p, final("Hello?"), VADUserStoppedSpeakingFrame(), settle=0.05)
    await feed(p, final("Ruko ruko, ek minute"), settle=0.05)  # barge-in during the reply
    assert any(isinstance(f, InterruptionFrame) for f in p.pushed)
    assert not any(isinstance(f, LLMFullResponseEndFrame) for f in p.pushed)


@pytest.mark.parametrize("text, unfinished", [
    ("जी मैं राहुल", True), ("right now", True), ("7 4", True), ("It's double", True),
    ("मैं अभी payment नहीं कर पाऊंगा. Because", True), ("Tuesday.", False),
    ("Double 7, 4 3.", False), ("बोल रहा हूं.", False), ("Can you hear me?", False),
    ("Main aapko bata dun ki", True),
])
def test_looks_unfinished(text, unfinished):
    """Fragments from the 2026-11-03 owner calls."""
    assert looks_unfinished(text) is unfinished


async def test_unfinished_words_wait_and_join_when_borrower_goes_on(make):
    brain = FakeBrain()
    p = make(brain, unfinished_wait=0.3)
    await feed(p, VADUserStartedSpeakingFrame(), final("जी मैं राहुल"), VADUserStoppedSpeakingFrame())
    assert brain.turns == []  # no reply to the fragment
    await feed(p, VADUserStartedSpeakingFrame(), final("बोल रहा हूं."), VADUserStoppedSpeakingFrame())
    assert brain.turns == ["जी मैं राहुल बोल रहा हूं."]


async def test_unfinished_words_get_a_reply_after_the_pause(make):
    brain = FakeBrain()
    p = make(brain, unfinished_wait=0.1)
    await feed(p, VADUserStartedSpeakingFrame(), final("haan ji"), VADUserStoppedSpeakingFrame(),
               settle=0.2)
    assert brain.turns == ["haan ji"]


async def test_hang_up_waits_for_the_borrower_to_finish(make):
    """Owner call 2026-11-03: the borrower started a reply to the closing line and was cut off."""
    p = make(FakeBrain())
    p.bot_spoke, p.user_speaking = True, True
    waiter = asyncio.get_running_loop().create_task(p._wait_until_quiet(quiet=0.05, limit=2.0))
    await asyncio.sleep(0.3)
    assert not waiter.done()  # still talking: no hang-up
    p.user_speaking = False
    await asyncio.wait_for(waiter, 0.5)


def _dg_result(confidence, words):
    """A Deepgram ListenV1Results-shaped object: what TranscriptionFrame.result carries."""
    from types import SimpleNamespace as NS
    ws = [NS(word=w.lower(), punctuated_word=w, confidence=c) for w, c in words]
    return NS(channel=NS(alternatives=[NS(confidence=confidence, words=ws)]))


def test_confidence_note_lists_low_words_and_lowest_transcript_score():
    from emi_agent.bot import confidence_note
    # Live call 2026-11-04: "बाईस July" heard as "buys, July".
    note = confidence_note([_dg_result(0.81, [("Date", 0.99), ("buys,", 0.41), ("July", 0.97)]),
                            _dg_result(0.93, [("19", 0.95), ("94.", 0.9)])])
    assert note == "confidence 0.81 | low words: buys,(0.41)"


def test_confidence_note_empty_without_results():
    from emi_agent.bot import confidence_note
    assert confidence_note([]) == ""
    assert confidence_note([_dg_result(0.95, [("Okay.", 0.95)])]) == "confidence 0.95"


def test_confidence_note_shows_languages_deepgram_detected():
    from types import SimpleNamespace as NS
    from emi_agent.bot import confidence_note
    r = NS(channel=NS(alternatives=[NS(confidence=0.9, languages=["hi", "en"],
                                       words=[NS(word="bies", punctuated_word="Bies.", confidence=0.59)])]))
    assert confidence_note([r]) == "confidence 0.90 | langs en,hi | low words: Bies.(0.59)"


# Hardship heard (owner, 2026-11-04): wait longer mid-sentence and speak a little slower.
async def test_hardship_words_get_the_longer_wait(make):
    brain = FakeBrain()
    p = make(brain, unfinished_wait=0.05, hardship_wait=0.4)
    await feed(p, VADUserStartedSpeakingFrame(), final("मेरी job चली गई है और"), VADUserStoppedSpeakingFrame(),
               settle=0.15)
    assert brain.turns == []  # the normal 0.05 s wait would have replied already
    await feed(p, VADUserStartedSpeakingFrame(), final("पैसे का problem है."), VADUserStoppedSpeakingFrame())
    assert brain.turns == ["मेरी job चली गई है और पैसे का problem है."]


async def test_slower_pace_sent_once_before_the_reply(make):
    from pipecat.frames.frames import TTSUpdateSettingsFrame
    brain = FakeBrain()
    brain.hardship_voice = True
    p = make(brain)
    for text in ("मेरी job चली गई है.", "हाँ."):
        await feed(p, VADUserStartedSpeakingFrame(), final(text), VADUserStoppedSpeakingFrame(), settle=0.2)
    kinds = [type(f).__name__ for f in p.pushed]
    assert kinds.count("TTSUpdateSettingsFrame") == 1
    assert kinds.index("TTSUpdateSettingsFrame") < kinds.index("LLMFullResponseStartFrame")
    assert next(f for f in p.pushed if isinstance(f, TTSUpdateSettingsFrame)).delta.pace == 0.9


async def test_no_pace_change_without_hardship(make):
    brain = FakeBrain()
    p = make(brain)
    await feed(p, VADUserStartedSpeakingFrame(), final("Haan, main pay kar dungi."), VADUserStoppedSpeakingFrame(),
               settle=0.2)
    assert not any(type(f).__name__ == "TTSUpdateSettingsFrame" for f in p.pushed)


class BridgeBrain(FakeBrain):
    async def respond(self, text, hint=None):
        self.turns.append(text)
        yield Sentence("जी।", bridge=True)
        yield Sentence("समय देने के लिए धन्यवाद।")


async def test_bridge_line_is_flushed_to_tts_at_once(make):
    """C42: Sarvam held the one-word bridge "जी।" 1.8 s until the next sentence; a flush follows it."""
    from emi_agent.providers import FlushTTSFrame
    p = make(BridgeBrain())
    await feed(p, VADUserStartedSpeakingFrame(), final("No. Thank you."), VADUserStoppedSpeakingFrame())
    out = [f for f in p.pushed if isinstance(f, (AggregatedTextFrame, FlushTTSFrame))]
    assert [type(f).__name__ for f in out] == ["AggregatedTextFrame", "FlushTTSFrame", "AggregatedTextFrame"]
    assert out[0].text.startswith("जी।")  # the flush comes right after the bridge, not after the reply


# C47: the model is slow (live call B004 19:51: 4.75 s of silence after an LLM timeout).
def _texts(p):
    from emi_agent.providers import FlushTTSFrame
    return [f.text.strip() if isinstance(f, AggregatedTextFrame) else "FLUSH"
            for f in p.pushed if isinstance(f, (AggregatedTextFrame, FlushTTSFrame))]


async def test_slow_model_gets_a_filler_first_then_the_reply(make):
    brain = FakeBrain(delay=0.3)
    p = make(brain, filler_after=0.1)
    await feed(p, VADUserStartedSpeakingFrame(), final("जी payment हो जाएगी."), VADUserStoppedSpeakingFrame(),
               settle=0.5)
    assert _texts(p) == ["जी, एक second।", "FLUSH", "Theek hai."]
    assert brain.fillers == 1
    assert sum(isinstance(f, LLMFullResponseStartFrame) for f in p.pushed) == 1  # one TTS response


async def test_fast_model_gets_no_filler(make):
    brain = FakeBrain(delay=0.0)
    p = make(brain, filler_after=0.2)
    await feed(p, VADUserStartedSpeakingFrame(), final("WhatsApp."), VADUserStoppedSpeakingFrame(), settle=0.4)
    assert _texts(p) == ["Theek hai."] and not hasattr(brain, "fillers")


async def test_filler_off_when_set_to_zero(make):
    brain = FakeBrain(delay=0.3)
    p = make(brain, filler_after=0)
    await feed(p, VADUserStartedSpeakingFrame(), final("Yes."), VADUserStoppedSpeakingFrame(), settle=0.5)
    assert _texts(p) == ["Theek hai."]


async def test_barge_in_before_the_filler_cancels_it(make):
    """The borrower speaks again while the model is still silent: the old reply and its filler timer
    are cancelled, so the filler never plays over the borrower."""
    brain = FakeBrain(delay=1.0)
    p = make(brain, filler_after=0.2)
    await feed(p, final("Haan ji, boliye."), VADUserStoppedSpeakingFrame(), settle=0.05)
    await feed(p, VADUserStartedSpeakingFrame(), final("Ek minute, main apna number dekh rahi hoon"), settle=0.4)
    assert any(isinstance(f, InterruptionFrame) for f in p.pushed)
    assert not hasattr(brain, "fillers") and _texts(p) == []  # 0.45 s in: the old timer would have fired


# Live call B003 22:25 (C67): the reply was fully written, its audio still playing, when the borrower cut in.
async def test_barge_in_on_audio_of_a_written_reply_tells_the_brain(make):
    brain = FakeBrain()
    p = make(brain)
    p.reply, p.bot_speaking = None, True
    await p._barge_in("बिल्कुल payment नहीं", "interim")
    assert getattr(brain, "playback_cuts", 0) == 1


# C69, live calls 4 Nov: Sarvam timed out and the Groq fallback answered ~4 s after the filler, in silence.
async def test_very_slow_model_gets_one_more_filler_then_the_reply(make):
    brain = FakeBrain(delay=0.5)
    p = make(brain, filler_after=0.1, filler_again=0.15)
    await feed(p, VADUserStartedSpeakingFrame(), final("Mera date of birth 18 December 1983."),
               VADUserStoppedSpeakingFrame(), settle=0.8)
    assert _texts(p) == ["जी, एक second।", "FLUSH", "बस एक moment और, देख रही हूँ।", "FLUSH", "Theek hai."]
    assert brain.fillers_again == 1


async def test_reply_before_the_second_filler_gets_none(make):
    brain = FakeBrain(delay=0.2)
    p = make(brain, filler_after=0.1, filler_again=0.4)
    await feed(p, VADUserStartedSpeakingFrame(), final("Yes."), VADUserStoppedSpeakingFrame(), settle=0.8)
    assert _texts(p) == ["जी, एक second।", "FLUSH", "Theek hai."] and not hasattr(brain, "fillers_again")


def test_filler_turn_reports_the_real_wait_to_the_answer():
    from emi_agent.latency import Turn, report
    t = Turn(vad_stop=10.0, stt_final=10.5, first_sentence=11.2, playback_start=11.5, filler=True, answer=15.3)
    assert t.stages_ms()["gap"] == 1500 and t.stages_ms()["to_answer"] == 5300
    row = {"interrupted": False, "filler": True, "stop_secs": 0.5, "ms": t.stages_ms()}
    assert "real wait to the answer on those turns: 5300 ms" in report([row])
