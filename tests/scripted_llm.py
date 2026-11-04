"""A fake LLM that plays back scripted turns and records every request it receives."""
import copy
import itertools

from emi_agent.llm import LLMError, LLMTurn, ToolCall

_ids = itertools.count(1)


def say(text="", *tools, fail=False):
    """One model turn: spoken text plus (tool_name, input) pairs."""
    return {"text": text, "tools": list(tools), "fail": fail}


class ScriptedLLM:
    def __init__(self, turns, tool_start=False):
        self.turns = list(turns)
        self.tool_start = tool_start  # announce each tool call's name before the turn ends
        self.requests = []  # (system, messages) as sent

    async def stream(self, system, messages, tools):
        self.requests.append((system, copy.deepcopy(messages)))
        if not self.turns:
            raise AssertionError("script ran out of turns")
        spec = self.turns.pop(0)
        if spec["fail"]:
            raise LLMError("scripted failure")
        text = spec["text"]
        for i in range(0, len(text), 7):  # stream in small chunks like a real model
            yield "text", text[i:i + 7]
        calls = [ToolCall(f"toolu_{next(_ids)}", name, inp) for name, inp in spec["tools"]]
        if self.tool_start:
            for c in calls:
                yield "tool_start", c.name
        content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
        yield "turn", LLMTurn(content=content, tool_calls=calls,
                              stop_reason="tool_use" if calls else "end_turn",
                              usage={"input": 100, "output": 20, "cache_read": 0},
                              provider="scripted")
