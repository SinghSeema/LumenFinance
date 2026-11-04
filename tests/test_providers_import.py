"""
Phase 1 gate: confirm every provider class and the LiveKit transport import
correctly from pipecat 0.0.108. Does not require API keys.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_deepgram_stt_importable():
    from pipecat.services.deepgram.stt import DeepgramSTTService, DeepgramSTTSettings
    assert DeepgramSTTService
    assert DeepgramSTTSettings


def test_sarvam_stt_not_a_valid_provider():
    """Sarvam STT was dropped (batch-only, deprecated SDK). make_stt must reject it."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from emi_agent import providers
    import pytest
    with pytest.raises(ValueError, match="Unknown STT provider"):
        providers.make_stt({"stt": {"provider": "sarvam"}})


def test_sarvam_tts_importable():
    from pipecat.services.sarvam.tts import SarvamTTSService
    assert SarvamTTSService


def test_cartesia_tts_importable():
    from pipecat.services.cartesia.tts import CartesiaTTSService
    assert CartesiaTTSService


def test_livekit_transport_importable():
    from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
    assert LiveKitTransport
    assert LiveKitParams


def test_silero_vad_importable():
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    assert SileroVADAnalyzer


def test_livekit_transport_constructor_signature():
    """The transport takes url, token, room_name — confirm the signature."""
    import inspect
    from pipecat.transports.livekit.transport import LiveKitTransport
    sig = inspect.signature(LiveKitTransport.__init__)
    params = list(sig.parameters)
    assert "url" in params
    assert "token" in params
    assert "room_name" in params


def test_providers_module_importable():
    from emi_agent import providers
    assert hasattr(providers, "make_stt")
    assert hasattr(providers, "make_tts")
    assert hasattr(providers, "make_transport")
