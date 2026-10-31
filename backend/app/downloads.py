"""Model downloads from the browser (Settings -> Models): one file at a
time, resumable (<file>.part + Range), progress kept in memory. Files come
from Hugging Face (repo + path) or, for picture models added from Civitai,
from a download URL that Civitai redirects to its storage."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .catalog import GROUPS, Catalog

CIVITAI_LOGIN = ("Civitai wants a signed-in user for this file: create an API key at civitai.com (Account settings -> "
                 "API keys), put CIVITAI_TOKEN=... in /srv/slq-hub/.env, restart the Hub (update.sh compose up -d) "
                 "and download again.")


def hf_login(repo: str, status: int = 403) -> str:
    """A gated Hugging Face file (FLUX.2 klein 9B's, 0.61.0): 401 without HF_TOKEN or with one Hugging Face does not
    take (wrong or expired), 403 when its account has not accepted the model's licence. (The queue keeps 300
    characters of an error.)"""
    if not os.environ.get("HF_TOKEN"):
        return (f"{repo} is gated: sign in on Hugging Face, accept its licence at huggingface.co/{repo}, put a read "
                "token as HF_TOKEN=... in /srv/slq-hub/.env, restart the Hub (update.sh compose up -d) and download "
                "again.")
    if status == 401:
        return (f"Hugging Face did not take the HF_TOKEN for {repo}: is it a valid read token, and has its account "
                f"accepted the licence at huggingface.co/{repo}?")
    return (f"Hugging Face refused {repo} for this HF_TOKEN: accept its licence at huggingface.co/{repo} with the "
            "account the token belongs to (it is granted at once) and download again.")


class _UserCancelled(Exception):
    """Cancel in Settings -> Models: this file stops, the queue goes on (not the task's own CancelledError, which
    ended the queue and left the other models' files waiting for ever)."""


def _source(f: dict) -> tuple[str, dict, str]:
    """(url, headers, site) for one catalog file. Tokens go only to their own
    site; httpx drops Authorization when a redirect leaves the host."""
    headers = {"User-Agent": "slq-hub/1"}
    if f.get("url"):
        host = (urlparse(f["url"]).hostname or "").lower()
        civitai = host == "civitai.com" or host.endswith(".civitai.com")
        if civitai and os.environ.get("CIVITAI_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['CIVITAI_TOKEN']}"
        return f["url"], headers, "Civitai" if civitai else host
    if os.environ.get("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    return f"https://huggingface.co/{f['repo']}/resolve/main/{f['path']}?download=true", headers, "Hugging Face"


class Downloads:
    def __init__(self, catalog: Catalog, transport: httpx.AsyncBaseTransport | None = None):
        self.catalog = catalog
        self._transport = transport
        self.queue: list[tuple[str, dict]] = []       # (entry id, file)
        self.state: dict[str, dict] = {}              # dest -> progress
        self._task: asyncio.Task | None = None
        self._cancel: set[str] = set()

    def snapshot(self) -> dict:
        return {"active": self._task is not None and not self._task.done(),
                "files": list(self.state.values())}

    def enqueue(self, entry_id: str) -> list[str]:
        entry = None
        for group in GROUPS:
            entry = self.catalog.get(group, entry_id)
            if entry:
                break
        if not entry:
            raise KeyError(entry_id)
        added = []
        deps = [entry]
        for dep in entry.get("requires", []):
            d = next((x for x in (self.catalog.get(g, dep) for g in GROUPS) if x), None)
            if d:
                deps.insert(0, d)
        self._cancel.difference_update(e["id"] for e in deps)     # asked for again: an earlier Cancel is over
        for e in deps:
            for f in e["files"]:
                dest = f["dest"]
                if self.catalog.path(f).is_file():
                    continue
                if any(q[1]["dest"] == dest for q in self.queue) or \
                        self.state.get(dest, {}).get("status") == "downloading":
                    continue
                self.queue.append((e["id"], f))
                self.state[dest] = {"model": e["id"], "dest": dest, "status": "queued", "done": 0,
                                    "total": f.get("size") or 0, "rate": 0, "error": None}
                added.append(dest)
        if added and (self._task is None or self._task.done()):
            self._task = asyncio.get_running_loop().create_task(self._run())
        return added

    def cancel(self, entry_id: str) -> None:
        self._cancel.add(entry_id)
        self.queue = [q for q in self.queue if q[0] != entry_id]
        for st in self.state.values():
            if st["model"] == entry_id and st["status"] == "queued":
                st["status"] = "cancelled"

    async def _run(self) -> None:
        while self.queue:
            entry_id, f = self.queue.pop(0)
            st = self.state[f["dest"]]
            if entry_id in self._cancel:
                st["status"] = "cancelled"
                continue
            try:
                await self._download(entry_id, f, st)
                st["status"] = "done"
            except _UserCancelled:
                st["status"] = "cancelled"
            except asyncio.CancelledError:
                st["status"] = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001
                st["status"] = "failed"
                st["error"] = str(exc)[:300]
        self._cancel.clear()

    async def _download(self, entry_id: str, f: dict, st: dict) -> None:
        dest: Path = self.catalog.path(f)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        url, headers, site = _source(f)
        st["status"] = "downloading"
        for attempt in range(1, 9):
            have = part.stat().st_size if part.exists() else 0
            h = dict(headers)
            if have:
                h["Range"] = f"bytes={have}-"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30), follow_redirects=True,
                                             transport=self._transport) as c:
                    async with c.stream("GET", url, headers=h) as r:
                        if r.status_code == 416:            # already complete
                            break
                        if r.status_code in (401, 403) and site == "Civitai":
                            raise RuntimeError(CIVITAI_LOGIN)
                        if r.status_code in (401, 403) and site == "Hugging Face":
                            raise RuntimeError(hf_login(f["repo"], r.status_code))
                        if r.status_code >= 400:
                            raise RuntimeError(f"{site} answered {r.status_code}")
                        if have and r.status_code != 206:
                            have = 0
                        total = have + int(r.headers.get("content-length") or 0)
                        st["total"] = total or st["total"]
                        mode = "ab" if have else "wb"
                        t0, done0 = time.monotonic(), have
                        with open(part, mode) as out:
                            async for chunk in r.aiter_bytes(4 * 1024 * 1024):
                                if entry_id in self._cancel:
                                    raise _UserCancelled()
                                out.write(chunk)
                                have += len(chunk)
                                st["done"] = have
                                st["rate"] = int((have - done0) / max(0.001, time.monotonic() - t0))
                if not st["total"] or have >= st["total"]:
                    break
            except (httpx.HTTPError, OSError) as exc:
                st["error"] = f"{exc.__class__.__name__} - retrying ({attempt}/8)"
                await asyncio.sleep(min(60, 5 * attempt))
        else:
            raise RuntimeError("gave up after 8 attempts")
        os.replace(part, dest)
        st["error"] = None
