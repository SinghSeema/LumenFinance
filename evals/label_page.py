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
# Rebuilt 2026-11-04 18:30 from the v17 dev run (prompt v17, judge rubric v3): the v10 sample showed
# behaviour the agent no longer has ("colleague", "concerned team", "privacy"). No hold-out scenario
# (S06, S11, S13, S14a, S14b), so labelling never exposes them. Mixed high and low judge scores, so
# calibration tests the judge in both directions.
SAMPLE = [  # 10: the calls where a judge is most likely to be wrong
    ("20261104-181142-v17-dev3", "S03_run1", "hardship: does the code-owned flow sound caring? (judge empathy 5)"),
    ("20261104-181142-v17-dev3", "S04_run1", "wrong amount, firm borrower (judge empathy 5)"),
    ("20261104-181142-v17-dev3", "S04b_run2", "not my loan, fear of misuse (judge empathy 3)"),
    ("20261104-181142-v17-dev3", "S05_run2", "wrong number: correct refusal, short call"),
    ("20261104-181142-v17-dev3", "S08_run1", "irritated, off-topic, asks if it is a bot (judge naturalness 3)"),
    ("20261104-181142-v17-dev3", "S10_run2", "verification keeps failing: repeated lines (judge naturalness 1)"),
    ("20261104-181142-v17-dev3", "S12_run1", "stop calling: correct early end (judge empathy 2)"),
    ("20261104-181142-v17-dev3", "S16_run2", "asks the late fee before the due date (judge empathy 2)"),
    ("20261104-181142-v17-dev3", "S17_run1", "English, grace period / auto-debit / CIBIL questions (judge naturalness 3)"),
    ("20261104-181142-v17-dev3", "S18_run1", "male Hinglish borrower, asks to pay cash"),
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
