"""Tuning: how long Deepgram streaming takes to deliver the final words after the speaker stops.

    python3 -m bakeoff.stt_latency clip1.wav clip2.wav --runs 3 --configs multi:300 hi:300 multi:100

Streams each 16 kHz mono WAV the way the bot does: 10 ms PCM chunks at real-time pace, 1 s of
silence before, silence after (a live mic never stops sending), and Finalize sent `--vad-stop`
seconds after the last voiced chunk, as Pipecat does at VAD stop. All times are measured from
the moment the last voiced chunk was sent. Paid: about (clip + 4.5 s) of Deepgram audio per run.
"""
from __future__ import annotations

import argparse
import asyncio
import audioop
import statistics
import time
import wave

from deepgram import AsyncDeepgramClient
from deepgram.environment import DeepgramClientEnvironment
from deepgram.core.events import EventType
from deepgram.listen.v1.types import ListenV1Finalize, ListenV1Results

from emi_agent import config as config_mod

CHUNK = 320  # 10 ms of 16 kHz 16-bit mono, what the LiveKit input hands the STT
LEAD_IN, TAIL = 1.0, 3.0


def load_clip(path: str) -> tuple[bytes, int]:
    """PCM and the byte offset just after the last voiced 10 ms chunk."""
    w = wave.open(path)
    assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, path
    pcm = w.readframes(w.getnframes())
    voiced = [i for i in range(0, len(pcm), CHUNK) if audioop.rms(pcm[i:i + CHUNK], 2) > 300]
    return pcm, voiced[-1] + CHUNK


async def one_run(client, opts: dict, pcm: bytes, speech_end: int, vad_stop: float) -> dict:
    events: list[tuple[float, ListenV1Results]] = []
    t0 = time.monotonic()
    async with client.listen.v1.connect(**opts) as conn:
        connect_ms = (time.monotonic() - t0) * 1000
        end_at: list[float] = []

        def on_msg(m):
            if isinstance(m, ListenV1Results):
                events.append((time.monotonic(), m))
        conn.on(EventType.MESSAGE, on_msg)
        listener = asyncio.create_task(conn.start_listening())
        audio = bytes(int(LEAD_IN * 32000)) + pcm + bytes(int(TAIL * 32000))
        end_byte = int(LEAD_IN * 32000) + speech_end
        start, finalized = time.monotonic(), False
        for n, i in enumerate(range(0, len(audio), CHUNK)):
            await asyncio.sleep(max(0.0, start + n * 0.01 - time.monotonic()))
            await conn.send_media(audio[i:i + CHUNK].ljust(CHUNK, b"\0"))
            if i + CHUNK == end_byte:
                end_at.append(time.monotonic())
            if end_at and not finalized and time.monotonic() >= end_at[0] + vad_stop:
                await conn.send_finalize(ListenV1Finalize(type="Finalize"))
                finalized = True
        await asyncio.sleep(0.5)
        listener.cancel()
    end = end_at[0]
    texted = [(t - end, m) for t, m in events if m.channel.alternatives[0].transcript.strip()]
    finals = [(dt, m) for dt, m in texted if m.is_final]
    words = lambda m: len(m.channel.alternatives[0].transcript.split())
    total = sum(words(m) for _, m in finals)
    # Words visible at VAD stop: finals so far plus the newest interim after them.
    seen, last_interim = 0, 0
    for dt, m in texted:
        if dt > vad_stop:
            break
        if m.is_final:
            seen, last_interim = seen + words(m), 0
        else:
            last_interim = words(m)
    pick = lambda pred: next((round(dt * 1000) for dt, m in texted if dt > -0.2 and pred(m)), None)
    return {
        "connect_ms": round(connect_ms),
        "final_done_ms": round(finals[-1][0] * 1000) if finals else None,
        "speech_final_ms": pick(lambda m: m.speech_final),
        "from_finalize_ms": pick(lambda m: getattr(m, "from_finalize", False)),
        "words_at_vad_stop": f"{seen + last_interim}/{total}",
        "text": " ".join(m.channel.alternatives[0].transcript for _, m in finals),
    }


def options(model: str, language: str, endpointing: int) -> dict:
    """The same query the bot's Pipecat service sends (see providers.make_stt)."""
    return {"model": model, "language": language, "smart_format": "false", "numerals": "true",
            "punctuate": "true", "interim_results": "true", "endpointing": str(endpointing),
            "encoding": "linear16", "channels": "1", "multichannel": "false",
            "sample_rate": "16000"}


def med(xs):
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):5.0f}" if xs else "    -"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--configs", nargs="+", default=["multi:300", "hi:300", "multi:100"],
                    help="language:endpointing_ms")
    ap.add_argument("--host", default="", help="e.g. api.eu.deepgram.com; default is Deepgram's US host")
    ap.add_argument("--vad-stop", type=float, default=0.5, help="seconds, as turn.vad_stop_secs")
    args = ap.parse_args()

    config_mod.load_dotenv()
    cfg = config_mod.load("hindi_bulbul")
    env = {}
    if args.host:
        env["environment"] = DeepgramClientEnvironment(
            base=f"https://{args.host}", production=f"wss://{args.host}", agent=f"wss://{args.host}")
    client = AsyncDeepgramClient(api_key=cfg["stt"]["deepgram_api_key"], **env)
    clips = {p: load_clip(p) for p in args.clips}
    rows: dict[str, list[dict]] = {c: [] for c in args.configs}
    for run in range(args.runs):
        for c in args.configs:  # alternate configs so a slow minute hits all of them
            lang, ep = c.split(":")
            for path, (pcm, speech_end) in clips.items():
                r = await one_run(client, options(cfg["stt"]["model"], lang, int(ep)), pcm,
                                  speech_end, args.vad_stop)
                rows[c].append(r)
                print(f"run {run + 1} {c:10s} {path.rsplit('/', 1)[-1]:10s} "
                      f"connect {r['connect_ms']:4d}  final done {r['final_done_ms']}  "
                      f"speech_final {r['speech_final_ms']}  from_finalize {r['from_finalize_ms']}  "
                      f"words at VAD stop {r['words_at_vad_stop']}  | {r['text']!r}", flush=True)
    print(f"\nms after the last voiced chunk; Finalize sent at +{args.vad_stop * 1000:.0f} ms")
    print("config      n   final done  speech_final  from_finalize  connect")
    for c, rs in rows.items():
        print(f"{c:10s} {len(rs):2d}   {med(r['final_done_ms'] for r in rs)}       "
              f"{med(r['speech_final_ms'] for r in rs)}         "
              f"{med(r['from_finalize_ms'] for r in rs)}        {med(r['connect_ms'] for r in rs)}")


if __name__ == "__main__":
    asyncio.run(main())
