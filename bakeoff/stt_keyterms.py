"""Does Deepgram keyterm prompting help on Hindi numbers and dates? (issues log T4, V8-V10)

    python3 -m bakeoff.stt_keyterms make-clips          # TTS borrower answers (paid, asks first)
    python3 -m bakeoff.stt_keyterms record              # the owner reads the same lines (free)
    python3 -m bakeoff.stt_keyterms run --sets none dates full

Scores what matters to the call, not word error: does our own parser (heard.find_dob /
find_last4) get the right value from the transcript, and do lines with no number stay free of
numbers. Each clip is streamed the way the bot sends audio (bakeoff/stt_latency.one_run): real
time, 10 ms chunks, Finalize 0.5 s after speech, nova-3 multi, the bot's query options.

TTS clips are cleaner than a phone mic: a signal, not the answer. The owner's recordings are.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

OUT = Path("results/stt_keyterms")
CLIPS = OUT / "clips"
TODAY = date(2026, 11, 4)
FIRST_NAME = "Priya"

# (id, kind, expected, text). Reference texts all parse correctly (checked by test_reference_lines).
LINES = [
    ("d01", "dob", "1994-07-22", "बाईस जुलाई उन्नीस सौ चौरानवे"),
    ("d02", "dob", "1994-07-22", "मेरी date of birth बाईस July उन्नीस सौ चौरानवे है"),
    ("d03", "dob", "1988-03-15", "जी, पंद्रह मार्च उन्नीस सौ अट्ठासी"),
    ("d04", "dob", "1985-11-09", "Date of birth है नौ नवंबर उन्नीस सौ पचासी"),
    ("d05", "dob", "1991-08-27", "सत्ताईस अगस्त उन्नीस सौ इक्यानवे"),
    ("d06", "dob", "1990-01-26", "छब्बीस January nineteen ninety"),
    ("d07", "dob", "1994-07-22", "twenty second July nineteen ninety four"),
    ("d08", "dob", "1986-02-28", "अट्ठाईस फ़रवरी उन्नीस सौ छियासी"),
    ("d09", "dob", "1999-12-21", "इक्कीस दिसंबर उन्नीस सौ निन्यानवे"),
    ("d10", "dob", "2001-06-14", "चौदह जून दो हज़ार एक"),
    ("d11", "dob", "1992-10-16", "सोलह अक्टूबर उन्नीस सौ बानवे"),
    ("d12", "dob", "1987-09-23", "तेईस सितंबर उन्नीस सौ सत्तासी"),
    ("l01", "last4", "7743", "सात सात चार तीन"),
    ("l02", "last4", "3367", "last four digits हैं तीन तीन छह सात"),
    ("l03", "last4", "4821", "चार आठ दो एक"),
    ("l04", "last4", "9052", "नौ शून्य पाँच दो"),
    ("l05", "last4", "6054", "six zero five four"),
    ("l06", "last4", "8819", "आठ आठ एक नौ"),
    ("n01", "none", "", "हाँ जी, मैं प्रिया बोल रही हूँ"),
    ("n02", "none", "", "Hello? हाँ, बोलिए"),
    ("n03", "none", "", "अभी मैं busy हूँ, बाद में call कीजिए"),
    ("n04", "none", "", "मैंने तो already pay कर दिया था UPI से"),
    ("n05", "none", "", "आप कौन बोल रहे हैं? Lumen Finance से?"),
    ("n06", "none", "", "ठीक है, ज़रा रुकिए"),
    ("n07", "none", "", "नहीं, अभी पैसे नहीं हैं, सैलरी आने दीजिए"),
]

VOICES = [  # (dir name, engine, model, voice). Two Sarvam voices and Cartesia's Hinglish speaker.
    ("simran", "sarvam", "bulbul:v4-flash", "simran_enhi_customer"),
    ("ishita", "sarvam", "bulbul:v4-flash", "ishita_enhi_customer"),
    ("arushi", "cartesia", "sonic-3.6", "95d51f79-c397-46f9-b49a-23763d3eaa2d"),
]


def score(kind: str, expected: str, text: str) -> tuple[bool, str]:
    """(correct, what our parser got)."""
    from emi_agent.heard import _tokens, find_dob, find_last4
    if kind == "dob":
        got = find_dob([text], TODAY)[0]
        return (got is not None and got.isoformat() == expected), str(got)
    if kind == "last4":
        got = find_last4(text)
        return got == expected, str(got)
    nums = [t[0] for t in _tokens(text) if not t[1]]  # everyday words ("दो" = give) excluded
    return not nums, ",".join(nums) or "-"


def make_clips(yes: bool) -> None:
    import httpx
    from dotenv import load_dotenv
    from bakeoff.tts_script_check import cartesia, sarvam
    load_dotenv()
    chars = sum(len(t) for *_, t in LINES)
    print(f"{len(LINES)} lines x {len(VOICES)} voices = {len(LINES) * len(VOICES)} TTS requests, "
          f"{chars * len(VOICES)} characters in all (estimate: well under 1 USD).")
    if not yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return
    keys = {"sarvam": os.environ["SARVAM_API_KEY"], "cartesia": os.environ["CARTESIA_API_KEY"]}
    with httpx.Client() as client:
        for name, engine, model, voice in VOICES:
            d = CLIPS / name
            d.mkdir(parents=True, exist_ok=True)
            synth = sarvam if engine == "sarvam" else cartesia
            for lid, _, _, text in LINES:
                p = d / f"{lid}.wav"
                if not p.exists():
                    p.write_bytes(synth(client, keys[engine], model, voice, "mixed", text))
            print(f"{name}: {len(LINES)} clips in {d}")


def record() -> None:
    """The owner reads each line into the default mic: 16 kHz mono, 6 s per line."""
    d = CLIPS / "owner"
    d.mkdir(parents=True, exist_ok=True)
    print("Read each line naturally, as you would on a call. Enter starts a 6 s recording; "
          "type s then Enter to skip a line.\n")
    for lid, _, _, text in LINES:
        if input(f"[{lid}] {text}\n  Enter to record, s to skip: ").strip().lower() == "s":
            continue
        subprocess.run(["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", "6",
                        str(d / f"{lid}.wav")], check=True)
    print(f"Saved to {d}")


async def run(sets: list[str], voices: list[str], concurrency: int) -> None:
    from deepgram import AsyncDeepgramClient
    from deepgram.environment import DeepgramClientEnvironment
    from bakeoff.stt_latency import load_clip, one_run, options
    from emi_agent import config as config_mod, keyterms

    config_mod.load_dotenv()
    cfg = config_mod.load("hindi_bulbul")
    stt = cfg["stt"]
    host = stt.get("host") or ""
    env = {"environment": DeepgramClientEnvironment(
        base=f"https://{host}", production=f"wss://{host}", agent=f"wss://{host}")} if host else {}
    client = AsyncDeepgramClient(api_key=stt["deepgram_api_key"], **env)
    lines = {lid: (kind, exp, text) for lid, kind, exp, text in LINES}
    jobs = [(v, s, lid, CLIPS / v / f"{lid}.wav") for s in sets for v in voices for lid in lines
            if (CLIPS / v / f"{lid}.wav").exists()]
    audio_s = sum(Path(p).stat().st_size / 32000 + 4.5 for *_, p in jobs)
    print(f"{len(jobs)} streams, about {audio_s / 60:.1f} min of Deepgram audio "
          f"(estimate: well under 1 USD, from the free credit)", flush=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(v, s, lid, path):
        kind, exp, _ = lines[lid]
        opts = options(stt["model"], stt["language"], stt["endpointing"])
        terms = keyterms.for_call(s, FIRST_NAME)
        if terms:
            opts["keyterm"] = terms
        pcm, end = load_clip(str(path))
        async with sem:
            r = await one_run(client, opts, pcm, end, 0.5)
        ok, got = score(kind, exp, r["text"])
        return {"voice": v, "set": s, "id": lid, "kind": kind, "expected": exp, "ok": ok,
                "parsed": got, "text": r["text"], "final_done_ms": r["final_done_ms"]}

    rows = await asyncio.gather(*(one(*j) for j in jobs))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{stamp}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = render(rows, sets, voices)
    (OUT / f"{stamp}.txt").write_text(report)
    print(report)
    print(f"saved {OUT / stamp}.txt and .jsonl")


def render(rows: list[dict], sets: list[str], voices: list[str]) -> str:
    out = ["Correct values parsed from the transcript (none = no number heard on a line without one)",
           f"{'set':6s} {'voice':7s} {'dob':>6s} {'last4':>6s} {'none':>6s}   final_done ms (median)"]
    for s in sets:
        for v in voices + ["ALL"]:
            rs = [r for r in rows if r["set"] == s and (v == "ALL" or r["voice"] == v)]
            if not rs:
                continue
            cell = lambda k: (lambda xs: f"{sum(x['ok'] for x in xs)}/{len(xs)}")(
                [r for r in rs if r["kind"] == k])
            ms = [r["final_done_ms"] for r in rs if r["final_done_ms"] is not None]
            out.append(f"{s:6s} {v:7s} {cell('dob'):>6s} {cell('last4'):>6s} {cell('none'):>6s}   "
                       f"{statistics.median(ms) if ms else '-'}")
        out.append("")
    out.append("Misses (set voice id: expected -> parsed | transcript)")
    for r in sorted(rows, key=lambda r: (r["id"], r["voice"], r["set"])):
        if not r["ok"]:
            out.append(f"  {r['set']:5s} {r['voice']:7s} {r['id']}: {r['expected'] or 'no number'} -> "
                       f"{r['parsed']} | {r['text']!r}")
    return "\n".join(out) + "\n"


def main() -> None:
    sys.path.insert(0, "src")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-clips")
    m.add_argument("--yes", action="store_true")
    sub.add_parser("record")
    r = sub.add_parser("run")
    r.add_argument("--sets", nargs="+", default=["none", "dates", "full"])
    r.add_argument("--voices", nargs="+", default=[v[0] for v in VOICES] + ["owner"])
    r.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    if a.cmd == "make-clips":
        make_clips(a.yes)
    elif a.cmd == "record":
        record()
    else:
        asyncio.run(run(a.sets, a.voices, a.concurrency))


if __name__ == "__main__":
    main()
