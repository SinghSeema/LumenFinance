"""Hand-labelling page for judge calibration: python3 -m evals.label_page -> results/labelling.html

The owner scores 10 conversations on the judge's rubric, blind (no pass/fail, no judge scores),
then exports owner_labels.json into evals/labels/. evals/calibrate.py compares candidate judges
with those labels. The 10 are chosen to cover every borrower type and the cases where judges
go wrong (correct refusals, repetition, angry callers).
"""
from __future__ import annotations

import json
from pathlib import Path

from evals.judge import FLAGS, RUBRIC
from evals.report import _btype

# (run directory, conversation, why it is in the set)
SAMPLE = [  # 10, owner 2026-11-04: the calls where a judge is most likely to be wrong
    ("20261104-133125-v10-all5", "S03_run1", "hardship: empathy matters"),
    ("20261104-133125-v10-all5", "S04_run2", "dispute; angry; closing said twice"),
    ("20261104-133125-v10-all5", "S04b_run4", "not my loan; promised a callback 'today'"),
    ("20261104-133125-v10-all5", "S06_run3", "family member: correct refusal (old judge gave clarity 1)"),
    ("20261104-133125-v10-all5", "S08_run3", "irritated (old judge gave firmness 1)"),
    ("20261104-133125-v10-all5", "S11_run2", "demands a waiver (old judge flagged a false promise)"),
    ("20261104-133125-v10-all5", "S12_run3", "stop calling: correct early end (old judge gave firmness 1)"),
    ("20261104-133125-v10-all5", "S13_run3", "vague date accepted without a question"),
    ("20261104-133125-v10-all5", "S14a_run1", "mumbled digits; 'pay today' refused"),
    ("20261104-135904-v10-fixes", "S04_run5", "angry, demands a human; same line repeated"),
]
OUT = Path("results/labelling.html")


def load_sample() -> list[dict]:
    items = []
    for run, conv, why in SAMPLE:
        a = json.loads(Path("results", run, conv + ".json").read_text())
        items.append({"id": f"{run}/{conv}", "type": _btype(a["scenario"]), "why": why,
                      "turns": [{"role": t["role"], "text": t["text"]} for t in a["turns"] if t.get("text")],
                      "art": a})
    return items


def main() -> None:
    items = load_sample()
    data = {"rubric": RUBRIC, "flags": FLAGS,
            "items": [{k: v for k, v in i.items() if k != "art"} for i in items]}
    page = (Path(__file__).parent / "label_template.html").read_text()
    OUT.write_text(page.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
    print(f"wrote {OUT}: {len(items)} conversations. Open it in a browser, score each one, press "
          "Export, and save the file as evals/labels/owner_labels.json")


if __name__ == "__main__":
    main()
