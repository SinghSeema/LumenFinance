"""Smart Turn gate (setup B, turn.detector: smart_turn). Sits between VAD and STT.

At each VAD stop it asks Smart Turn v3 (a local audio model) whether the borrower finished.
Finished: the stop frame goes on and the agent replies. Not finished: the stop is held. If the
borrower speaks again, the turn simply continues; if they stay silent for Smart Turn's own cap
(smart_turn_stop_secs), the held stop is released then. Every stop frame that leaves carries
`vad_stop_at`, the moment VAD heard silence, so latency includes any time held here.
"""
from __future__ import annotations

import time

from loguru import logger
from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
from pipecat.frames.frames import (
    InputAudioRawFrame, StartFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


class SmartTurnGate(FrameProcessor):
    def __init__(self, analyzer):
        super().__init__()
        self.analyzer = analyzer
        self.speaking = False
        self.held_since: float | None = None  # VAD stop time of a stop being held

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self.analyzer.set_sample_rate(frame.audio_in_sample_rate)
        elif isinstance(frame, InputAudioRawFrame):
            state = self.analyzer.append_audio(frame.audio, self.speaking)
            if state == EndOfTurnState.COMPLETE and self.held_since is not None:
                logger.info("smart turn: silence cap reached, ending the turn")
                await self._release()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.speaking = True
            if self.held_since is not None:
                self.held_since = None  # they went on: the turn never ended, so no new start
                return
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.speaking = False
            stopped = time.monotonic()
            state, _ = await self.analyzer.analyze_end_of_turn()
            took = (time.monotonic() - stopped) * 1000
            if state != EndOfTurnState.COMPLETE:
                logger.info(f"smart turn: not finished ({took:.0f} ms), holding the turn")
                self.held_since = stopped
                return
            logger.info(f"smart turn: finished ({took:.0f} ms)")
            frame.vad_stop_at = stopped

        await self.push_frame(frame, direction)

    async def _release(self) -> None:
        frame = VADUserStoppedSpeakingFrame()
        frame.vad_stop_at, self.held_since = self.held_since, None
        await self.push_frame(frame)


def make_smart_turn(config: dict):
    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

    turn = config["turn"]
    analyzer = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=turn["smart_turn_stop_secs"]))
    analyzer.update_vad_start_secs(turn["vad_start_secs"])
    return SmartTurnGate(analyzer)
