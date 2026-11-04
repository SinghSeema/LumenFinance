"""Judge calibration: python3 -m evals.calibrate [--judges groq:openai/gpt-oss-120b gemini:gemini-3.5-flash ...]

Runs each candidate judge on the 10 conversations the owner labelled (evals/labels/owner_labels.json,
from results/labelling.html) and reports how well each agrees with the owner, per rubric item:
exact match, within one point, "n/a" agreement, mean absolute difference, and guardrail-flag
precision and recall. The judge to trust is the one that agrees best; its agreement is the trust
number reported for M7. Judges' raw scores are kept in results/judge_calibration/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from evals.judge import FLAGS, RUBRIC, judge_all
from evals.label_page import load_sample

LABELS = Path("evals/labels/owner_labels.json")
OUT = Path("results/judge_calibration")
DEFAULT_JUDGES = ["groq:openai/gpt-oss-120b", "gemini:gemini-3.5-flash", "sarvam:glm5.3-flash"]


def agreement(owner: list[dict], judge: list[dict]) -> dict:
    """Per rubric item and for flags. Both lists are aligned by conversation."""
    out = {}
    for k in RUBRIC:
        pairs = [(o.get(k), j.get(k)) for o, j in zip(owner, judge) if o.get(k) is not None and k in j]
        both = [(a, b) for a, b in pairs if isinstance(a, int) and isinstance(b, int)]
        na_same = sum((a == "na") == (b == "na") for a, b in pairs)
        out[k] = {
            "n": len(pairs),
            "exact": sum(a == b for a, b in pairs) / len(pairs) if pairs else None,
            "within1": sum((a == b) or (isinstance(a, int) and isinstance(b, int) and abs(a - b) <= 1)
                           for a, b in pairs) / len(pairs) if pairs else None,
            "na_agree": na_same / len(pairs) if pairs else None,
            "mae": sum(abs(a - b) for a, b in both) / len(both) if both else None,
        }
    tp = fp = fn = 0
    for o, j in zip(owner, judge):
        mine, theirs = set(o.get("flags") or []), {f["type"] for f in j.get("flags", [])}
        tp, fp, fn = tp + len(mine & theirs), fp + len(theirs - mine), fn + len(mine - theirs)
    out["flags"] = {"tp": tp, "fp": fp, "fn": fn,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tp / (tp + fn) if tp + fn else None}
    w = [out[k]["within1"] for k in RUBRIC if out[k]["within1"] is not None]
    out["overall_within1"] = sum(w) / len(w) if w else None
    return out


def _pct(x) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def render(results: dict[str, dict], n: int) -> str:
    lines = [f"# Judge calibration ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"{n} conversations hand-labelled by the owner (rubric v3). Agreement of each candidate judge "
             "with the owner: within one point / exact / n/a agreement / mean absolute difference.", "",
             "| Judge | " + " | ".join(RUBRIC) + " | Overall within 1 | Flags precision / recall |",
             "|---|" + "---|" * (len(RUBRIC) + 2)]
    for name, a in sorted(results.items(), key=lambda kv: -(kv[1]["overall_within1"] or 0)):
        mae = lambda k: "-" if a[k]["mae"] is None else format(a[k]["mae"], ".2f")
        cells = [f"{_pct(a[k]['within1'])} / {_pct(a[k]['exact'])} / {_pct(a[k]['na_agree'])} / {mae(k)}"
                 for k in RUBRIC]
        f = a["flags"]
        lines.append(f"| {name} | " + " | ".join(cells) + f" | **{_pct(a['overall_within1'])}** | "
                     f"{_pct(f['precision'])} / {_pct(f['recall'])} (tp {f['tp']}, fp {f['fp']}, fn {f['fn']}) |")
    best = max(results, key=lambda k: results[k]["overall_within1"] or 0)
    lines += ["", f"Best agreement: **{best}**. Use it as the tone judge "
              "(configs/defaults.yaml evals.judge_provider / judge_model) and report its agreement as M7's trust."]
    return "\n".join(lines) + "\n"


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--judges", nargs="+", default=DEFAULT_JUDGES, help="provider:model")
    p.add_argument("--labels", default=str(LABELS))
    args = p.parse_args()
    if not Path(args.labels).exists():
        raise SystemExit(f"No labels yet: open results/labelling.html, score the 10 calls, Export, "
                         f"and save the file as {args.labels}")
    owner_all = {l["id"]: l for l in json.loads(Path(args.labels).read_text())["labels"]}
    sample = [s for s in load_sample() if s["id"] in owner_all]
    owner = [owner_all[s["id"]] for s in sample]
    print(f"{len(sample)} labelled conversations; judges: {', '.join(args.judges)} "
          f"(free tiers for Groq and Gemini; glm5.3-flash about Rs 1)")
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results, raw = {}, {}
    for spec in args.judges:
        provider, model = spec.split(":", 1)
        try:  # one judge failing (rate limit, bad key) must not lose the others' results
            # Groq free tier: 8,000 tokens a minute, so one call at a time (429 on 2026-11-04).
            scores = await judge_all(provider, model, [s["art"] for s in sample],
                                     concurrency=1 if provider == "groq" else 2)
        except Exception as e:
            print(f"  {spec}: FAILED, {type(e).__name__}: {str(e)[:200]}")
            continue
        raw[spec] = [{"id": s["id"], **j} for s, j in zip(sample, scores)]
        errors = sum("error" in j for j in scores)
        results[spec] = agreement(owner, scores)
        print(f"  {spec}: overall within 1 = {_pct(results[spec]['overall_within1'])}"
              f"{f' ({errors} unparsable replies)' if errors else ''}")
    (OUT / f"{stamp}.json").write_text(json.dumps({"agreement": results, "scores": raw}, indent=2, ensure_ascii=False))
    report = render(results, len(sample))
    (OUT / f"{stamp}.md").write_text(report)
    print("\n" + report + f"\nsaved {OUT / stamp}.md and .json")


if __name__ == "__main__":
    asyncio.run(main())
