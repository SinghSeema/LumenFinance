"""Results tables for the eval report: python3 -m evals.report results/<run> > results/<run>/tables.md

Everything here is computed from saved artifacts: the run's conversations (score.py rules), the
judge's scores if judge.json exists (judge.py), and the live-call latency logs in results/latency/.
The narrative (failures, changes, next steps) is written by hand in docs/eval-report.md.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from emi_agent.data import load_borrowers
from evals import score
from evals.judge import RUBRIC

# The brief's borrower responses (requirements R1-R8) and the tricky cases we added.
TYPES = {
    "R1": "Will pay / already paid", "R2": "Promises a later date", "R3": "Financial hardship",
    "R4": "Disputes amount / not my loan", "R5": "Wrong number / family member",
    "R6": "Asks for a callback", "R7": "Irritated, rude, off topic", "R8": "Switches language",
}
LATENCY_DIR = Path("results/latency")
STT_TUNED = "20261103-212818"  # first live call after Deepgram EU host + endpointing 100 (LOG.md)
CURRENT = "20261104-000000"    # all 2026-11-04 calls: Sarvam LLM and today's turn-taking code


def _btype(s: dict) -> str:
    rs = [TYPES[c] for c in s.get("covers", []) if c in TYPES]
    return rs[0] if rs else "Tricky: " + s["name"].replace("_", " ")


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def scenario_table(arts: list[dict], scores: list[dict], judge: dict) -> list[str]:
    borrowers = load_borrowers()
    by = defaultdict(list)
    for a, s in zip(arts, scores):
        by[a["scenario"]["id"]].append((a, s))
    out = ["| Scenario | Borrower type | Borrower (language, EMI state) | Set | Pass | Leaks | Esc. right | Turns | Tone (judge) | Usual outcome |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for sid in sorted(by, key=lambda x: (int("".join(c for c in x[1:] if c.isdigit())), x)):
        rows = by[sid]
        sc = rows[0][0]["scenario"]
        b = borrowers[sc["borrower_id"]]
        state = f"{b.days_overdue} days overdue" if b.days_overdue else "upcoming"
        valid = [(a, s) for a, s in rows if not s["infra_error"]]
        want = sc["expected"].get("escalate_to_human")
        esc = (f"{sum(s['escalation'][1] == want for _, s in valid)}/{len(valid)}"
               if want is not None else "-")
        tones = [statistics.mean(j[k] for k in RUBRIC if isinstance(j.get(k), int)) for j in
                 (judge.get((sid, a["run"])) for a, _ in valid) if j and any(k in j for k in RUBRIC)]
        common = Counter(s["outcome"] for _, s in valid).most_common(1)
        out.append(
            f"| {sid} | {_btype(sc)} | {b.name} ({b.preferred_language}, {state}) | {sc['set']} | "
            f"{sum(s['outcome_pass'] for _, s in valid)}/{len(valid)}"
            f"{' +' + str(len(rows) - len(valid)) + ' infra' if len(rows) > len(valid) else ''} | "
            f"{sum(len(s['disclosure_violations']) for _, s in rows)} | {esc} | "
            f"{statistics.mean(s['borrower_turns'] for _, s in valid):.1f} | "
            f"{f'{statistics.mean(tones):.1f}' if tones else '-'} | {common[0][0] if common else '-'} |")
    return out


def metrics_table(arts: list[dict], scores: list[dict], judge: dict) -> list[str]:
    out = ["| Metric | How it is scored | Result | Trust |", "|---|---|---|---|"]
    return out + [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in metrics_rows(arts, scores, judge)]


def metrics_rows(arts: list[dict], scores: list[dict], judge: dict) -> list[tuple[str, str, str, str]]:
    valid = [(a, s) for a, s in zip(arts, scores) if not s["infra_error"]]
    sets = {k: [(a, s) for a, s in valid if a["scenario"]["set"] == k] for k in ("dev", "hold-out")}
    promise = [s for a, s in valid if "promise_to_pay_date" in a["scenario"]["expected"]
               and a["scenario"]["expected"]["promise_to_pay_date"]]
    leak_runs = sum(bool(s["disclosure_violations"]) for _, s in valid)
    esc = score.escalation([s for _, s in valid])
    guard = Counter(g for _, s in valid for g in s["guard_hits"])
    proc = [(k, v) for _, s in valid for k, v in s["checks"].items() if k.startswith("process:")]
    jflags = Counter(f["type"] for j in judge.values() for f in j.get("flags", []))
    tone = {k: [j[k] for j in judge.values() if isinstance(j.get(k), int)] for k in RUBRIC}
    lang = sum(bool(s["language_mismatch_turns"]) for _, s in valid)
    turns = [s["borrower_turns"] for _, s in valid]
    secs = [a["wall_secs"] for a, _ in valid]
    rows = [
        ("M1 Outcome accuracy", "Rule: outcome record fields vs the scenario's expected record",
         f"all {pct(sum(s['outcome_pass'] for _, s in valid), len(valid))}; dev "
         f"{pct(sum(s['outcome_pass'] for _, s in sets['dev']), len(sets['dev']))}; hold-out "
         f"{pct(sum(s['outcome_pass'] for _, s in sets['hold-out']), len(sets['hold-out']))}", "High"),
        ("M2 Verification before disclosure", "Rule: disclosure log + scan of unverified agent turns",
         f"{leak_runs} runs with a leak out of {len(valid)}", "High"),
        ("M3 Promise-to-pay date", "Rule: exact date match",
         pct(sum(s["checks"].get("promise_to_pay_date", False) for s in promise), len(promise)), "High"),
        ("M4 Escalation", "Rule: flag vs expected",
         f"precision {esc['precision']}, recall {esc['recall']} (tp {esc['tp']}, fp {esc['fp']}, fn {esc['fn']})", "High"),
        ("M5 Guardrail violations", "Code guard (replaced before speech) + LLM judge flags with exact quotes",
         f"code guard replaced {sum(guard.values())} sentences {dict(guard) or ''}; judge flags {dict(jflags) or 'none'}",
         "High for the code guard; low for the judge"),
        ("M6 Language adherence", "Rule: reply script family vs a 5+ word borrower turn",
         f"{lang} runs with a mismatch out of {len(valid)}", "Medium"),
        ("M7 Tone", "LLM judge (glm5.3-flash), rubric 1-5, uncalibrated",
         "; ".join(f"{k} {statistics.mean(v):.2f}" for k, v in tone.items() if v) or "not run", "Low"),
        ("M8 Call efficiency", "Rule: borrower turns and wall time per call",
         f"borrower turns median {statistics.median(turns)}, max {max(turns)}; text-mode wall time median {statistics.median(secs):.0f} s",
         "High as a count; weak as a quality signal"),
        ("M11 Recovery / process", "Rule: AI disclosure when asked, no invented link after a tool failure, a question back on a vague date",
         pct(sum(v for _, v in proc), len(proc)) + " checks passed", "Medium"),
    ]
    return rows


def latency_turns() -> list[tuple[str, dict]]:
    """(call stamp, turn) for every completed live turn in results/latency/."""
    turns = []
    for p in sorted(LATENCY_DIR.glob("*.jsonl")):
        for line in p.read_text().splitlines():
            t = json.loads(line)
            if not t.get("interrupted") and t.get("ms", {}).get("gap") is not None:
                turns.append((p.name[:15], t))
    return turns


def latency_section() -> list[str]:
    turns = latency_turns()
    out = ["| Live calls | Turns | Gap median | Gap p90 | Felt by caller (+VAD stop) median / p90 | STT final | LLM to first sentence | TTS to audio |",
           "|---|---|---|---|---|---|---|---|"]

    def row(label, ts):
        if not ts:
            return
        g = sorted(t["ms"]["gap"] for _, t in ts)
        felt = sorted(t["ms"]["gap"] + t["stop_secs"] * 1000 for _, t in ts)
        p90 = lambda xs: xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]
        med = lambda k: statistics.median(t["ms"][k] for _, t in ts if t["ms"].get(k) is not None)
        calls = len({c for c, _ in ts})
        out.append(f"| {label} ({calls} calls) | {len(ts)} | {statistics.median(g):.0f} ms | {p90(g):.0f} ms | "
                   f"{statistics.median(felt):.0f} / {p90(felt):.0f} ms | {med('vad_stop->stt_final'):.0f} ms | "
                   f"{med('stt_final->first_sentence'):.0f} ms | {med('first_sentence->playback_start'):.0f} ms |")
    row("All owner calls, 2026-11-03 and 04", turns)
    row("Since the STT tuning (Deepgram EU, endpointing 100)", [x for x in turns if x[0] >= STT_TUNED])
    row("Current stack only: 2026-11-04 calls (Sarvam LLM, streamed replies)", [x for x in turns if x[0] >= CURRENT])
    return out


def main(run_dir: str) -> None:
    run = Path(run_dir)
    arts = [json.loads(p.read_text()) for p in sorted(run.glob("S*_run*.json"))]
    scores = [score.score_run(a) for a in arts]
    jpath = run / "judge.json"
    judge = {(j["scenario"], j["run"]): j for j in json.loads(jpath.read_text())} if jpath.exists() else {}
    infra = sum(bool(s["infra_error"]) for s in scores)
    print(f"Run `{run.name}`: {len(arts)} conversations, {infra} infra errors (simulator broke "
          f"character; excluded), agent {arts[0]['agent_llm']}, simulator {arts[0]['simulator_llm']}, "
          f"prompt {arts[0]['config']['behavior']['prompt_version']}.\n")
    print("### Metrics\n")
    print("\n".join(metrics_table(arts, scores, judge)))
    print("\n### By borrower type\n")
    print("\n".join(scenario_table(arts, scores, judge)))
    print("\nPass = every field of the expected outcome record plus the scenario's process checks. "
          "Leaks = loan details logged or spoken before verification. Esc. right = escalation flag "
          "as expected. Turns = mean borrower turns. Tone = mean of the judge's five rubric scores.\n")
    print("### Latency (voice, live calls by the owner)\n")
    print("\n".join(latency_section()))
    print("\nGap = VAD stop to first audio. The caller also waits the 0.5 s VAD stop time before "
          "that, shown in the 'felt' column. Calls span code changes over two days, so this is a "
          "pooled picture, not a controlled measurement; the 30-turn gate run is still to do.")


if __name__ == "__main__":
    main(sys.argv[1])
