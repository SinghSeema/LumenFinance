"""Eval dashboard: python3 -m evals.dashboard <baseline run> [<fix round 1> [<fix round 2>]] -> results/dashboard.html

One self-contained HTML page (no network) laid out along the assignment brief: the borrower
responses it lists, its example metrics, latency, real failures and what changed, and every
conversation readable in full. All numbers come from saved artifacts, scored by evals/score.py
(rules), evals/judge.py (tone) and the live-call latency logs; verdicts on failures come from
the hand review in evals/failure_review.yaml.
"""
from __future__ import annotations

import html
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import yaml

from emi_agent.data import load_borrowers
from evals import score
from evals.judge import RUBRIC
from evals.report import CURRENT, _btype, latency_turns, metrics_rows

OUT = Path("results/dashboard.html")
REVIEW = Path("evals/failure_review.yaml")


def load_run(spec: str) -> dict:
    """One run directory, or several joined by "+" (same code version, a later run replacing an
    earlier one scenario by scenario)."""
    dirs = [Path(d) for d in spec.split("+")]
    latest = {}
    for d in dirs:
        for p in sorted(d.glob("S*_run*.json")):
            latest.setdefault(p.name.split("_run")[0], {})[p.name] = (d, p)
        for sid in {p.name.split("_run")[0] for p in d.glob("S*_run*.json")}:
            latest[sid] = {p.name: (d, p) for p in d.glob(f"{sid}_run*.json")}
    pairs = [v for sid in sorted(latest) for v in sorted(latest[sid].values())]
    arts = [json.loads(p.read_text()) for _, p in pairs]
    for (d, _), a in zip(pairs, arts):
        a["_dir"] = d.name
    scores = [score.score_run(a) for a in arts]
    judge = {}
    for d in dirs:
        jp = d / "judge.json"
        if jp.exists():
            judge.update({(j["scenario"], j["run"]): j for j in json.loads(jp.read_text())})
    return {"id": "+".join(d.name for d in dirs), "arts": arts, "scores": scores, "judge": judge}


def _p90(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]


LABELS = ["Baseline", "Fix round 1", "Fix round 2"]


def build(runs: list[dict]) -> dict:
    """runs[0] is the baseline (all scenarios); later runs re-ran the scenarios the fixes touched."""
    borrowers = load_borrowers()
    review = (yaml.safe_load(REVIEW.read_text()) or {}) if REVIEW.exists() else {}
    base = runs[0]

    def per_scenario(run):
        by = {}
        for a, s in zip(run["arts"], run["scores"]):
            if s["infra_error"]:
                continue
            d = by.setdefault(a["scenario"]["id"], {"pass": 0, "n": 0, "outcomes": {}})
            d["pass"] += s["outcome_pass"]
            d["n"] += 1
            d["outcomes"][s["outcome"]] = d["outcomes"].get(s["outcome"], 0) + 1
        return by

    by_run = [per_scenario(r) for r in runs]
    scenarios = []
    for a in base["arts"]:
        sc = a["scenario"]
        if any(x["id"] == sc["id"] for x in scenarios):
            continue
        bw = borrowers[sc["borrower_id"]]
        brs = [(x, s) for x, s in zip(base["arts"], base["scores"]) if x["scenario"]["id"] == sc["id"]]
        tones = [statistics.mean(j[k] for k in RUBRIC if k in j)
                 for j in (base["judge"].get((sc["id"], x["run"])) for x, _ in brs) if j and "politeness" in j]
        want = sc["expected"].get("escalate_to_human")
        scenarios.append({
            "id": sc["id"], "type": _btype(sc), "set": sc["set"], "expected": sc["expected"],
            "borrower": f"{bw.name} ({bw.preferred_language}, "
                        f"{str(bw.days_overdue) + ' days overdue' if bw.days_overdue else 'upcoming'})",
            "runs": [b.get(sc["id"]) for b in by_run],
            "leaks": sum(len(s["disclosure_violations"]) for r in runs for s in r["scores"]
                         if s["scenario"] == sc["id"]),
            "esc": f"{sum(s['escalation'][1] == want for _, s in brs)}/{len(brs)}" if want is not None else "-",
            "turns": round(statistics.mean(s["borrower_turns"] for _, s in brs), 1),
            "tone": round(statistics.mean(tones), 1) if tones else None,
        })
    scenarios.sort(key=lambda s: (int("".join(c for c in s["id"][1:] if c.isdigit())), s["id"]))

    def tiles(run):
        valid = [(a, s) for a, s in zip(run["arts"], run["scores"]) if not s["infra_error"]]
        sets = {k: [s for a, s in valid if a["scenario"]["set"] == k] for k in ("dev", "hold-out")}
        esc = score.escalation([s for _, s in valid])
        return {"n": len(valid), "pass": sum(s["outcome_pass"] for _, s in valid),
                "dev": [sum(s["outcome_pass"] for s in sets["dev"]), len(sets["dev"])],
                "hold": [sum(s["outcome_pass"] for s in sets["hold-out"]), len(sets["hold-out"])],
                "leaks": sum(bool(s["disclosure_violations"]) for _, s in valid),
                "precision": esc["precision"], "recall": esc["recall"],
                "scenarios": sorted({a["scenario"]["id"] for a, _ in valid})}

    turns = latency_turns()
    cur = [t for c, t in turns if c >= CURRENT]
    gaps = [t["ms"]["gap"] for t in cur]
    lat_rows = []
    for label, sel in (("All owner calls, 3-4 Nov", turns), ("Current stack, 4 Nov calls", [x for x in turns if x[0] >= CURRENT])):
        ts = [t for _, t in sel]
        g = [t["ms"]["gap"] for t in ts]
        lat_rows.append({"label": label, "calls": len({c for c, _ in sel}), "turns": len(ts),
                         "median": statistics.median(g), "p90": _p90(g),
                         "stt": statistics.median(t["ms"]["vad_stop->stt_final"] for t in ts),
                         "llm": statistics.median(t["ms"]["stt_final->first_sentence"] for t in ts),
                         "tts": statistics.median(t["ms"]["first_sentence->playback_start"] for t in ts)})

    failures, convs, flipped = [], [], 0
    for run, label in zip(runs, LABELS):
        for a, s in zip(run["arts"], run["scores"]):
            notes = review.get(a["_dir"], {})
            key = f"{a['scenario']['id']}#{a['run']}"
            j = run["judge"].get((a["scenario"]["id"], a["run"]), {})
            if s["outcome_pass"] and key in notes:
                flipped += 1  # failed when first scored, passes with today's scorer (review says why)
            if not s["outcome_pass"]:
                r = notes.get(key, {})
                failures.append({"run": label, "key": key, "outcome": s["outcome"],
                                 "failed": [k for k, v in s["checks"].items() if not v],
                                 "verdict": r.get("verdict", "not reviewed"), "note": r.get("note", ""),
                                 "fix": r.get("fix", "")})
            convs.append({
                "run": label, "key": key, "pass": s["outcome_pass"],
                "checks": s["checks"], "leaks": s["disclosure_violations"], "guard": s["guard_hits"],
                "outcome": a["outcome"],
                "turns": [{"role": t["role"], "text": t.get("text", ""), "tools": t.get("tools", [])}
                          for t in a["turns"]],
                "tools": [{"name": c["name"], "args": c["args"], "result": c["result"]} for c in a["tool_calls"]],
                "judge": {k: j[k] for k in RUBRIC if k in j} | ({"comment": j["comment"]} if j.get("comment") else {}),
            })

    a0 = base["arts"][0]
    return {
        "meta": {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "runs": [r["id"] for r in runs],
                 "labels": LABELS[:len(runs)], "agent": a0["agent_llm"], "simulator": a0["simulator_llm"],
                 "prompt": a0["config"]["behavior"]["prompt_version"], "flipped": flipped},
        "tiles": [tiles(r) for r in runs],
        "scenarios": scenarios,
        "metrics": [list(r) for r in metrics_rows(base["arts"], base["scores"], base["judge"])],
        "latency": {"rows": lat_rows, "gaps": gaps, "median": statistics.median(gaps), "p90": _p90(gaps)},
        "failures": failures, "convs": convs,
    }


def main(argv: list[str]) -> None:
    if not 1 <= len(argv) <= len(LABELS):
        raise SystemExit(__doc__)
    data = build([load_run(p) for p in argv])
    page = (Path(__file__).parent / "dashboard_template.html").read_text()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(page.replace("/*DATA*/null", payload))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB): {len(data['convs'])} conversations, "
          f"{len(data['failures'])} failures listed")


if __name__ == "__main__":
    main(sys.argv[1:])
