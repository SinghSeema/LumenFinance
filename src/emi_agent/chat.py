"""Talk to the Brain in the terminal: python -m emi_agent.chat --borrower B001 [--profile groq_dev]"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from emi_agent import config as config_mod
from emi_agent.brain import Brain
from emi_agent.data import load_borrowers
from emi_agent.llm import build_llm


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--borrower", default="B001")
    p.add_argument("--profile")
    p.add_argument("--set", action="append", default=[], dest="overrides")
    p.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = p.parse_args()

    config_mod.load_dotenv()
    cfg = config_mod.load(args.profile, args.overrides)
    borrower = load_borrowers()[args.borrower]
    brain = Brain(cfg, borrower, args.today, build_llm(cfg),
                  calls_path=Path(cfg["behavior"]["calls_log"]))
    print(f"[{cfg['llm']['provider']}:{cfg['llm']['model']} | today {args.today} | "
          f"{borrower.borrower_id} {borrower.name}]  Ctrl-D to hang up.\n")
    print(f"agent> {brain.greeting}")
    while not brain.should_hang_up:
        try:
            text = input("you>   ").strip()
        except EOFError:
            break
        if not text:
            continue
        async for s in brain.respond(text):
            tag = f"  [guard: {s.guard}]" if s.guard else ""
            print(f"agent> {s.text}{tag}")
    record = brain.finish()
    print("\noutcome:", record.model_dump_json(indent=2))
    print(f"stage trail: {[c['stage'] + ':' + c['name'] for c in brain.state.tool_calls]}")
    print(f"guard hits: {len(brain.state.guard_hits)} | tokens: {brain.usage}")


if __name__ == "__main__":
    asyncio.run(main())
