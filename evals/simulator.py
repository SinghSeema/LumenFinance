"""LLM-played borrower. It sees persona, hidden goal and opening only, never the expected outcome (E-07)."""
from __future__ import annotations

import re

HANGUP = "[HANGUP]"
# v17-dev2-fix2 S08: the simulator hung up as "[हैंगअप]" and "[कॉल समाप्त]"; the call went on.
_HANGUP_ANY = re.compile(r"\[\s*\W*(?:hang ?up|हैंगअप|हैंग अप|कॉल समाप्त|call (?:ended|end)|फ़ोन कट|फोन कट)[^\]]*\]", re.IGNORECASE)

_PROMPT = """You are role-playing a person who has just answered a phone call. The caller is a voice agent.

Today's date: {today}
Who you are: {persona}
How you start: {opening}
What you want from this call (never say this out loud): {hidden_goal}
{identity}
Rules for your replies:
- Reply with only the words you say, as a speech-to-text transcript would show them. No stage directions, no quotes, no emojis.
- Keep it short: one or two sentences, like a real phone call.
- Speak Hindi in Devanagari script, Hinglish in Roman script, English in English.
- Stay in character. Do not help the agent and do not mention that this is a test.
- When you would hang up, or the agent has said goodbye, reply with exactly {hangup}"""

_KNOWS = """Your details, if the caller asks to verify who you are: name {name}, date of birth {dob}, last four digits of your registered mobile number {last4}.
"""
_DOES_NOT_KNOW = """You do not know the account holder's date of birth or mobile number digits. If asked, you cannot give the real ones.
"""


# A bracket tag or a lone bracket instead of speech, e.g. "[EXTRACTION_RESULT]" or "[".
_GARBAGE = re.compile(r"^\[[A-Z_ ]*\]?$")


class SimulatorGarbage(Exception):
    """The simulator model broke character: an infra error, not an agent failure."""


def system_prompt(scenario: dict, borrower) -> str:
    knows = scenario.get("knows_identity", True)
    identity = (_KNOWS.format(name=borrower.name, dob=borrower.dob.strftime("%d %B %Y"),
                              last4=borrower.last4) if knows else _DOES_NOT_KNOW)
    from datetime import date as _date
    today = _date.fromisoformat(str(scenario.get("today", _date.today()))).strftime("%A %-d %B %Y")
    return _PROMPT.format(today=today, persona=scenario["persona"].strip(), opening=scenario["opening"].strip(),
                          hidden_goal=scenario["hidden_goal"].strip(), identity=identity, hangup=HANGUP)


# The simulator can run on a different provider from the agent. Groq was used up to 2026-11-04;
# its free daily cap ended the v5 dev run after 18 conversations, so the default is now Sarvam.
SIMULATOR_URLS = {"groq": "https://api.groq.com/openai/v1", "sarvam": "https://api.sarvam.ai/v2"}


class Simulator:
    def __init__(self, scenario: dict, borrower, provider: str, model: str, api_key: str,
                 temperature: float, max_retries: int):
        import openai
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=SIMULATOR_URLS[provider],
                                         max_retries=max_retries)
        self.model, self.temperature = model, temperature
        # Roles are flipped: the agent's lines are the simulator's "user" turns.
        self.messages = [{"role": "system", "content": system_prompt(scenario, borrower)}]
        self.usage = {"input": 0, "output": 0}

    async def reply(self, agent_text: str) -> str | None:
        """The borrower's next line, or None if they hang up. A garbage line ("[", "[EXTRACTION_RESULT]",
        seen in eval S10 2026-11-04) is asked for again once, then raised as an infra error."""
        self.messages.append({"role": "user", "content": agent_text or "..."})
        for attempt in (1, 2):
            r = await self.client.chat.completions.create(
                model=self.model, messages=self.messages, temperature=self.temperature, max_tokens=400)
            self.usage["input"] += r.usage.prompt_tokens
            self.usage["output"] += r.usage.completion_tokens
            text = (r.choices[0].message.content or "").strip()
            if _HANGUP_ANY.search(text) or not _GARBAGE.match(text):  # "[HANGUP]" alone also matches the
                break                                       # bracket pattern (glm, 2026-11-04)
            if attempt == 2:
                raise SimulatorGarbage(f"simulator produced {text!r} twice")
        self.messages.append({"role": "assistant", "content": text})
        if not text or _HANGUP_ANY.search(text):
            return None
        return text
