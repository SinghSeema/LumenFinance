"""SmartTurnGate (setup B): what reaches STT and the Brain at VAD stop. Fake analyzer, no model."""
import pytest
from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
from pipecat.frames.frames import (
    InputAudioRawFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

from emi_agent.turn_gate import SmartTurnGate

C, I = EndOfTurnState.COMPLETE, EndOfTurnState.INCOMPLETE


class FakeAnalyzer:
    def __init__(self, verdicts, cap_after=None):
        self.verdicts, self.cap_after, self.silent = list(verdicts), cap_after, 0

    def append_audio(self, buffer, is_speech):
        self.silent = 0 if is_speech else self.silent + 1
        return C if self.cap_after and self.silent >= self.cap_after else I

    async def analyze_end_of_turn(self):
        return self.verdicts.pop(0), None


async def _noop():
    return None


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(FrameProcessor, "process_frame", lambda self, f, d: _noop())

    def _make(analyzer):
        g = SmartTurnGate(analyzer)
        g.pushed = []

        async def push(frame, direction=None):
            g.pushed.append(frame)
        g.push_frame = push
        return g
    return _make


def audio():
    return InputAudioRawFrame(b"\0\0" * 160, 16000, 1)


def stops(g):
    return [f for f in g.pushed if isinstance(f, VADUserStoppedSpeakingFrame)]


async def feed(g, *frames):
    for f in frames:
        await g.process_frame(f, None)


async def test_finished_turn_passes_the_stop_with_its_vad_time(gate):
    g = gate(FakeAnalyzer([C]))
    await feed(g, VADUserStartedSpeakingFrame(), audio(), VADUserStoppedSpeakingFrame())
    assert len(stops(g)) == 1 and stops(g)[0].vad_stop_at > 0


async def test_unfinished_turn_is_held_and_continues_when_they_speak_again(gate):
    """'जी मैं राहुल' ... 'बोल रहा हूं.': one turn, one start and one stop downstream."""
    g = gate(FakeAnalyzer([I, C]))
    await feed(g, VADUserStartedSpeakingFrame(), audio(), VADUserStoppedSpeakingFrame())
    assert stops(g) == []
    await feed(g, VADUserStartedSpeakingFrame(), audio(), VADUserStoppedSpeakingFrame())
    starts = [f for f in g.pushed if isinstance(f, VADUserStartedSpeakingFrame)]
    assert len(starts) == 1 and len(stops(g)) == 1


async def test_held_turn_ends_at_the_silence_cap_with_the_first_vad_time(gate):
    g = gate(FakeAnalyzer([I], cap_after=3))
    await feed(g, VADUserStartedSpeakingFrame(), audio(), VADUserStoppedSpeakingFrame())
    held_at = g.held_since
    await feed(g, audio(), audio())
    assert stops(g) == []
    await feed(g, audio())
    assert len(stops(g)) == 1 and stops(g)[0].vad_stop_at == held_at
