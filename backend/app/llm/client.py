"""HTTP client for llama-server's OpenAI-compatible API (and its extras).

Every call here is made by code that already holds a scheduler ticket for
the residency the server belongs to (CPU-only aux servers excepted).
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Callable

import httpx


class LlmError(RuntimeError):
    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def _error_text(body: bytes, status: int) -> str:
    try:
        data = json.loads(body)
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)
    except ValueError:
        return body.decode("utf-8", "replace")[:500] or f"HTTP {status}"


class LlmClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        # Called when a model server cannot be reached at all (crashed?), so the
        # scheduler checks it before sending the next job there.
        self.on_unreachable: Callable[[], None] | None = None

    def _unreachable(self, exc: Exception) -> LlmError:
        if self.on_unreachable and isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)):
            try:
                self.on_unreachable()
            except Exception:  # noqa: BLE001
                pass
        return LlmError(f"the model server is not answering ({exc.__class__.__name__})", 502)

    def client(self, timeout: httpx.Timeout | float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout if timeout is not None else httpx.Timeout(None, connect=10),
                                 transport=self._transport)

    async def stream_chat(self, base_url: str, payload: dict) -> AsyncIterator[dict]:
        """POST /v1/chat/completions with stream=true; yields each chunk as a dict."""
        body = {**payload, "stream": True}
        body.setdefault("stream_options", {"include_usage": True})
        try:
            async with self.client() as c:
                async with c.stream("POST", base_url + "/v1/chat/completions", json=body) as r:
                    if r.status_code >= 400:
                        raise LlmError(_error_text(await r.aread(), r.status_code), r.status_code)
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        if isinstance(chunk, dict) and chunk.get("error"):
                            err = chunk["error"]
                            raise LlmError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                        yield chunk
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

    async def end_reasoning(self, base_url: str, cmpl_id: str) -> bool:
        """End the thinking of a completion that is running now (0.30.0: Stop thinking): llama-server's reasoning
        control forces the budget's message and the end of thinking, and the model goes on with its answer. It
        starts nothing - the turn that holds the scheduler's ticket for this server asks it about its own request.
        Whether the server did it (False: no such completion running, or no reasoning control on it)."""
        try:
            async with self.client(timeout=10) as c:
                r = await c.post(base_url + "/v1/chat/completions/control",
                                 json={"id": cmpl_id, "action": "reasoning_end"})
        except httpx.HTTPError:
            return False
        if r.status_code >= 400:
            return False
        try:
            return bool(r.json().get("success"))
        except (ValueError, AttributeError):
            return False

    async def chat(self, base_url: str, payload: dict) -> dict:
        body = {**payload, "stream": False}
        body.pop("stream_options", None)
        try:
            async with self.client() as c:
                r = await c.post(base_url + "/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if r.status_code >= 400:
            raise LlmError(_error_text(r.content, r.status_code), r.status_code)
        return r.json()

    async def tokenize(self, base_url: str, text: str) -> list[int]:
        async with self.client(30) as c:
            r = await c.post(base_url + "/tokenize", json={"content": text, "add_special": False,
                                                           "parse_special": True})
        if r.status_code >= 400:
            raise LlmError(_error_text(r.content, r.status_code), r.status_code)
        return r.json().get("tokens", [])

    async def apply_template(self, base_url: str, messages: list[dict], tools: list | None = None) -> str:
        body: dict = {"messages": messages}
        if tools:
            body["tools"] = tools
        async with self.client(30) as c:
            r = await c.post(base_url + "/apply-template", json=body)
        if r.status_code >= 400:
            raise LlmError(_error_text(r.content, r.status_code), r.status_code)
        return r.json().get("prompt", "")

    async def count_tokens(self, base_url: str, messages: list[dict], tools: list | None = None) -> int:
        return len(await self.tokenize(base_url, await self.apply_template(base_url, messages, tools)))

    async def embeddings(self, base_url: str, inputs: list) -> list[list[float]]:
        async with self.client(300) as c:
            r = await c.post(base_url + "/v1/embeddings", json={"input": inputs})
        if r.status_code >= 400:
            raise LlmError(_error_text(r.content, r.status_code), r.status_code)
        data = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]

    async def rerank(self, base_url: str, query: str, documents: list[str]) -> list[dict]:
        async with self.client(300) as c:
            r = await c.post(base_url + "/v1/rerank", json={"query": query, "documents": documents,
                                                            "top_n": len(documents)})
        if r.status_code >= 400:
            raise LlmError(_error_text(r.content, r.status_code), r.status_code)
        return r.json().get("results", [])

    async def health(self, base_url: str) -> bool:
        try:
            async with self.client(3) as c:
                return (await c.get(base_url + "/health")).status_code == 200
        except httpx.HTTPError:
            return False
