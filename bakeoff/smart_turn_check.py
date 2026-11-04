"""Smart Turn v3 on this laptop: verdict and time per check on cut-down clips (local, free).

    python3 -m bakeoff.smart_turn_check clip.wav:0.87 clip.wav ...   (clip[:seconds])
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
import wave

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

CHUNK = 320  # 10 ms at 16 kHz


async def check(analyzer, pcm: bytes) -> tuple[str, float]:
    analyzer.clear()
    for i in range(0, len(pcm), CHUNK):
        analyzer.append_audio(pcm[i:i + CHUNK].ljust(CHUNK, b"\0"), True)
    for _ in range(20):  # 200 ms of silence, as when VAD stop (0.2 s) fires
        analyzer.append_audio(bytes(CHUNK), False)
    t = time.monotonic()
    state, _ = await analyzer.analyze_end_of_turn()
    return state.name, (time.monotonic() - t) * 1000


async def main() -> None:
    analyzer = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=2.0))
    analyzer.set_sample_rate(16000)
    for arg in sys.argv[1:]:
        path, _, secs = arg.partition(":")
        w = wave.open(path)
        pcm = w.readframes(w.getnframes())
        if secs:
            pcm = pcm[:int(float(secs) * 32000) // 2 * 2]
        runs = [await check(analyzer, pcm) for _ in range(5)]
        name = path.rsplit("/", 1)[-1] + (f"[:{secs}s]" if secs else "")
        print(f"{name:22s} verdict {runs[-1][0]:10s} check {statistics.median(r[1] for r in runs):5.0f} ms "
              f"(median of 5, first {runs[0][1]:.0f} ms)")


if __name__ == "__main__":
    asyncio.run(main())
