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

from . import tokens
from .catalog import GROUPS, Catalog

CIVITAI_LOGIN = ("Civitai wants a signed-in user for this file: create an API key at civitai.com (Account settings -> "
                 "API keys), add it under Settings -> Models -> Sign-ins for downloads and download again.")


def hf_login(repo: str, status: int = 403) -> str:
    """A gated Hugging Face file (FLUX.2 klein 9B's, 0.61.0): 401 without a token or with one Hugging Face does not
    take (wrong or expired), 403 when its account has not accepted the model's licence. The token is set in
    Settings -> Models -> Sign-ins for downloads (0.64.0; or HF_TOKEN in .env). (The queue keeps 300 characters of an
    error.)"""
    if not tokens.hf():
        return (f"{repo} is gated: sign in on Hugging Face, accept its licence at huggingface.co/{repo}, add a read "
                "token of that account under Settings -> Models -> Sign-ins for downloads, and download again.")
    if status == 401:
        return (f"Hugging Face did not take the token for {repo}: is it a valid read token (Settings -> Models -> "
                f"Sign-ins for downloads), and has its account accepted the licence at huggingface.co/{repo}?")
    return (f"Hugging Face refused {repo} for this token: accept its licence at huggingface.co/{repo} with the "
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
        if civitai and tokens.civitai():
            headers["Authorization"] = f"Bearer {tokens.civitai()}"
        if host == "huggingface.co" and tokens.hf():          # a link to a fixed revision (a workflow's, 0.67.0)
            headers["Authorization"] = f"Bearer {tokens.hf()}"
        return f["url"], headers, "Civitai" if civitai else "Hugging Face" if host == "huggingface.co" else host
    if tokens.hf():
        headers["Authorization"] = f"Bearer {tokens.hf()}"
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
                if not f.get("repo") and not f.get("url"):          # uploaded from the computer (0.62.0)
                    raise ValueError(f"{Path(dest).name} was uploaded from a computer and cannot be downloaded: "
                                     "upload it again (Upload).")
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

    def enqueue_files(self, owner: str, files: list[dict]) -> list[str]:
        """Files that belong to no catalog model (0.67.0: a workflow's missing models), under the name `owner`
        ("workflow:<id>") - each {"dest", "repo" and "path" or "url", "size"}. Cancel takes the same name."""
        added = []
        self._cancel.discard(owner)
        for f in files:
            dest = f["dest"]
            if self.catalog.path(f).is_file():
                continue
            if any(q[1]["dest"] == dest for q in self.queue) or \
                    self.state.get(dest, {}).get("status") == "downloading":
                continue
            self.queue.append((owner, f))
            self.state[dest] = {"model": owner, "dest": dest, "status": "queued", "done": 0,
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

    def _training_copy(self, f: dict) -> Path | None:
        """The same file of the same Hugging Face repository, fetched already for a LoRA training into Hugging Face's
        cache (models/hf, app/training/runs.py) - at exactly the catalog's size, which only exact sizes match.
        FLUX.2 [klein] 9B uncensored's text encoder is 16.4 GB for its pictures and for its training (0.63.0)."""
        repo, path, size = str(f.get("repo") or ""), str(f.get("path") or ""), int(f.get("size") or 0)
        if not repo or not path or not size or ".." in Path(path).parts or path.startswith("/"):
            return None
        snaps = self.catalog.path({"dest": f"hf/hub/models--{repo.replace('/', '--')}/snapshots"})
        try:
            for snap in sorted(snaps.iterdir()) if snaps.is_dir() else []:
                p = snap / path
                if p.is_file() and p.stat().st_size == size:
                    return p
        except OSError:
            pass
        return None

    def _link(self, f: dict, dest: Path, st: dict) -> bool:
        """A hard link to the training's copy instead of a second download (one file on disk for both). False when
        there is none, or the two folders are on different file systems."""
        src = self._training_copy(f)
        if src is None:
            return False
        tmp = dest.with_name(dest.name + ".link")
        try:
            tmp.unlink(missing_ok=True)
            os.link(src.resolve(strict=True), tmp)     # the data itself: Linux links a symlink, not its target
            os.replace(tmp, dest)
        except OSError:
            tmp.unlink(missing_ok=True)
            return False
        dest.with_name(dest.name + ".part").unlink(missing_ok=True)       # a download begun before
        st["done"] = st["total"] = int(f["size"])
        return True

    async def _download(self, entry_id: str, f: dict, st: dict) -> None:
        dest: Path = self.catalog.path(f)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._link(f, dest, st):
            st["error"] = None
            return
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
                            repo = f.get("repo") or "/".join(urlparse(f.get("url") or "").path.split("/")[1:3])
                            raise RuntimeError(hf_login(repo, r.status_code))
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
