"""Audition Sarvam Bulbul speakers by ear, and probe whether a model name is accepted.

Paid: one Sarvam REST call per (speaker, line). Prints the character count first.
Writes WAVs to results/tts_audition/<model>/<speaker>_<line>.wav

    python3 -m bakeoff.tts_audition --speakers priya neha kavya ishita shreya
    python3 -m bakeoff.tts_audition --probe-model bulbul:v4
"""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

URL = "https://api.sarvam.ai/text-to-speech"

# Same sentences in the scripts the agent may send (D-11): roman, devanagari, mixed.
LINES = {  # name: (language code, text)
    "greet_roman": ("hi-IN", "Namaste, main Lumen Finance ki virtual assistant bol rahi hoon. "
                             "Kya meri baat Rahul ji se ho rahi hai?"),
    "emi_roman": ("hi-IN", "Aapki EMI, chaudah hazaar paanch sau rupaye, paanch November ko due hai."),
    "emi_deva": ("hi-IN", "आपकी EMI, चौदह हज़ार पाँच सौ रुपये, पाँच नवंबर को due है।"),
    "emi_en": ("en-IN", "Your EMI of fourteen thousand five hundred rupees is due on the fifth of November."),
}


def synth(client: httpx.Client, key: str, lang: str, text: str, speaker: str, model: str) -> bytes:
    resp = client.post(
        URL,
        headers={"api-subscription-key": key},
        json={
            "text": text,
            "target_language_code": lang,
            "speaker": speaker,
            "model": model,
            "speech_sample_rate": 16000,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:2000]}")
    return base64.b64decode(resp.json()["audios"][0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bulbul:v3")
    ap.add_argument("--speakers", nargs="*", default=[])
    ap.add_argument("--lines", nargs="*", default=list(LINES))
    ap.add_argument("--probe-model", help="send one short line with this model name and report")
    args = ap.parse_args()

    load_dotenv()
    key = os.environ.get("SARVAM_API_KEY") or exit("SARVAM_API_KEY is not set")

    with httpx.Client() as client:
        if args.probe_model:
            text = "Namaste."
            print(f"Probing {args.probe_model!r} ({len(text)} chars)")
            for speaker in ["priya", "shubh"]:
                try:
                    audio = synth(client, key, "hi-IN", text, speaker, args.probe_model)
                    print(f"  {speaker}: accepted, {len(audio)} bytes")
                    break
                except RuntimeError as e:
                    print(f"  {speaker}: rejected: {e}")
            return

        jobs = [(s, n) for s in args.speakers for n in args.lines]
        chars = sum(len(LINES[n][1]) for _, n in jobs)
        print(f"{len(jobs)} requests, {chars} characters")
        out = Path("results/tts_audition") / args.model.replace(":", "_")
        out.mkdir(parents=True, exist_ok=True)
        for speaker, name in jobs:
            try:
                path = out / f"{speaker}_{name}.wav"
                lang, text = LINES[name]
                path.write_bytes(synth(client, key, lang, text, speaker, args.model))
                print(f"  wrote {path}")
            except RuntimeError as e:
                print(f"  {speaker}/{name} failed: {e}")


if __name__ == "__main__":
    main()
