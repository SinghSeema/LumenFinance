"""Blind listening check: which TTS input script reads best, on Sarvam Bulbul and Cartesia Sonic (D-11, D-05).

Same real agent lines in three forms: mixed (Hindi in Devanagari, English terms in Latin), deva
(everything in Devanagari) and roman. Plus one English line, and one line in the mix Cartesia's docs
list as unsupported (Devanagari + romanised Hindi), to hear what that claim means in practice.

Paid: one request per (voice, line). Prints the character count first and asks to proceed.
Writes clip WAVs with neutral names, a hidden key.csv and a listen.html page:

    python3 -m bakeoff.tts_script_check            # then open results/tts_script_check/<run>/listen.html
"""
from __future__ import annotations

import base64
import csv
import os
import random
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
CARTESIA_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2026-08-14"

VOICES = [  # (engine, model, voice id, label). All feminine (D-20).
    ("sarvam", "bulbul:v4-flash", "simran_enhi_customer", "simran"),   # current placeholder
    ("sarvam", "bulbul:v4-flash", "ishita_enhi_customer", "ishita"),
    ("cartesia", "sonic-3.6", "5283efe8-07d1-4e3a-b615-2ae4a81c1b73", "kriti"),   # hi-IN native
    ("cartesia", "sonic-3.6", "95d51f79-c397-46f9-b49a-23763d3eaa2d", "arushi"),  # "Hinglish Speaker"
]

# Lines adapted from real agent turns (results/*sarvam-v4-dev5). Numbers already in words, as the
# formatter writes them, so Cartesia normalization is off and Sarvam gets the same text.
LINES = {  # line: {form: text}
    "L1_verify": {
        "mixed": "बस आगे बढ़ने से पहले मुझे आपकी पहचान verify करनी होगी। क्या आप अपनी date of birth "
                 "या registered mobile number के last four digits बता सकते हैं?",
        "deva": "बस आगे बढ़ने से पहले मुझे आपकी पहचान वेरिफाई करनी होगी। क्या आप अपनी डेट ऑफ बर्थ "
                "या रजिस्टर्ड मोबाइल नंबर के लास्ट फोर डिजिट्स बता सकते हैं?",
        "roman": "Bas aage badhne se pehle mujhe aapki pehchaan verify karni hogi. Kya aap apni date of "
                 "birth ya registered mobile number ke last four digits bata sakte hain?",
    },
    "L2_emi": {
        "mixed": "आपकी personal loan की बारह हज़ार पाँच सौ रुपये की EMI पाँच नवंबर को due है।",
        "deva": "आपकी पर्सनल लोन की बारह हज़ार पाँच सौ रुपये की ई एम आई पाँच नवंबर को ड्यू है।",
        "roman": "Aapki personal loan ki barah hazaar paanch sau rupaye ki EMI paanch November ko due hai.",
        "unsupported": "आपकी personal loan ki बारह हज़ार पाँच सौ rupaye की EMI paanch नवंबर ko due है।",
        "english": "Your personal loan EMI of twelve thousand five hundred rupees is due on the fifth of November.",
    },
    "L3_link": {
        "mixed": "मैं आपको SMS पर payment link भेज देती हूँ, जिससे आप UPI से pay कर सकें।",
        "deva": "मैं आपको एसएमएस पर पेमेंट लिंक भेज देती हूँ, जिससे आप यूपीआई से पे कर सकें।",
        "roman": "Main aapko SMS par payment link bhej deti hoon, jisse aap UPI se pay kar sakein.",
    },
    "L4_readback": {
        "mixed": "मैंने सात सात चार तीन सुना, क्या ये last four digits सही हैं?",
        "deva": "मैंने सात सात चार तीन सुना, क्या ये लास्ट फोर डिजिट्स सही हैं?",
        "roman": "Maine saat saat chaar teen suna, kya ye last four digits sahi hain?",
    },
    "L5_hardship": {
        "mixed": "मैं समझ सकती हूँ कि अभी हालात मुश्किल हैं। मेरे एक colleague आपको जल्दी call करेंगे।",
        "deva": "मैं समझ सकती हूँ कि अभी हालात मुश्किल हैं। मेरे एक कलीग आपको जल्दी कॉल करेंगे।",
        "roman": "Main samajh sakti hoon ki abhi haalaat mushkil hain. Mere ek colleague aapko jaldi call karenge.",
    },
}


# Number style A/B (owner, 2026-11-04): the same Hindi sentence with English vs Hindi number words.
# Text is what speech_format produces for number_lang 'hi-en' vs 'hi'.
NUMBER_LINES = {
    "N1_amount": {
        "en_numbers": "आपकी personal loan की twelve thousand five hundred rupees की EMI twenty-fifth November को due है।",
        "hi_numbers": "आपकी personal loan की बारह हज़ार पाँच सौ रुपये की EMI पच्चीस नवंबर को due है।",
    },
    "N2_overdue": {
        "en_numbers": "आपकी EMI twentieth October को due थी, और अब fourteen days हो गए हैं।",
        "hi_numbers": "आपकी EMI बीस अक्टूबर को due थी, और अब चौदह दिन हो गए हैं।",
    },
    "N3_readback": {
        "en_numbers": "मैंने seven, seven, four, three सुना, क्या ये सही है?",
        "hi_numbers": "मैंने सात, सात, चार, तीन सुना, क्या ये सही है?",
    },
    "N4_dob": {
        "en_numbers": "आपकी date of birth fifteen March nineteen eighty-eight है, सही?",
        "hi_numbers": "आपकी date of birth पंद्रह मार्च उन्नीस सौ अट्ठासी है, सही?",
    },
    "N5_hinglish": {
        "en_numbers": "Aapki twelve thousand five hundred rupees ki EMI parson due hai.",
        "hi_numbers": "Aapki barah hazaar paanch sau rupaye ki EMI parson due hai.",
    },
}


def _locale(form: str) -> str:
    return "en-IN" if form == "english" else "hi-IN"


def sarvam(client: httpx.Client, key: str, model: str, voice: str, form: str, text: str) -> bytes:
    r = client.post(SARVAM_URL, headers={"api-subscription-key": key}, timeout=30, json={
        "text": text, "target_language_code": _locale(form), "speaker": voice, "model": model,
        "speech_sample_rate": 16000})
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    return base64.b64decode(r.json()["audios"][0])


def cartesia(client: httpx.Client, key: str, model: str, voice: str, form: str, text: str) -> bytes:
    # Romanised Hindi uses en-IN normalization per Cartesia's table; ours has no digits, so "off"
    # is equivalent and keeps the formatter as the only normalizer.
    r = client.post(CARTESIA_URL, timeout=30, headers={
        "Authorization": f"Bearer {key}", "Cartesia-Version": CARTESIA_VERSION}, json={
        "model_id": model, "transcript": text, "voice": {"id": voice}, "locale": _locale(form),
        "normalization": "off",
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 16000}})
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    return fix_wav_sizes(r.content)


def fix_wav_sizes(b: bytes) -> bytes:
    """Cartesia's streamed WAV header carries placeholder sizes (players read ~37 hours); set real ones."""
    import struct
    b = bytearray(b)
    i = b.find(b"data")
    struct.pack_into("<I", b, 4, len(b) - 8)
    struct.pack_into("<I", b, i + 4, len(b) - (i + 8))
    return bytes(b)


_PAGE = """<!doctype html><meta charset="utf-8"><title>TTS script check</title>
<style>body{{font:15px system-ui;max-width:900px;margin:24px auto;padding:0 16px}}
td{{padding:4px 8px;border-bottom:1px solid #ddd;vertical-align:middle}} audio{{height:32px}}</style>
<h2>Blind TTS check</h2><p>Score each clip 1-5 for naturalness and note any mispronounced word
(e.g. EMI, verify, UPI, numbers). Engines and scripts are in key.csv; open it only after scoring.</p>
{groups}"""


def main() -> None:
    import sys
    global LINES, VOICES
    if "--numbers" in sys.argv:  # number-style A/B: Sarvam voices + one Cartesia voice
        LINES = NUMBER_LINES
        VOICES = [v for v in VOICES if v[3] != "kriti"]
    load_dotenv()
    keys = {"sarvam": os.environ.get("SARVAM_API_KEY"), "cartesia": os.environ.get("CARTESIA_API_KEY")}
    for k, v in keys.items():
        if not v:
            raise SystemExit(f"{k.upper()}_API_KEY is not set")
    jobs = [(v, line, form, text) for line, forms in LINES.items() for form, text in forms.items()
            for v in VOICES]
    chars = {e: sum(len(t) for v, _, _, t in jobs if v[0] == e) for e in keys}
    print(f"{len(jobs)} requests; characters: {chars} (paid, small)")
    if input("Proceed? [y/N] ").strip().lower() != "y":
        return

    out = Path("results/tts_script_check") / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    rng = random.Random(7)
    rows, groups = [], []
    with httpx.Client() as client:
        for line in LINES:
            clips = [j for j in jobs if j[1] == line]
            rng.shuffle(clips)  # blind: order within a line says nothing about engine or script
            cells = []
            for (engine, model, voice, label), _, form, text in clips:
                clip = f"c{len(rows) + 1:03d}"
                started = time.monotonic()
                try:
                    audio = (sarvam if engine == "sarvam" else cartesia)(client, keys[engine], model,
                                                                       voice, form, text)
                    ms, err = round((time.monotonic() - started) * 1000), ""
                    (out / f"{clip}.wav").write_bytes(audio)
                    cells.append(f'<tr><td>{clip}</td><td><audio controls preload="none" '
                                 f'src="{clip}.wav"></audio></td><td>score: ____</td></tr>')
                except (RuntimeError, httpx.HTTPError) as e:
                    ms, err = None, str(e)[:200]
                    print(f"  {clip} {engine}/{label}/{form} FAILED: {err}")
                rows.append({"clip": clip, "line": line, "form": form, "engine": engine, "model": model,
                             "voice": label, "request_ms": ms, "error": err, "text": text})
            groups.append(f"<h3>{line}</h3><table>{''.join(cells)}</table>")
    with (out / "key.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "listen.html").write_text(_PAGE.format(groups="".join(groups)))
    for e in keys:
        ms = sorted(r["request_ms"] for r in rows if r["engine"] == e and r["request_ms"])
        if ms:
            print(f"{e}: whole-clip request time median {ms[len(ms) // 2]} ms (n={len(ms)}); "
                  f"NOT first-audio latency (REST, full clip)")
    print(f"Wrote {out}/listen.html and key.csv")


if __name__ == "__main__":
    main()
