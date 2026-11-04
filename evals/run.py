"""Text-mode eval runner (E-01): python -m evals.run --scenarios dev --runs 1 [--profile P] [--set k=v]

--scenarios takes dev, holdout, all, or ids like S02,S05.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import yaml

from emi_agent import config as config_mod
from emi_agent.brain import Brain
from emi_agent.data import load_borrowers
from emi_agent.llm import build_llm
from emi_agent.tools import protected_phrases
from evals import score
from evals.simulator import Simulator

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
RESULTS_DIR = Path(__file__).parent.parent / "results"
_SETS = {"dev": "dev", "holdout": "hold-out"}
_SECRET_SUFFIXES = ("api_key", "api_secret", "secret_key")
# Agent tokens per call, measured: (input, output). Groq: baseline-v1, gpt-oss-20b, 10 dev calls.
# Sarvam: sarvam-v3-smoke, sarvam-105b-conversations, 11 dev calls (101,402 / 2,278 in total).
_TOKENS_PER_RUN = {"groq": (14500, 1000), "sarvam": (9200, 210)}
_REQUESTS_PER_RUN = 7  # rough guess, not a measurement
# Sarvam list price in rupees per 1M tokens (docs price sheet for sarvam-105b; not the dashboard).
_SARVAM_INR_PER_M = (29.28, 73.2)
# Simulator tokens per call, measured: sarvam-v4-dev5 (qwen3.8-27b on Groq), 135,950 / 6,369 over 55.
# glm5.3-flash also bills reasoning tokens, so its output may be higher; its price is assumed to be
# the sarvam-105b price above until checked on the dashboard.
_SIM_TOKENS_PER_RUN = (2500, 120)
_SIM_KEYS = {"groq": "llm.groq_api_key", "sarvam": "tts.sarvam_api_key"}
_FREE_TPM = 8000
_FREE_RPD = 1000
_FREE_TPD = 200_000  # gpt-oss-20b, from a 429 on 2026-11-03; not shown in response headers
# One model per run: a silent fallback would mix two models' results (E-10). Retries ride out
# the per-minute cap; an exhausted daily cap then shows up as an infra error, not a model switch.
EVAL_OVERRIDES = ["llm.max_retries=8", "llm.fallback=[]"]


def load_scenarios(selector: str) -> list[dict]:
    scenarios = [yaml.safe_load(p.read_text()) for p in sorted(SCENARIOS_DIR.glob("*.yaml"))]
    if selector == "all":
        return scenarios
    if selector in _SETS:
        return [s for s in scenarios if s["set"] == _SETS[selector]]
    ids = set(selector.split(","))
    chosen = [s for s in scenarios if s["id"] in ids]
    if missing := ids - {s["id"] for s in chosen}:
        raise SystemExit(f"Unknown scenario ids: {sorted(missing)}")
    return chosen


def redact(cfg: dict) -> dict:
    return {k: (redact(v) if isinstance(v, dict) else "***" if k.endswith(_SECRET_SUFFIXES) else v)
            for k, v in cfg.items()}


async def run_conversation(scenario: dict, run: int, cfg: dict, borrower, llm, simulator,
                           calls_path: Path) -> dict:
    """Play the simulator against a fresh Brain; return the raw artifact (E-09)."""
    today = date.fromisoformat(scenario["today"])
    clock = {"t": 0.0}
    brain = Brain(cfg, borrower, today, llm, calls_path=calls_path, clock=lambda: clock["t"])
    turns = [{"role": "agent", "text": brain.greeting, "verified": False, "guard": []}]
    end_reason, infra_error = "max_turns", None
    started = time.monotonic()
    try:
        agent_text = brain.greeting
        for _ in range(cfg["evals"]["max_turns"]):
            said = await simulator.reply(agent_text)
            if said is None:
                end_reason = "borrower_hung_up"
                break
            turns.append({"role": "borrower", "text": said})
            clock["t"] += cfg["evals"]["secs_per_turn"]
            n_calls = len(brain.state.tool_calls)
            sentences = [s async for s in brain.respond(said)]
            agent_text = " ".join(s.text for s in sentences)
            # Tools called while producing this turn, so process checks can order them (S13).
            turns.append({"role": "agent", "text": agent_text, "verified": brain.state.verified,
                          "guard": [s.guard for s in sentences if s.guard],
                          "tools": [c["name"] for c in brain.state.tool_calls[n_calls:]]})
            if brain.should_hang_up:
                end_reason = "agent_ended"
                break
    except Exception as e:  # a simulator API failure must not lose the artifact
        infra_error = f"simulator: {type(e).__name__}: {e}"
    if "llm_failed" in brain.state.flags:
        infra_error = infra_error or "agent LLM failed: " + next(
            c["result"]["error"] for c in brain.state.tool_calls if c["name"] == "_llm_error")
    record = brain.finish()
    s = brain.state
    return {
        "scenario": scenario,
        "run": run,
        "agent_llm": f"{cfg['llm']['provider']}:{cfg['llm']['model']}",
        "simulator_llm": f"{cfg['evals']['simulator_provider']}:{cfg['evals']['simulator_model']}",
        "config": redact(cfg),
        "turns": turns,
        "tool_calls": s.tool_calls,
        "outcome": record.model_dump(mode="json"),
        "disclosure_log": [asdict(d) | {"stage": d.stage.value} for d in s.disclosure_log],
        "guard_hits": s.guard_hits,
        "speech_hits": s.speech_hits,
        "flags": sorted(s.flags),
        "protected_phrases": protected_phrases(borrower, today),
        "usage": {"agent": brain.usage, "simulator": getattr(simulator, "usage", {})},
        "llm_turns_by_provider": brain.turns_by_provider,
        "end_reason": end_reason,
        "infra_error": infra_error,
        "wall_secs": round(time.monotonic() - started, 1),
    }


def _estimate(n_runs: int, provider: str, sim_provider: str) -> None:
    tin, tout = _TOKENS_PER_RUN.get(provider, _TOKENS_PER_RUN["groq"])
    requests, tokens, out = n_runs * _REQUESTS_PER_RUN, n_runs * tin, n_runs * tout
    print(f"Estimate (not measured): {n_runs} runs, ~{requests} agent requests, ~{tokens:,} agent "
          f"input and ~{out:,} output tokens ({provider}).")
    if provider == "sarvam":
        inr = (tokens * _SARVAM_INR_PER_M[0] + out * _SARVAM_INR_PER_M[1]) / 1e6
        print(f"  Agent on Sarvam: about Rs {inr:.0f} at the docs price.")
    else:
        print(f"  Groq free tier: ~{tokens / _FREE_TPM:.0f} min at {_FREE_TPM} tokens/min; "
              f"{requests} of {_FREE_RPD} requests/day; {tokens:,} of {_FREE_TPD:,} tokens/day "
              f"per model. Cost on the free tier: $0.")
    if sim_provider == "sarvam":
        sin, sout = n_runs * _SIM_TOKENS_PER_RUN[0], n_runs * _SIM_TOKENS_PER_RUN[1]
        inr = (sin * _SARVAM_INR_PER_M[0] + sout * _SARVAM_INR_PER_M[1]) / 1e6
        print(f"  Simulator on Sarvam: ~{sin:,} in / ~{sout:,} out, about Rs {inr:.0f} "
              f"(assumes the sarvam-105b price).")
    else:
        print("  Simulator on the Groq free tier: $0 (200k tokens/day cap).")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scenarios", default="dev")
    p.add_argument("--runs", type=int, help="runs per scenario (default: the scenario's own 'runs')")
    p.add_argument("--profile")
    p.add_argument("--set", action="append", default=[], dest="overrides")
    p.add_argument("--label", default="")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = p.parse_args()

    config_mod.load_dotenv()
    scenarios = load_scenarios(args.scenarios)
    plan = [(s, r) for s in scenarios for r in range(1, (args.runs or s["runs"]) + 1)]
    base = EVAL_OVERRIDES + args.overrides
    probe = config_mod.load(args.profile, base)
    sim_key = _SIM_KEYS[probe["evals"]["simulator_provider"]]
    section, key = sim_key.split(".")
    if not probe[section].get(key):
        raise SystemExit(f"Simulator key {sim_key} is missing; add it to .env (see .env.example).")
    print(f"Agent {probe['llm']['provider']}:{probe['llm']['model']} | simulator "
          f"{probe['evals']['simulator_provider']}:{probe['evals']['simulator_model']} | scenarios {[s['id'] for s in scenarios]}")
    _estimate(len(plan), probe["llm"]["provider"], probe["evals"]["simulator_provider"])
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.label}" if args.label else "")
    out = RESULTS_DIR / run_id
    out.mkdir(parents=True)
    borrowers = load_borrowers()
    totals = {"agent_input": 0, "agent_output": 0, "sim_input": 0, "sim_output": 0}
    for scenario, run in plan:
        faults = [f"faults.{k}={v}" for k, v in (scenario.get("faults") or {}).items()]
        cfg = config_mod.load(args.profile, base + faults)
        borrower = borrowers[scenario["borrower_id"]]
        ev = cfg["evals"]
        section, key = _SIM_KEYS[ev["simulator_provider"]].split(".")
        sim = Simulator(scenario, borrower, ev["simulator_provider"], ev["simulator_model"],
                        cfg[section][key], ev["simulator_temperature"], cfg["llm"]["max_retries"])
        a = await run_conversation(scenario, run, cfg, borrower, build_llm(cfg), sim,
                                   out / "calls.jsonl")
        (out / f"{scenario['id']}_run{run}.json").write_text(
            json.dumps(a, indent=2, ensure_ascii=False, default=str))
        r = score.score_run(a)
        totals["agent_input"] += a["usage"]["agent"]["input"]
        totals["agent_output"] += a["usage"]["agent"]["output"]
        totals["sim_input"] += a["usage"]["simulator"].get("input", 0)
        totals["sim_output"] += a["usage"]["simulator"].get("output", 0)
        status = "INFRA" if r["infra_error"] else "pass" if r["outcome_pass"] else "FAIL"
        print(f"{scenario['id']} run{run}: {status} outcome={r['outcome']} "
              f"leaks={len(r['disclosure_violations'])} turns={r['borrower_turns']} "
              f"{a['wall_secs']}s {r['infra_error'] or ''}")

    score.print_table(score.summarize(out))
    print(f"Tokens: {totals}\nArtifacts: {out}")


if __name__ == "__main__":
    asyncio.run(main())
