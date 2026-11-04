"""Tuning T5: time to first spoken text per LLM arm, on the same real requests.

    python3 -m bakeoff.effort_ttft --runs 3 --arms groq:openai/gpt-oss-20b:low gemini:gemini-3.5-flash-lite

Each arm is provider:model[:reasoning_effort]. Two requests per run, both with the real system
prompt for B003: the reply to the greeting (expects text) and a last-four answer (expects a
verify_identity call). Arms alternate so a slow minute hits all of them. Paid or free-tier quota:
2 x runs requests of about 1.5k input tokens per arm.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import date

from emi_agent import config as config_mod
from emi_agent.brain import Brain
from emi_agent.data import load_borrowers
from emi_agent.llm import LLMError, _make_one
from emi_agent.tools import TOOL_SCHEMAS

ASK_LAST4 = "Dhanyavaad. Kya aap apne registered mobile number ke aakhri chaar ank bata sakte hain?"
TURNS = {  # history as the Brain sends it (the greeting lives in the system prompt)
    "greeting": [{"role": "user", "content": "Haan, main Rahul bol raha hoon. Boliye."}],
    "last4": [{"role": "user", "content": "Haan, main Rahul bol raha hoon. Boliye."},
              {"role": "assistant", "content": ASK_LAST4},
              {"role": "user", "content": "Haan ji, last four digits saat saat chaar teen hain."}],
}

async def one(llm, system: str, history: list) -> tuple[float, float, int, str, list]:
    start, first, text, tokens, tools = time.monotonic(), None, "", 0, []
    async for kind, value in llm.stream(system, history, TOOL_SCHEMAS):
        if kind == "text":
            first = first or time.monotonic()
            text += value
        else:
            tokens, tools = value.usage.get("output", 0), [c.name for c in value.tool_calls]
    end = time.monotonic()
    return ((first or end) - start) * 1000, (end - start) * 1000, tokens, text, tools


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--arms", nargs="+", required=True, help="provider:model[:reasoning_effort]")
    ap.add_argument("--gap", type=float, default=8.0, help="seconds between requests")
    args = ap.parse_args()

    config_mod.load_dotenv()
    cfg = config_mod.load("hindi_bulbul", overrides=["llm.timeout_secs=10"])
    brain = Brain(cfg, load_borrowers()["B003"], date.today(), llm=None)
    llms = {}
    for arm in args.arms:
        provider, *parts = arm.split(":")
        effort = parts.pop() if parts[-1] in ("none", "low", "medium", "high") else ""
        model = ":".join(parts)
        llms[arm] = _make_one({"provider": provider, "model": model, "max_retries": 0,
                               "reasoning_effort": effort or None}, cfg)
    rows: dict[str, list] = {a: [] for a in args.arms}
    for i in range(args.runs):
        for name, history in TURNS.items():
            for arm, llm in llms.items():
                try:
                    first, total, out, text, tools = await one(llm, brain.system, history)
                except LLMError as e:
                    print(f"run {i + 1} {name:8s} {arm:40s} FAILED {str(e)[:150]}")
                    continue
                rows[arm].append((first, total))
                print(f"run {i + 1} {name:8s} {arm:40s} first text {first:5.0f} ms  total "
                      f"{total:5.0f} ms  out {out:4d}  tools {tools}  | {text[:60]!r}", flush=True)
                await asyncio.sleep(args.gap)
    for arm, r in rows.items():
        if r:
            print(f"{arm:40s} median first text {statistics.median(x[0] for x in r):5.0f} ms   "
                  f"median total {statistics.median(x[1] for x in r):5.0f} ms   (n={len(r)})")


if __name__ == "__main__":
    asyncio.run(main())
