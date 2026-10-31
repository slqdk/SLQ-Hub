"""Model files from the owner's computer (0.62.0): Settings -> Models -> Upload.

A model file is 5-30 GB, so it is sent in pieces (at most CHUNK_MAX each; the browser sends 32 MiB). One request
then passes the limit of the proxy in front of the Hub (Nginx Proxy Manager's client_max_body_size, 3200m in
INSTALL.md), and a lost connection, a closed page or an update that restarts the Hub costs one piece, not the file.

An upload is two files in models/.uploads: <id>.part, what has arrived, and <id>.json, its name, size and the
file's date on the computer. It lies on the models disk, so `take` moves it into place with a rename. The browser
first asks for the upload of the same name, size and date (`create` gives that one back: resume), then sends the
rest - a piece is written only at the offset where the file ends now. Nobody took it within KEEP_S: deleted.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

CHUNK_MAX = 128 * 1024 * 1024
WRITE_BLOCK = 4 * 1024 * 1024            # what is gathered before it goes to the disk (in a thread)
MAX_SIZE = 200 * 1000**3                 # far more than any model the card can run
KEEP_S = 3 * 86400
SPARE = 5 * 1024**3                      # the disk keeps this much free, as for downloads (system_routes._disk_check)
BUSY_S = 120                             # an upload that got a piece this recently holds an update back
ID_RE = re.compile(r"^[0-9a-f]{24}$")


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def file_name(name: str) -> str:
    """The file's own name, as the browser sends it - never a path."""
    return Path(str(name or "").replace("\\", "/")).name.strip()[:200]


class Uploads:
    def __init__(self, models_dir: Path):
        self.dir = Path(models_dir) / ".uploads"
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}        # id -> when its last piece came

    # ------------------------------------------------------------ files
    def part(self, uid: str) -> Path:
        if not ID_RE.match(uid or ""):
            raise UploadError("No such upload.", 404)
        return self.dir / f"{uid}.part"

    def _meta_path(self, uid: str) -> Path:
        return self.part(uid).with_suffix(".json")

    def get(self, uid: str) -> dict:
        part, meta = self.part(uid), self._meta_path(uid)
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            received = part.stat().st_size
        except (OSError, ValueError):
            raise UploadError("No such upload (it was finished, cancelled or too old).", 404)
        return {**m, "id": uid, "received": received, "done": received == m["size"]}

    def list(self) -> list[dict]:
        out = []
        for meta in sorted(self.dir.glob("*.json")) if self.dir.is_dir() else []:
            try:
                out.append(self.get(meta.stem))
            except UploadError:
                pass
        return out

    # ------------------------------------------------------------ an upload's life
    def create(self, name: str, size: int, modified: int | None = None) -> dict:
        """A new upload - or the one of the same file begun before, to go on with (name, size and date match)."""
        name = file_name(name)
        if not name:
            raise UploadError("The file has no name.")
        try:
            size = int(size)
        except (TypeError, ValueError):
            raise UploadError("The size must be a number of bytes.")
        if not 0 < size <= MAX_SIZE:
            raise UploadError(f"A file of {size} bytes cannot be uploaded.")
        modified = int(modified) if isinstance(modified, (int, float)) and not isinstance(modified, bool) else None
        self.cleanup()
        for m in self.list():
            if m["name"] == name and m["size"] == size and m.get("modified") == modified:
                return m
        self.dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.dir).free
        if size > free - SPARE:
            raise UploadError(f"Not enough disk space: {name} is {size / 1e9:.1f} GB, {free / 1e9:.1f} GB is free and "
                              "the Hub keeps 5 GB spare.", 507)
        uid = secrets.token_hex(12)
        self.part(uid).touch()
        self._meta_path(uid).write_text(json.dumps({"name": name, "size": size, "modified": modified,
                                                    "created": time.time()}), encoding="utf-8")
        return self.get(uid)

    async def append(self, uid: str, offset: int, chunks) -> dict:
        """One piece, from `offset` - which must be where the file ends now. What arrives is written even when the
        connection breaks in the middle: the browser asks how much the Hub has and goes on from there."""
        lock = self._locks.setdefault(uid, asyncio.Lock())
        async with lock:
            m = self.get(uid)
            if offset != m["received"]:
                raise UploadError(f"The Hub has {m['received']} bytes of {m['name']}, not {offset}: go on from "
                                  "there.", 409)
            room = min(m["size"] - offset, CHUNK_MAX)
            written, buf, too_long = 0, bytearray(), False
            with open(self.part(uid), "r+b") as out:
                out.seek(offset)
                try:
                    async for chunk in chunks:
                        if written + len(buf) + len(chunk) > room:
                            too_long = True
                            break
                        buf += chunk
                        if len(buf) >= WRITE_BLOCK:
                            await asyncio.to_thread(out.write, bytes(buf))
                            written += len(buf)
                            buf.clear()
                finally:
                    if too_long:                         # the whole piece is refused, not a part of it
                        out.truncate(offset)
                    elif buf:                            # what arrived - also when the connection broke
                        out.write(bytes(buf))
                    self._last[uid] = time.time()
            if too_long:
                raise UploadError(f"That piece goes past the end of {m['name']} ({m['size']} bytes) or is larger than "
                                  f"{CHUNK_MAX // 1024**2} MiB.")
            return self.get(uid)

    def done(self, uid: str) -> dict:
        m = self.get(uid)
        if not m["done"]:
            raise UploadError(f"{m['name']} has not all arrived yet ({m['received']} of {m['size']} bytes).", 409)
        return m

    def take(self, uid: str, dest: Path) -> dict:
        """A finished upload into its place on the models disk (a rename). Returns what it was, for `give_back`."""
        m = self.done(uid)
        if dest.exists():
            raise UploadError(f"There is a file at {dest.name} already.", 409)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.part(uid), dest)
        self._meta_path(uid).unlink(missing_ok=True)
        self._forget(uid)
        return m

    def give_back(self, m: dict, dest: Path) -> None:
        """Undo `take` when what was to use the file failed: the upload is there again, to try once more."""
        try:
            os.replace(dest, self.part(m["id"]))
            self._meta_path(m["id"]).write_text(json.dumps({k: m[k] for k in ("name", "size", "modified", "created")}),
                                                encoding="utf-8")
        except OSError:
            pass

    def delete(self, uid: str) -> None:
        self.part(uid).unlink(missing_ok=True)
        self._meta_path(uid).unlink(missing_ok=True)
        self._forget(uid)

    def _forget(self, uid: str) -> None:
        self._locks.pop(uid, None)
        self._last.pop(uid, None)

    def cleanup(self) -> None:
        """Uploads nobody took (nor went on with) within KEEP_S, and pieces whose record is gone."""
        if not self.dir.is_dir():
            return
        now = time.time()
        for p in self.dir.iterdir():
            try:
                if p.suffix == ".part" and not p.with_suffix(".json").is_file() and now - p.stat().st_mtime > 3600:
                    p.unlink()
                elif p.suffix == ".json" and now - max(p.stat().st_mtime, (p.with_suffix(".part").stat().st_mtime
                                                       if p.with_suffix(".part").exists() else 0)) > KEEP_S:
                    p.with_suffix(".part").unlink(missing_ok=True)
                    p.unlink()
            except OSError:
                pass

    def head(self, uid: str, n: int) -> bytes:
        """The first n bytes that have arrived (fewer while it is still coming)."""
        with open(self.part(uid), "rb") as f:
            return f.read(n)

    def busy(self) -> list[str]:
        """The uploads that are coming in now (a piece within BUSY_S) - the updater waits for them."""
        now = time.time()
        names = []
        for uid, t in list(self._last.items()):
            if now - t < BUSY_S:
                try:
                    m = self.get(uid)
                    if not m["done"]:
                        names.append(m["name"])
                except UploadError:
                    self._forget(uid)
        return names
