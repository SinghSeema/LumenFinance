"""LLM adapters for the Brain. History is kept in Anthropic message format; Groq converts it.

Each adapter's stream() yields ("text", chunk) pieces, then exactly one ("turn", LLMTurn).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from loguru import logger


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class LLMTurn:
    content: list[dict]       # assistant content blocks, replayed unchanged next request
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict
    provider: str


class AnthropicLLM:
    def __init__(self, model: str, api_key: str, max_tokens: int, effort: str):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model, self.max_tokens, self.effort = model, max_tokens, effort

    async def stream(self, system: str, messages: list, tools: list) -> AsyncIterator:
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                tools=tools,
                output_config={"effort": self.effort},
            ) as s:
                async for event in s:
                    if event.type == "text":
                        yield "text", event.text
                final = await s.get_final_message()
        except self._anthropic.APIError as e:
            raise LLMError(f"anthropic: {e}") from e
        if final.stop_reason == "refusal":
            raise LLMError(f"anthropic refusal: {getattr(final, 'stop_details', None)}")
        if final.stop_reason == "max_tokens" and any(b.type == "tool_use" for b in final.content):
            raise LLMError("anthropic: tool input truncated at max_tokens")
        u = final.usage
        yield "turn", LLMTurn(
            content=[b.model_dump(mode="json", exclude_none=True) for b in final.content],
            tool_calls=[ToolCall(b.id, b.name, b.input) for b in final.content if b.type == "tool_use"],
            stop_reason=final.stop_reason,
            usage={"input": u.input_tokens, "output": u.output_tokens,
                   "cache_read": u.cache_read_input_tokens or 0},
            provider=f"anthropic:{self.model}",
        )


def to_openai(system: str, messages: list) -> list[dict]:
    """Anthropic-format history to OpenAI chat format. Thinking blocks are dropped."""
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if m["role"] == "system" or isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "user":
            for b in content:
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
            continue
        text = "".join(b["text"] for b in content if b["type"] == "text")
        calls = [{"id": b["id"], "type": "function",
                  "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                 for b in content if b["type"] == "tool_use"]
        msg: dict = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = calls
        out.append(msg)
    return out


class GroqLLM:
    def __init__(self, model: str, api_key: str, max_tokens: int, temperature: float,
                 max_retries: int = 2, reasoning_effort: str | None = None,
                 timeout: float | None = None):
        import groq
        self._groq = groq
        extra = {"timeout": timeout} if timeout else {}  # None keeps the SDK default (60 s)
        self.client = groq.AsyncGroq(api_key=api_key, max_retries=max_retries, **extra)
        _ = self.client.chat.completions  # load the SDK's chat module now, not mid-call (C53)
        self.model, self.max_tokens, self.temperature = model, max_tokens, temperature
        # gpt-oss thinks before answering; None leaves Groq's default (medium).
        self.extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}

    async def stream(self, system: str, messages: list, tools: list) -> AsyncIterator:
        try:
            resp = await self.client.chat.completions.create(
                model=self.model, messages=to_openai(system, messages), tools=openai_tools(tools),
                max_tokens=self.max_tokens, temperature=self.temperature, stream=True, **self.extra)
            async for item in accumulate(resp, f"groq:{self.model}", _groq_usage):
                yield item
        except (self._groq.APIError, httpx.HTTPError) as e:  # httpx: a timeout mid-stream
            raise LLMError(f"groq: {type(e).__name__} {e}") from e


def _groq_usage(chunk) -> dict | None:
    x = getattr(chunk, "x_groq", None)
    if x is not None and getattr(x, "usage", None):
        return {"input": x.usage.prompt_tokens, "output": x.usage.completion_tokens, "cache_read": 0}
    return None


def _openai_usage(chunk) -> dict | None:
    u = getattr(chunk, "usage", None)
    return {"input": u.prompt_tokens, "output": u.completion_tokens, "cache_read": 0} if u else None


def openai_tools(tools: list) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools]


async def accumulate(resp, provider: str, usage_of) -> AsyncIterator:
    """OpenAI-style stream chunks -> ("text", piece)... then one ("turn", LLMTurn). Also
    ("tool_start", name) the moment a tool call's name arrives, so the caller can hear a short
    line instead of silence while the tool runs and the model writes the reply."""
    text, calls, usage, finish = [], {}, {}, None
    async for chunk in resp:
        usage = usage_of(chunk) or usage
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        finish = choice.finish_reason or finish
        if choice.delta.content:
            text.append(choice.delta.content)
            yield "text", choice.delta.content
        for tc in choice.delta.tool_calls or []:
            # Gemini sends each call whole and may omit the index; Groq streams pieces by index.
            key = tc.index if tc.index is not None else len(calls)
            acc = calls.setdefault(key, {"id": None, "name": "", "args": ""})
            acc["id"] = tc.id or acc["id"]
            if tc.function and tc.function.name:
                if not acc["name"]:
                    yield "tool_start", tc.function.name
                acc["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                acc["args"] += tc.function.arguments
    tool_calls = []
    for i in sorted(calls):
        try:
            args = json.loads(calls[i]["args"] or "{}")
        except json.JSONDecodeError:
            args = {}  # the tool gate rejects this as invalid_arguments
        tool_calls.append(ToolCall(calls[i]["id"] or f"call_{i}", calls[i]["name"], args))
    content = ([{"type": "text", "text": "".join(text)}] if text else []) + [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tool_calls]
    yield "turn", LLMTurn(content, tool_calls, finish or "stop",
                          usage or {"input": 0, "output": 0, "cache_read": 0}, provider)


# OpenAI-compatible chat endpoints, by provider name in config.
OPENAI_COMPAT_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",  # Google AI Studio key
    "sarvam": "https://api.sarvam.ai/v2",                                   # same key as Sarvam TTS
}


class OpenAICompatLLM:
    """Any provider with an OpenAI-style /chat/completions endpoint (Gemini, Sarvam)."""

    def __init__(self, provider: str, model: str, api_key: str, max_tokens: int,
                 temperature: float, reasoning_effort: str | None = None,
                 timeout: float | None = None):
        import openai
        self._openai = openai
        self.provider = provider
        extra = {"timeout": timeout} if timeout else {}
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=OPENAI_COMPAT_URLS[provider],
                                         max_retries=0, **extra)
        # The SDK imports its chat module on first use: 240 ms that blocked the event loop during the
        # first reply of a call (measured 2026-11-04; live call B004 20:13, turn 1 got no filler, C53).
        _ = self.client.chat.completions
        self.model, self.max_tokens, self.temperature = model, max_tokens, temperature
        self.extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}

    async def stream(self, system: str, messages: list, tools: list) -> AsyncIterator:
        try:
            resp = await self.client.chat.completions.create(
                model=self.model, messages=to_openai(system, messages), tools=openai_tools(tools),
                max_tokens=self.max_tokens, temperature=self.temperature, stream=True,
                stream_options={"include_usage": True}, **self.extra)
            async for item in accumulate(resp, f"{self.provider}:{self.model}", _openai_usage):
                yield item
        except (self._openai.APIError, httpx.HTTPError) as e:  # httpx: a timeout mid-stream
            raise LLMError(f"{self.provider}: {type(e).__name__} {e}") from e


def _ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


class FallbackLLM:
    """Tries providers in order. Falls over only if nothing has been spoken yet."""

    def __init__(self, providers: list):
        self.providers = providers

    async def stream(self, system: str, messages: list, tools: list) -> AsyncIterator:
        errors = []
        for provider in self.providers:
            spoke, start = False, time.monotonic()
            name = getattr(provider, "model", type(provider).__name__)
            try:
                async for kind, value in provider.stream(system, messages, tools):
                    if kind == "text" and not spoke:
                        logger.info(f"llm {name}: first text after {_ms(start)} ms")
                    spoke = spoke or kind == "text"
                    if kind == "turn":
                        logger.info(f"llm {name}: done in {_ms(start)} ms, tools "
                                    f"{[c.name for c in value.tool_calls]}, tokens {value.usage}")
                    yield kind, value
                return
            except LLMError as e:
                logger.warning(f"llm {name}: failed after {_ms(start)} ms: {str(e)[:300]}")
                if spoke:
                    raise
                errors.append(str(e))
        raise LLMError("all LLM providers failed: " + " | ".join(errors))


def _make_one(spec: dict, config: dict):
    llm_cfg = config["llm"]
    provider = spec["provider"]
    if provider == "anthropic":
        key = llm_cfg.get("anthropic_api_key") or _missing("ANTHROPIC_API_KEY")
        return AnthropicLLM(spec["model"], key, llm_cfg["max_tokens"], spec.get("effort", llm_cfg["effort"]))
    if provider == "groq":
        key = llm_cfg.get("groq_api_key") or _missing("GROQ_API_KEY")
        return GroqLLM(spec["model"], key, llm_cfg["max_tokens"],
                       spec.get("temperature", llm_cfg["temperature"]),
                       spec.get("max_retries", llm_cfg["max_retries"]),
                       spec.get("reasoning_effort", llm_cfg["reasoning_effort"]),
                       llm_cfg["timeout_secs"])
    if provider in OPENAI_COMPAT_URLS:
        key = {"gemini": llm_cfg.get("gemini_api_key"),
               "sarvam": config["tts"].get("sarvam_api_key")}[provider]
        key = key or _missing(f"{provider.upper()}_API_KEY")
        return OpenAICompatLLM(provider, spec["model"], key, llm_cfg["max_tokens"],
                               spec.get("temperature", llm_cfg["temperature"]),
                               spec.get("reasoning_effort"), llm_cfg["timeout_secs"])
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def build_llm(config: dict) -> FallbackLLM:
    """The primary provider must have its key (fail fast); a fallback without a key is skipped loudly."""
    llm_cfg = config["llm"]
    providers = [_make_one({"provider": llm_cfg["provider"], "model": llm_cfg["model"]}, config)]
    for spec in llm_cfg.get("fallback") or []:
        try:
            providers.append(_make_one(spec, config))
        except EnvironmentError as e:
            print(f"WARNING: LLM fallback {spec['provider']} disabled: {e}")
    return FallbackLLM(providers)


def _missing(env_name: str):
    raise EnvironmentError(f"{env_name} is not set. Add it to your .env file (see .env.example).")
