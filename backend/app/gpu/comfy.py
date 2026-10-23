"""Client for the headless ComfyUI in the `comfyui` container.

Only the scheduler's image and sound jobs call it; the scheduler has made
sure no LLM is on the card first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

import httpx

try:  # websockets >= 13
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover
    ws_connect = None


log = logging.getLogger("hub.comfy")


class ComfyError(RuntimeError):
    pass


ProgressFn = Callable[[int, int, str | None], Awaitable[None] | None]


LOADERS = {"LoadImage": "image", "LoadImageMask": "image", "LoadAudio": "audio"}   # node -> its file input
OUTPUT_KINDS = ("images", "audio")          # what saving nodes put in a job's history


def input_images(graph: dict) -> list[str]:
    """The files a graph loads from ComfyUI's input folder (pictures, sound)."""
    out = []
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") in LOADERS:
            name = (node.get("inputs") or {}).get(LOADERS[node["class_type"]])
            if isinstance(name, str) and name:
                out.append(name)
    return out


class Comfy:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None, use_ws: bool = True,
                 files: Path | None = None):
        self.url = url.rstrip("/")
        self._transport = transport
        self.use_ws = use_ws and ws_connect is not None and transport is None
        self.files = files            # ComfyUI's input/ and output/ folders as mounted in the Hub (None: not mounted)
        self._object_info: dict | None = None
        self._object_info_at = 0.0

    def client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def _get(self, path: str, timeout: float = 30, **kw) -> httpx.Response:
        try:
            async with self.client(timeout) as c:
                r = await c.get(self.url + path, **kw)
        except httpx.HTTPError as exc:
            raise ComfyError(f"ComfyUI at {self.url} is not answering ({exc.__class__.__name__})") from exc
        if r.status_code >= 400:
            raise ComfyError(f"ComfyUI {path}: {r.status_code} {r.text[:300]}")
        return r

    async def system_stats(self) -> dict:
        return (await self._get("/system_stats", timeout=10)).json()

    async def object_info(self, max_age: float = 300) -> dict:
        if self._object_info is None or time.monotonic() - self._object_info_at > max_age:
            self._object_info = (await self._get("/object_info", timeout=60)).json()
            self._object_info_at = time.monotonic()
        return self._object_info

    async def free(self) -> None:
        try:
            async with self.client(15) as c:
                await c.post(self.url + "/free", json={"unload_models": True, "free_memory": True})
        except httpx.HTTPError:
            pass        # not running = nothing on the card

    async def interrupt(self, prompt_id: str | None = None) -> None:
        try:
            async with self.client(10) as c:
                await c.post(self.url + "/interrupt", json={"prompt_id": prompt_id} if prompt_id else {})
        except httpx.HTTPError:
            pass

    async def upload_image(self, data: bytes, filename: str, mime: str = "image/png") -> str:
        try:
            async with self.client(60) as c:
                r = await c.post(self.url + "/upload/image", files={"image": (filename, data, mime)},
                                 data={"overwrite": "true", "type": "input"})
        except httpx.HTTPError as exc:
            raise ComfyError(f"upload to ComfyUI failed ({exc.__class__.__name__})") from exc
        if r.status_code >= 400:
            raise ComfyError(f"ComfyUI refused the upload: {r.status_code} {r.text[:300]}")
        body = r.json()
        return f"{body['subfolder']}/{body['name']}" if body.get("subfolder") else body["name"]

    async def queue(self, graph: dict, client_id: str) -> str:
        try:
            async with self.client(60) as c:
                r = await c.post(self.url + "/prompt", json={"prompt": graph, "client_id": client_id})
        except httpx.HTTPError as exc:
            raise ComfyError(f"ComfyUI is not answering ({exc.__class__.__name__})") from exc
        if r.status_code >= 400:
            raise ComfyError("ComfyUI refused the workflow: " + _describe_errors(r))
        return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict:
        return (await self._get(f"/history/{prompt_id}")).json().get(prompt_id) or {}

    async def view(self, filename: str, subfolder: str = "", kind: str = "output") -> bytes:
        return (await self._get("/view", timeout=120,
                                params={"filename": filename, "subfolder": subfolder, "type": kind})).content

    async def run(self, graph: dict, on_progress: ProgressFn | None = None, timeout: float = 1800) -> list[tuple[str, bytes]]:
        """Queue a workflow, wait for it, and return its output images."""
        return (await self.run_outputs(graph, on_progress, timeout))[0]

    async def run_outputs(self, graph: dict, on_progress: ProgressFn | None = None, timeout: float = 1800,
                          keep: bool = True) -> tuple[list[tuple[str, bytes]], dict[str, list[str]]]:
        """Like run(), plus the text outputs by node id (PreviewAny shows text). keep=False: ComfyUI keeps
        nothing of the job afterwards (forget())."""
        return await self.run_job(graph, on_progress, timeout, keep, kind="images")

    async def run_job(self, graph: dict, on_progress: ProgressFn | None = None, timeout: float = 1800,
                      keep: bool = True, kind: str = "images") -> tuple[list[tuple[str, bytes]], dict[str, list[str]]]:
        """Queue a workflow, wait for it, and return the files its saving nodes wrote - pictures (kind "images")
        or sound ("audio") - with the text outputs by node id."""
        client_id = uuid.uuid4().hex
        prompt_id: str | None = None
        hist: dict | None = None
        try:
            if self.use_ws:
                prompt_id = await self._run_ws(graph, client_id, on_progress, timeout)
            else:
                prompt_id = await self.queue(graph, client_id)
                await self._poll(prompt_id, timeout)
            hist = await self.history(prompt_id)
            status = hist.get("status") or {}
            if status.get("status_str") == "error":
                raise ComfyError("ComfyUI reported an error: " + _status_message(status))
            files: list[tuple[str, bytes]] = []
            texts: dict[str, list[str]] = {}
            for node_id, out in (hist.get("outputs") or {}).items():
                if isinstance(out.get("text"), list):
                    texts[str(node_id)] = [str(t) for t in out["text"]]
                for item in out.get(kind) or []:
                    if item.get("type") not in (None, "output"):
                        continue
                    data = await self.view(item["filename"], item.get("subfolder", ""), item.get("type", "output"))
                    files.append((item["filename"], data))
            if not files:
                raise ComfyError("the workflow finished without an output image" if kind == "images" else
                                 "the workflow finished without a sound file")
            return files, texts
        except asyncio.CancelledError:
            if prompt_id:
                await asyncio.shield(self.interrupt(prompt_id))
            raise
        finally:
            if not keep:
                await asyncio.shield(self.forget(graph, prompt_id, hist))

    async def forget(self, graph: dict, prompt_id: str | None, hist: dict | None = None) -> None:
        """Delete what ComfyUI keeps of a job: its history entry (the prompt and file names, in memory) and, with
        its folders mounted here, the pictures and sound the job saved and the files it loaded. Best effort."""
        files: list[tuple[str, str, str]] = [("input", "", name) for name in input_images(graph)]
        if prompt_id:
            if hist is None:
                try:
                    hist = await self.history(prompt_id)
                except ComfyError:
                    hist = None
            for out in ((hist or {}).get("outputs") or {}).values():
                for kind in OUTPUT_KINDS:
                    for item in out.get(kind) or []:
                        files.append((item.get("type") or "output", item.get("subfolder") or "",
                                      item.get("filename") or ""))
            try:
                async with self.client(10) as c:
                    await c.post(self.url + "/history", json={"delete": [prompt_id]})
            except httpx.HTTPError:
                pass
        self.delete_files(files)

    def delete_files(self, files: list[tuple[str, str, str]]) -> None:
        """(kind input/output, subfolder, name) - when ComfyUI's folders are mounted here."""
        if self.files is None:
            return
        for kind, sub, name in files:
            if kind not in ("input", "output") or not name:
                continue
            base = (self.files / kind).resolve()
            p = (base / sub / name).resolve()
            if base not in p.parents:
                continue
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("could not delete ComfyUI's %s/%s: %s", kind, name, exc)

    async def _poll(self, prompt_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hist = await self.history(prompt_id)
            status = hist.get("status") or {}
            if status.get("completed") or status.get("status_str") in ("success", "error"):
                return
            await asyncio.sleep(1.0)
        await self.interrupt(prompt_id)
        raise ComfyError(f"the ComfyUI job did not finish within {int(timeout)} s")

    async def _run_ws(self, graph: dict, client_id: str, on_progress: ProgressFn | None, timeout: float) -> str:
        ws_url = self.url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?clientId={client_id}"
        try:
            ws = await ws_connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=10)
        except Exception:                       # no websocket: poll instead
            prompt_id = await self.queue(graph, client_id)
            await self._poll(prompt_id, timeout)
            return prompt_id
        async with ws:
            prompt_id = await self.queue(graph, client_id)
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    await self.interrupt(prompt_id)
                    raise ComfyError(f"the ComfyUI job did not finish within {int(timeout)} s")
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=min(left, 30))
                except asyncio.TimeoutError:
                    hist = await self.history(prompt_id)       # missed the end?
                    if (hist.get("status") or {}).get("completed"):
                        return prompt_id
                    continue
                if isinstance(msg, bytes):
                    continue                                    # preview frames
                try:
                    ev = json.loads(msg)
                except ValueError:
                    continue
                kind, data = ev.get("type"), ev.get("data") or {}
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                if kind == "progress" and on_progress:
                    res = on_progress(int(data.get("value", 0)), int(data.get("max", 0)), data.get("node"))
                    if asyncio.iscoroutine(res):
                        await res
                elif kind == "execution_error":
                    raise ComfyError(f"{data.get('node_type', 'a node')} failed: "
                                     f"{data.get('exception_message', '').strip()[:500]}")
                elif kind == "execution_interrupted":
                    raise ComfyError("the ComfyUI job was interrupted")
                elif kind == "execution_success" and data.get("prompt_id") == prompt_id:
                    return prompt_id
                elif kind == "executing" and data.get("node") is None and data.get("prompt_id") == prompt_id:
                    return prompt_id


def _describe_errors(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"{r.status_code} {r.text[:300]}"
    parts = []
    err = body.get("error")
    if isinstance(err, dict):
        parts.append(err.get("message") or "")
        if err.get("details"):
            parts.append(str(err["details"]))
    elif err:
        parts.append(str(err))
    for node_id, ne in (body.get("node_errors") or {}).items():
        for e in ne.get("errors") or []:
            parts.append(f"node {node_id} ({ne.get('class_type')}): {e.get('message')} {e.get('details', '')}")
    return "; ".join(p for p in parts if p)[:1500] or str(r.status_code)


def _status_message(status: dict) -> str:
    for kind, data in status.get("messages") or []:
        if kind == "execution_error":
            return f"{data.get('node_type')}: {data.get('exception_message', '')}"[:500]
    return status.get("status_str") or "unknown"
