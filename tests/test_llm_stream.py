"""llm.accumulate: a tool call is announced as soon as its name streams in (no dead air)."""
import asyncio
from types import SimpleNamespace as NS

from emi_agent.llm import accumulate


def _chunk(content=None, name=None, args=None, finish=None):
    tc = [NS(index=0, id="c1", function=NS(name=name, arguments=args))] if (name or args) else None
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tc), finish_reason=finish)], usage=None)


async def _stream(chunks):
    for c in chunks:
        yield c


def test_tool_start_comes_before_the_arguments_finish():
    chunks = [_chunk(name="record_outcome"), _chunk(args='{"outcome": '), _chunk(args='"will_pay"}', finish="tool_calls")]
    out = asyncio.run(_collect(accumulate(_stream(chunks), "x", lambda c: None)))
    assert out[0] == ("tool_start", "record_outcome")
    assert out[-1][0] == "turn" and out[-1][1].tool_calls[0].input == {"outcome": "will_pay"}
    assert sum(k == "tool_start" for k, _ in out) == 1


async def _collect(agen):
    return [x async for x in agen]
