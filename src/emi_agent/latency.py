"""Per-turn voice latency (LLD 13): timestamps in, median and p90 out.

    python3 -m emi_agent.latency results/latency/<call>.jsonl [more files...]

The response gap is measured from the VAD stop event to playback start. VAD fires
`stop_secs` after the borrower's last sound, so what the borrower feels is gap + stop_secs;
the report shows both.
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MARKS = ("vad_stop", "stt_final", "first_sentence", "playback_start")


@dataclass
class Turn:
    """Monotonic seconds. first_sentence = Brain yielded its first complete sentence (TTS can start)."""
    vad_stop: float | None = None
    stt_final: float | None = None
    first_sentence: float | None = None
    playback_start: float | None = None
    stop_secs: float = 0.0
    interrupted: bool = False
    after_barge_in: bool = False  # reply right after a barge-in (Sarvam reconnects then)
    bridge: bool = False  # first audio was a code bridge line said while a tool ran
    filler: bool = False  # first audio was the slow-model filler (C47): the reply itself came later
    answer: float | None = None  # on a filler turn: when the reply's first sentence went to TTS (C69)

    def stages_ms(self) -> dict[str, float]:
        """Duration of each stage that has both ends, plus the total gap."""
        out = {}
        for a, b in zip(MARKS, MARKS[1:]):
            if getattr(self, a) is not None and getattr(self, b) is not None:
                out[f"{a}->{b}"] = round((getattr(self, b) - getattr(self, a)) * 1000)
        if self.vad_stop is not None and self.playback_start is not None:
            out["gap"] = round((self.playback_start - self.vad_stop) * 1000)
        if self.filler and self.vad_stop is not None and self.answer is not None:
            # Live calls 4 Nov (C69): the gap ended at the filler (1988 ms) but the answer came 5.3 s after.
            out["to_answer"] = round((self.answer - self.vad_stop) * 1000)
        return out


def append(turn: Turn, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({**asdict(turn), "ms": turn.stages_ms()}) + "\n")


def median_p90(values: list[float]) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    return statistics.median(values), statistics.quantiles(values, n=10, method="inclusive")[8]


def report(rows: list[dict]) -> str:
    complete = [r for r in rows if "gap" in r["ms"] and not r["interrupted"]]
    lines = [f"{len(rows)} turns, {len(complete)} complete (interrupted or missing marks excluded)"]
    fillers = sum(1 for r in complete if r.get("filler"))
    if fillers:  # honest reporting: there the gap ends at "जी, एक second", not at the answer (C47)
        lines.append(f"  {fillers} of them started with the slow-model filler: the gap is to the filler, not the reply")
        waits = [r["ms"]["to_answer"] for r in complete if r.get("filler") and "to_answer" in r["ms"]]
        if waits:  # C69: the real wait on those turns, to the answer (sent to TTS; audio ~0.3 s later)
            lines.append(f"  real wait to the answer on those turns: {', '.join(f'{w} ms' for w in waits)}")
    if not complete:
        return lines[0]
    stop = complete[0]["stop_secs"]
    keys = [f"{a}->{b}" for a, b in zip(MARKS, MARKS[1:])] + ["gap"]
    for k in keys:
        vals = [r["ms"][k] for r in complete if k in r["ms"]]
        if vals:
            med, p90 = median_p90(vals)
            lines.append(f"  {k:30s} median {med:6.0f} ms   p90 {p90:6.0f} ms   (n={len(vals)})")
    med, p90 = median_p90([r["ms"]["gap"] for r in complete])
    lines.append(f"  gap + VAD stop_secs ({stop:.2f}s)    median {med + stop * 1000:6.0f} ms   "
                 f"p90 {p90 + stop * 1000:6.0f} ms")
    return "\n".join(lines)


def main() -> None:
    rows = [json.loads(line) for p in sys.argv[1:] for line in Path(p).read_text().splitlines() if line]
    print(report(rows))


if __name__ == "__main__":
    main()
