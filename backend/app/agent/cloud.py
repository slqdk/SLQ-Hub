"""Cloud models for escalation: Anthropic (Claude) or any OpenAI-compatible
endpoint (DeepSeek, OpenRouter, ...). Used only by web chats in projects
where cloud escalation is switched on - never by the model API."""
from __future__ import annotations

import base64
import json
from typing import AsyncIterator

import httpx

from ..llm.client import LlmError
from ..llm.stream import Accumulator


def configured(settings) -> bool:
    c = settings.get("cloud")
    if not c.get("api_key"):
        return False
    return c.get("provider") == "anthropic" or bool(c.get("base_url"))


def _to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts, out = [], []

    def push(role: str, blocks: list[dict]):
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role = m.get("role")
        c = m.get("content")
        if role == "system":
            system_parts.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or []))
            continue
        if role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": str(c or "")}])
            continue
        blocks: list[dict] = []
        if isinstance(c, str):
            if c:
                blocks.append({"type": "text", "text": c})
        else:
            for p in c or []:
                if p.get("type") == "text" and p.get("text"):
                    blocks.append({"type": "text", "text": p["text"]})
                elif p.get("type") == "image_url":
                    url = p["image_url"]["url"]
                    head, _, data = url.partition(",")
                    mime = head[5:].split(";")[0] if head.startswith("data:") else "image/png"
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"), "input": args})
        if blocks:
            push("assistant" if role == "assistant" else "user", blocks)
    return "\n\n".join(system_parts), out


class CloudClient:
    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = settings.get("cloud")
        self._transport = transport

    @property
    def label(self) -> str:
        return f"{self.cfg.get('provider')}:{self.cfg.get('model')}"

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[tuple[str, dict]]:
        """Yields the same small events as Accumulator.feed; finally ('result', {...})."""
        if self.cfg.get("provider") == "anthropic":
            async for ev in self._anthropic(messages, tools):
                yield ev
        else:
            async for ev in self._openai(messages, tools):
                yield ev

    async def _openai(self, messages, tools):
        acc = Accumulator()
        body = {"model": self.cfg.get("model"), "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}, "max_tokens": int(self.cfg.get("max_tokens") or 16000)}
        if tools:
            body["tools"] = tools
        url = self.cfg.get("base_url", "").rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20), transport=self._transport) as c:
            async with c.stream("POST", url, json=body,
                                headers={"Authorization": f"Bearer {self.cfg.get('api_key')}"}) as r:
                if r.status_code >= 400:
                    raise LlmError(f"cloud model: {r.status_code} {(await r.aread())[:300]!r}", r.status_code)
                async for line in r.aiter_lines():
                    if not line.startswith("data:") or line[5:].strip() in ("", "[DONE]"):
                        continue
                    try:
                        chunk = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    for ev in acc.feed(chunk):
                        yield ev
        yield "result", acc.result()

    async def _anthropic(self, messages, tools):
        system, msgs = _to_anthropic(messages)
        body: dict = {"model": self.cfg.get("model"), "max_tokens": int(self.cfg.get("max_tokens") or 16000),
                      "messages": msgs, "stream": True}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                              "input_schema": t["function"].get("parameters") or {"type": "object"}} for t in tools]
        headers = {"x-api-key": self.cfg.get("api_key"), "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        content, calls, usage, stop = "", {}, {}, None
        current: dict | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20), transport=self._transport) as c:
            async with c.stream("POST", "https://api.anthropic.com/v1/messages", json=body, headers=headers) as r:
                if r.status_code >= 400:
                    raise LlmError(f"Claude: {r.status_code} {(await r.aread())[:400]!r}", r.status_code)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    t = ev.get("type")
                    if t == "message_start":
                        usage.update((ev.get("message") or {}).get("usage") or {})
                    elif t == "content_block_start":
                        b = ev.get("content_block") or {}
                        if b.get("type") == "tool_use":
                            current = {"id": b.get("id"), "name": b.get("name"), "arguments": ""}
                            calls[ev.get("index")] = current
                            yield "tool_start", {"id": current["id"], "name": current["name"]}
                        else:
                            current = None
                    elif t == "content_block_delta":
                        d = ev.get("delta") or {}
                        if d.get("type") == "text_delta":
                            content += d.get("text", "")
                            yield "text", {"text": d.get("text", "")}
                        elif d.get("type") == "thinking_delta":
                            yield "thinking", {"text": d.get("thinking", "")}
                        elif d.get("type") == "input_json_delta" and current is not None:
                            current["arguments"] += d.get("partial_json", "")
                            yield "tool_args", {"id": current["id"], "delta": d.get("partial_json", "")}
                    elif t == "message_delta":
                        usage.update(ev.get("usage") or {})
                        stop = (ev.get("delta") or {}).get("stop_reason")
                    elif t == "error":
                        raise LlmError(f"Claude: {ev.get('error', {}).get('message')}")
        tool_calls = [calls[i] for i in sorted(calls)]
        yield "result", {"content": content, "reasoning": "", "tool_calls": tool_calls,
                         "finish_reason": "tool_calls" if tool_calls else ("length" if stop == "max_tokens" else "stop"),
                         "usage": {"prompt_tokens": usage.get("input_tokens", 0),
                                   "completion_tokens": usage.get("output_tokens", 0)}, "timings": {}}


def image_block_b64(data: bytes, mime: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}}
