"""Pipecat STT, TTS, VAD and transport from config. The LLM is not here: the Brain calls it (llm.py)."""
from __future__ import annotations

import dataclasses
from typing import Any

from emi_agent import keyterms


def make_stt(config: dict, first_name: str | None = None) -> Any:
    stt_cfg = config["stt"]
    provider = stt_cfg["provider"]

    if provider == "deepgram":
        from pipecat.services.deepgram.stt import DeepgramSTTService, DeepgramSTTSettings

        api_key = stt_cfg.get("deepgram_api_key") or _missing("DEEPGRAM_API_KEY")
        settings = DeepgramSTTSettings(
            model=stt_cfg["model"],
            language=stt_cfg.get("language", "multi"),
            # smart_format rewrites "7th November 2026" as US-order "11/07/2026"; numerals keeps the
            # spoken order ("7th of November") and still gives digits for "7743" (checked 2026-11-03).
            smart_format=False,
            numerals=True,
            punctuate=True,
            interim_results=True,
            endpointing=stt_cfg.get("endpointing", 300),
        )
        terms = keyterms.for_call(stt_cfg.get("keyterms", "none"), first_name)
        if terms:
            settings.keyterm = terms
        host = stt_cfg.get("host") or ""  # empty = Deepgram's default (US) host
        service = DeepgramSTTService(api_key=api_key, settings=settings, base_url=host)
        # Pipecat logs "Language string 'multi' is not a recognized Language code ... passed to the
        # service as-is": its own enum lacks "multi", the string is still sent. This line shows the
        # query Deepgram actually gets (owner asked, 2026-11-04).
        q = service._build_connect_kwargs()
        from loguru import logger
        logger.info(f"deepgram query: model={q.get('model')} language={q.get('language')} "
                    f"keyterms={len(q.get('keyterm') or [])} numerals={q.get('numerals')} "
                    f"endpointing={q.get('endpointing')} host={host or 'default'}")
        return service

    raise ValueError(f"Unknown STT provider: {provider!r}")


def make_tts(config: dict) -> Any:
    tts_cfg = config["tts"]
    provider = tts_cfg["provider"]

    if provider == "sarvam":
        from pipecat.services.sarvam.tts import SarvamTTSService

        api_key = tts_cfg.get("sarvam_api_key") or _missing("SARVAM_API_KEY")
        _register_sarvam_model(tts_cfg["model"])
        return SarvamTTSService(
            api_key=api_key,
            sample_rate=tts_cfg.get("sample_rate", 16000),
            settings=SarvamTTSService.Settings(
                model=tts_cfg["model"], voice=tts_cfg["voice"], language=tts_cfg["language"]),
        )

    if provider == "cartesia":
        from pipecat.services.cartesia.tts import CartesiaTTSService

        api_key = tts_cfg.get("cartesia_api_key") or _missing("CARTESIA_API_KEY")
        return CartesiaTTSService(
            api_key=api_key,
            model=tts_cfg.get("model", "sonic-2"),
            sample_rate=tts_cfg.get("sample_rate", 16000),
        )

    raise ValueError(f"Unknown TTS provider: {provider!r}")


def _register_sarvam_model(model: str) -> None:
    """Pipecat 0.0.108 only knows bulbul v2/v3-beta/v3 and rejects other names before any network
    call. Sarvam's API also accepts bulbul:v4-flash (checked 2026-11-03), so add it with v3's
    settings. Speaker names are not checked by Pipecat; Sarvam rejects unknown ones."""
    from pipecat.services.sarvam import tts as sarvam

    if model not in sarvam.TTS_MODEL_CONFIGS and model == "bulbul:v4-flash":
        sarvam.TTS_MODEL_CONFIGS[model] = dataclasses.replace(
            sarvam.TTS_MODEL_CONFIGS["bulbul:v3"], speakers=())


def make_vad(config: dict) -> Any:
    """A pipeline VADProcessor; VAD on the transport is deprecated in Pipecat 0.0.108."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.processors.audio.vad_processor import VADProcessor

    turn = config["turn"]
    return VADProcessor(vad_analyzer=SileroVADAnalyzer(
        params=VADParams(stop_secs=turn["vad_stop_secs"], start_secs=turn["vad_start_secs"])))


def make_transport(config: dict, token: str) -> Any:
    from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

    lk = config["livekit"]
    url = lk.get("url") or _missing("LIVEKIT_URL")
    params = LiveKitParams(audio_in_enabled=True, audio_out_enabled=True)
    return LiveKitTransport(url=url, token=token, room_name=lk["room_name"], params=params)


def _missing(env_name: str) -> str:
    raise EnvironmentError(
        f"{env_name} is not set. Add it to your .env file (see .env.example)."
    )
