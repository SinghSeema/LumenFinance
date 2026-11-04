"""Voice call: LiveKit audio -> VAD -> Deepgram STT -> Brain -> Sarvam TTS -> LiveKit.

    livekit-server --dev                                     # terminal 1
    python3 -m emi_agent.bot --profile hindi_bulbul          # terminal 2: joins the room, waits
    python3 scripts/serve_client.py                          # terminal 3: open http://localhost:8080

The Brain owns the conversation; this file only moves audio and text and decides when a turn
starts, when the borrower has interrupted, and when to hang up.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import aclosing
from datetime import date, datetime
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame, BotStartedSpeakingFrame, BotStoppedSpeakingFrame, EndTaskFrame,
    InterimTranscriptionFrame, InterruptionFrame, LLMFullResponseEndFrame,
    LLMFullResponseStartFrame, TranscriptionFrame, TTSSpeakFrame, VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.text.base_text_aggregator import AggregationType

from emi_agent import config as config_mod
from emi_agent.turn_gate import make_smart_turn
from emi_agent import latency, providers
from emi_agent.brain import _ASK_DOB, _ASK_LAST4, Brain, is_stop_request
from emi_agent.data import load_borrowers
from emi_agent.heard import has_number_or_month
from emi_agent.llm import build_llm
from emi_agent.speech_format import clean_for_tts


# Voice cannot wait out a 429: the SDK's retries honour Groq's retry-after (seconds of silence),
# so the primary falls over to the next model at once; the last fallback keeps one retry.
VOICE_OVERRIDES = ["llm.max_retries=0", "llm.timeout_secs=4"]


def voice_config(cfg: dict) -> dict:
    for spec in cfg["llm"].get("fallback") or []:
        spec.setdefault("max_retries", 0)  # never sit out a retry-after wait mid-call
    return cfg


# Words a sentence does not end on: "because", "aur", "double" (a number is coming).
_CONTINUES = {
    "because", "and", "but", "so", "or", "that", "if", "the", "my", "is", "to", "of", "double",
    "triple", "aur", "ki", "ke", "ka", "to", "toh", "lekin", "par", "kyunki", "kyonki", "ya",
    "matlab", "jo", "mera", "meri", "और", "कि", "के", "का", "की", "तो", "लेकिन", "पर", "क्योंकि",
    "या", "मतलब", "जो", "मेरा", "मेरी",
}


UNHEARD_SECS = 1.0  # a reply cut within this much playing was not heard (live call 16:03: cut 250 ms in)


def looks_unfinished(text: str) -> bool:
    """Deepgram punctuates a sentence it heard end ('Tuesday.'); fragments cut by a pause have no
    final mark ('जी मैं राहुल', 'right now', '7 4') or end on a joining word ('... Because')."""
    text = text.strip()
    if not text:
        return False
    last = text.split()[-1].strip(",;:").lower()
    return text[-1] not in ".?!।" or last.rstrip(".?!।") in _CONTINUES


LOW_WORD_CONFIDENCE = 0.6  # words below this are listed in the log


def confidence_note(results: list) -> str:
    """Deepgram's scores for one borrower turn, from the results it already sent (no extra
    request, no added latency): the lowest transcript confidence of the turn's finals, and each
    word under LOW_WORD_CONFIDENCE. Logging only; nothing decides on it yet."""
    alts = [r.channel.alternatives[0] for r in results
            if getattr(r, "channel", None) and r.channel.alternatives]
    if not alts:
        return ""
    conf = min(a.confidence for a in alts if a.confidence is not None)
    low = [f"{w.punctuated_word or w.word}({w.confidence:.2f})" for a in alts for w in (a.words or [])
           if w.confidence is not None and w.confidence < LOW_WORD_CONFIDENCE]
    # Deepgram returns detected languages only with language=multi: proof the mode is on.
    langs = sorted({lang for a in alts for lang in (getattr(a, "languages", None) or [])})
    return (f"confidence {conf:.2f}" + (f" | langs {','.join(langs)}" if langs else "")
            + (f" | low words: {' '.join(low)}" if low else ""))


class BrainProcessor(FrameProcessor):
    """Sits between STT and TTS. Turns final transcripts into Brain turns and sentences into speech."""

    def __init__(self, brain: Brain, config: dict, latency_path: Path):
        super().__init__()
        self.brain = brain
        self.stop_secs = config["turn"]["vad_stop_secs"]
        self.stt_wait = config["turn"]["stt_wait_secs"]
        # Setup B: Smart Turn already judged the turn, so the punctuation rule stays off.
        self.unfinished_wait = (0.0 if config["turn"]["detector"] == "smart_turn"
                                else config["turn"]["unfinished_wait_secs"])
        self.min_words = config["turn"]["min_words_interrupt"]
        self.latency_path = latency_path
        self.heard: list[str] = []      # final transcript pieces of the borrower's current turn
        self.pending = ""               # latest interim text Deepgram has not finalized yet
        self.lang_hint: str | None = None
        self.user_speaking = False
        self.bot_speaking = False
        self.bot_spoke = False          # any audio played since the current reply started
        self.reply: asyncio.Task | None = None
        self.wait: asyncio.Task | None = None
        self.turn: latency.Turn | None = None
        self.ended = False
        self.speech_started = 0.0      # when the borrower's current speech began (VAD)
        self.last_final = ("", 0.0)    # Deepgram can send the same final twice
        self.barged_in = False         # the next reply follows a barge-in
        self.speech_ended = 0.0        # when VAD last heard the borrower stop
        self.last_agent_text = ""      # what the agent said last, to judge a lone number (barge-in)
        self.greeting_playing = False  # a barge-in now means the borrower never heard the greeting
        t = config["turn"]
        self.silence_secs = (t["silence_after_barge_in_secs"], t["silence_prompts_secs"],
                             t["silence_close_secs"])
        self.silence: asyncio.Task | None = None  # the silence prompt timer
        self.nudged = False            # "can you hear me?" already said since the borrower last spoke
        self.bot_started_at = 0.0      # when the agent's current audio began
        self.heard_results: list = []  # Deepgram results behind self.heard, for confidence logging

    # ---- frames ----

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self.user_speaking = True
            self.speech_started = time.monotonic()
            await self._cancel_silence()
            self.turn = None  # the borrower kept talking; time from the next VAD stop
            await self._cancel_wait()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.user_speaking = False
            self.speech_ended = time.monotonic()
            # vad_stop_at: set by the Smart Turn gate, so time it held the turn counts too.
            stopped = getattr(frame, "vad_stop_at", None) or time.monotonic()
            self.turn = latency.Turn(vad_stop=stopped, stop_secs=self.stop_secs)
            await self._cancel_wait()
            if self.pending:  # words still in progress at Deepgram: wait for their final (capped)
                self.wait = self.create_task(self._start_after_wait())
            else:             # everything said is already final: reply now, or soon if unfinished
                await self._reply_now_or_after_pause()
        elif isinstance(frame, InterimTranscriptionFrame):
            self.pending = frame.text.strip()
            if self._agent_talking() and self._is_barge_in(frame.text):
                await self._barge_in(frame.text, "interim")
            return  # transcripts stop here; TTS must not see them
        elif isinstance(frame, TranscriptionFrame):
            await self._on_final(frame)
            return
        elif isinstance(frame, BotStartedSpeakingFrame):
            self.bot_speaking = self.bot_spoke = True
            self.bot_started_at = time.monotonic()
            if self.turn and self.turn.first_sentence and not self.turn.playback_start:
                self.turn.playback_start = time.monotonic()
                latency.append(self.turn, self.latency_path)
                if self.turn.after_barge_in:  # does the Sarvam reconnect ever delay a reply?
                    wait = (self.turn.playback_start - self.turn.first_sentence) * 1000
                    logger.info(f"after barge-in: first audio {wait:.0f} ms after first sentence")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.bot_speaking = False
            self.greeting_playing = False
            # The agent has finished: start the silence prompt timer, unless a barge-in already
            # started its shorter one or the borrower is mid-turn.
            if not (self.barged_in or self.user_speaking or self._agent_talking() or self.ended
                    or self.brain.should_hang_up):
                self._start_silence(self.silence_secs[2] if self.nudged else self.silence_secs[1])

        await self.push_frame(frame, direction)

    async def _on_final(self, frame: TranscriptionFrame) -> None:
        text, now = frame.text.strip(), time.monotonic()
        if not text or (text == self.last_final[0] and now - self.last_final[1] < 1.5):
            return  # empty, or a repeat of the final just received
        self.last_final = (text, now)
        self.pending = ""  # this final closes the segment the interims belonged to
        if self._agent_talking():
            if not self._is_barge_in(text):
                logger.info(f"borrower (backchannel, ignored)> {text}")
                return
            await self._barge_in(text, "final")
        self.heard.append(text)
        if getattr(frame, "result", None) is not None:
            self.heard_results.append(frame.result)
        if frame.language:
            self.lang_hint = str(frame.language.value).split("-")[0]
        if self.user_speaking:
            return  # more is coming; VAD stop starts the turn
        await self._cancel_wait()
        await self._reply_now_or_after_pause()  # the borrower has stopped; the words are now final

    def _agent_talking(self) -> bool:
        return self.bot_speaking or (self.reply is not None and not self.reply.done())

    def _is_barge_in(self, text: str) -> bool:
        """A stop request always interrupts. Anything else needs VAD to agree someone is speaking
        (live call 2026-11-04: one misheard interim, "2", with no speech, cut the greeting), and then
        enough words, or a lone number or month only when the agent just asked for one (DOB, last
        four). 'haan' and 'okay' never interrupt."""
        if is_stop_request(text):
            return True
        if not (self.user_speaking or time.monotonic() - self.speech_ended < 1.0):
            return False
        if len(text.split()) >= self.min_words:
            return True
        asked = _ASK_DOB.search(self.last_agent_text) or _ASK_LAST4.search(self.last_agent_text)
        return bool(asked) and has_number_or_month(text)

    # ---- turns ----

    async def _start_after_wait(self) -> None:
        """Cap on waiting for a pending final: after it, reply with the interim text as heard."""
        await asyncio.sleep(self.stt_wait)
        self.wait = None
        if self.user_speaking:
            return
        if self.pending:
            logger.info(f"no final after {self.stt_wait}s; using interim: {self.pending}")
            self.heard.append(self.pending)
            self.pending = ""
        await self._start_reply()

    async def _reply_now_or_after_pause(self) -> None:
        if self.unfinished_wait and looks_unfinished(" ".join(self.heard)):
            logger.info(f"sounds unfinished, waiting {self.unfinished_wait}s: {' '.join(self.heard)}")
            self.wait = self.create_task(self._start_after_pause())
        else:
            await self._start_reply()

    async def _start_after_pause(self) -> None:
        """The words so far look cut off mid-sentence: give the borrower a moment to go on.
        If they start speaking again, VAD start cancels this task and the turn continues."""
        await asyncio.sleep(self.unfinished_wait)
        self.wait = None
        if not self.user_speaking:
            await self._start_reply()

    async def _start_reply(self) -> None:
        text, self.heard = " ".join(self.heard), []
        results, self.heard_results = self.heard_results, []
        if not text or self.ended:
            return
        if self.turn:
            self.turn.stt_final = time.monotonic()  # last transcript of the turn is in
        logger.info(f"borrower> {text}")
        if note := confidence_note(results):
            logger.info(f"  stt {note}")
        await self._cancel_silence()
        self.nudged = False
        if self.turn:
            self.turn.after_barge_in, self.barged_in = self.barged_in, False
        self.bot_spoke = False
        self.reply = self.create_task(self._reply(text, self.turn, self.lang_hint))

    async def _reply(self, text: str, turn: latency.Turn | None, hint: str | None) -> None:
        """All sentences of one reply go to TTS as one LLM-style response, so Sarvam speaks them
        back to back. Separate TTSSpeakFrames made TTS wait for each sentence to finish playing
        before starting the next (0.2-0.9 s gaps, owner calls 2026-11-03)."""
        started = False
        self.last_agent_text = ""
        async with aclosing(self.brain.respond(text, hint)) as sentences:
            async for s in sentences:
                spoken = clean_for_tts(s.text)
                self.last_agent_text += spoken + " "
                if turn and turn.first_sentence is None:
                    turn.first_sentence = time.monotonic()
                    turn.bridge = s.bridge
                logger.info(f"agent> {spoken}" + (f"  [guard: {s.guard}]" if s.guard else "")
                            + ("  [bridge while a tool runs]" if s.bridge else ""))
                if s.guard:
                    logger.info(f"  guard blocked: {self.brain.state.guard_hits[-1]['original']}")
                if s.speech:
                    logger.info(f"  pre-TTS {list(s.speech)}")
                if spoken:
                    if not started:
                        await self.push_frame(LLMFullResponseStartFrame())
                        started = True
                    # Already a whole, guarded sentence: skip Pipecat's own sentence aggregator.
                    await self.push_frame(AggregatedTextFrame(spoken + " ", AggregationType.SENTENCE))
        if started:  # not reached on a barge-in: the interruption resets TTS instead
            await self.push_frame(LLMFullResponseEndFrame())
        if self.brain.should_hang_up:
            await self._wait_until_quiet()
            await self.end_call()

    async def _barge_in(self, text: str = "", source: str = "") -> None:
        heard_ms = (time.monotonic() - self.speech_started) * 1000 if self.user_speaking else None
        logger.info(f"barge-in on {source} {text!r}: stopping agent speech"
                    + (f" ({heard_ms:.0f} ms after the borrower started speaking)" if heard_ms
                       else " (VAD: borrower not speaking)"))
        if self.greeting_playing:
            self.greeting_playing = False
            self.brain.greeting_cut()
        if self.turn and self.turn.first_sentence and not self.turn.playback_start:
            self.turn.interrupted = True
            latency.append(self.turn, self.latency_path)
        if self.reply and not self.reply.done():
            played = time.monotonic() - self.bot_started_at if self.bot_speaking else 0.0
            if played < UNHEARD_SECS:
                self.brain.reply_unheard()  # e.g. cut 250 ms in: the apology was never heard (call 16:03)
            await self.cancel_task(self.reply)  # Brain records the cut-off turn (brain._end_turn)
        self.reply = None
        self.bot_speaking = False
        self.barged_in = True
        # Downstream only: TTS drops queued sentences, the output transport stops playing.
        await self.push_frame(InterruptionFrame())
        # If no borrower turn follows (a false barge-in), don't sit in silence.
        self._start_silence(self.silence_secs[0])

    # ---- silence ----

    def _start_silence(self, secs: float) -> None:
        if self.silence:
            self.silence.cancel()
        self.silence = self.create_task(self._on_silence(secs))

    async def _cancel_silence(self) -> None:
        if self.silence:
            task, self.silence = self.silence, None
            if task is not asyncio.current_task():
                await self.cancel_task(task)

    async def _on_silence(self, secs: float) -> None:
        """Nobody spoke for `secs`: say "can you hear me?" (or the greeting again if it was cut);
        after a second silence, close politely. Code-owned lines, no model request."""
        await asyncio.sleep(secs)
        if self.pending and not self.user_speaking:
            # An interim Deepgram never finalized, seconds ago (live call 2026-11-04): stale.
            logger.info(f"silence: dropping stale interim {self.pending!r}")
            self.pending = ""
        if (self.ended or self.user_speaking or self._agent_talking() or self.heard or self.pending
                or self.brain.should_hang_up):
            return
        close = self.nudged
        line = self.brain.silence_line(close=close)
        self.nudged, self.barged_in = True, False
        logger.info(f"silence {secs:.0f}s: agent> {line}")
        self.last_agent_text = line
        await self.push_frame(TTSSpeakFrame(line))
        if close:
            await self._wait_until_quiet()
            await self.end_call()

    async def _cancel_wait(self) -> None:
        if self.wait:
            await self.cancel_task(self.wait)
            self.wait = None

    async def _wait_until_quiet(self, quiet: float = 1.0, limit: float = 20.0) -> None:
        """Let the last sentence play out, and the borrower finish a last "thank you", before
        hanging up (the bot may pause between sentences)."""
        start, quiet_since = time.monotonic(), None
        while time.monotonic() - start < limit:
            now = time.monotonic()
            if self.bot_speaking or self.user_speaking:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            elif self.bot_spoke and now - quiet_since >= quiet:
                return
            elif not self.bot_spoke and now - start >= 5.0:
                return  # nothing was ever played
            await asyncio.sleep(0.1)

    # ---- call start and end ----

    async def greet(self) -> None:
        logger.info(f"agent> {self.brain.greeting}")
        self.greeting_playing = True
        self.last_agent_text = self.brain.greeting
        await self.push_frame(TTSSpeakFrame(self.brain.greeting))

    async def end_call(self) -> None:
        if self.ended:
            return
        self.finish()
        await self.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)

    def finish(self) -> None:
        """Validate and write the outcome once, whoever ends the call."""
        if self.ended:
            return
        self.ended = True
        record = self.brain.finish()
        logger.info(f"outcome: {record.model_dump_json()}")
        logger.info(f"tokens: {self.brain.usage} | by model: {self.brain.turns_by_provider}")


def agent_token(cfg: dict) -> str:
    from livekit import api

    lk = cfg["livekit"]
    return (api.AccessToken(lk["api_key"], lk["api_secret"])
            .with_identity(lk["agent_identity"]).with_name("Lumen Finance agent")
            .with_grants(api.VideoGrants(room_join=True, room=lk["room_name"]))
            .to_jwt())


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="hindi_bulbul")
    p.add_argument("--borrower", default="B001")
    p.add_argument("--set", action="append", default=[], dest="overrides")
    p.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = p.parse_args()

    config_mod.load_dotenv()
    overrides = VOICE_OVERRIDES + args.overrides  # the command line still wins
    cfg = config_mod.load(args.profile, overrides)
    cfg = voice_config(config_mod.load(args.profile, overrides, require_keys=[  # fail fast on a missing key
        "livekit", f"{cfg['stt']['provider']}_stt", f"{cfg['tts']['provider']}_tts",
        f"{cfg['llm']['provider']}_llm"]))
    borrower = load_borrowers()[args.borrower]
    brain = Brain(cfg, borrower, args.today, build_llm(cfg), calls_path=Path(cfg["behavior"]["calls_log"]))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    latency_path = Path("results/latency") / f"{stamp}-{args.borrower}.jsonl"
    logger.info(f"profile {args.profile} | llm {cfg['llm']['model']} | tts {cfg['tts']['model']} "
                f"{cfg['tts'].get('voice')} | {borrower.borrower_id} | today {args.today} | "
                f"room {cfg['livekit']['room_name']} | turn {cfg['turn']['detector']} "
                f"(VAD stop {cfg['turn']['vad_stop_secs']}s)")

    transport = providers.make_transport(cfg, agent_token(cfg))
    proc = BrainProcessor(brain, cfg, latency_path)
    gate = [make_smart_turn(cfg)] if cfg["turn"]["detector"] == "smart_turn" else []
    pipeline = Pipeline([
        transport.input(),
        providers.make_vad(cfg),
        *gate,
        providers.make_stt(cfg, borrower.first_name),
        proc,
        providers.make_tts(cfg),
        transport.output(),
    ])
    rate = cfg["tts"]["sample_rate"]
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=16000, audio_out_sample_rate=rate, enable_metrics=True))

    @transport.event_handler("on_first_participant_joined")
    async def on_joined(_transport, participant_id):
        logger.info(f"borrower joined: {participant_id}")
        await proc.greet()

    @transport.event_handler("on_participant_left")
    async def on_left(_transport, participant_id, reason):
        logger.info(f"borrower left: {participant_id} ({reason})")
        proc.finish()
        await task.cancel()

    await PipelineRunner(handle_sigint=True).run(task)
    proc.finish()  # Ctrl-C or a dropped connection still records an outcome
    if latency_path.exists():
        rows = [json.loads(line) for line in latency_path.read_text().splitlines()]
        logger.info("latency this call:\n" + latency.report(rows))


if __name__ == "__main__":
    asyncio.run(main())
