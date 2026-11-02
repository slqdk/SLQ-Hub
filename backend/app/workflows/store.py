"""The owner's ComfyUI workflows (0.67.0, Settings -> Models -> Workflows): each a JSON file in data/workflows -
the workflow in API format, the file it came from, where its model files come from, and the inputs the owner
lets the assistant set (with a name and a line on each)."""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_FILE = 20 * 1024 * 1024            # a workflow is JSON (a picture with one in it can be a few MB)
MAX_NAME, MAX_DESCRIPTION, MAX_LABEL = 80, 600, 200


class WorkflowStoreError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Workflows:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "workflows"
        self._lock = threading.Lock()
        self._read: dict[str, tuple[float, int, dict]] = {}     # path -> (mtime, size, workflow): read once

    def _path(self, wid: str) -> Path:
        if not ID_RE.match(wid or ""):
            raise WorkflowStoreError("No such workflow.", 404)
        return self.dir / f"{wid}.json"

    def _write(self, wf: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self._path(wf["id"])
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(wf, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def list(self) -> list[dict]:
        """Every workflow (copies). Each file is read again only when it changed: every turn's tool list asks."""
        out = []
        for p in sorted(self.dir.glob("*.json")) if self.dir.is_dir() else []:
            try:
                st = p.stat()
                cached = self._read.get(str(p))
                if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                    wf = cached[2]
                else:
                    wf = json.loads(p.read_text(encoding="utf-8"))
                    self._read[str(p)] = (st.st_mtime, st.st_size, wf)
            except (OSError, ValueError):
                continue
            if isinstance(wf, dict) and ID_RE.match(str(wf.get("id") or "")):
                out.append(copy.deepcopy(wf))
        return sorted(out, key=lambda w: (str(w.get("name") or "").lower(), w["id"]))

    def get(self, wid: str) -> dict:
        p = self._path(wid)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WorkflowStoreError("No such workflow.", 404)
        except (OSError, ValueError):
            raise WorkflowStoreError("That workflow's file is damaged.", 500)

    def add(self, *, name: str, prompt: dict, source: dict, sources: dict, inputs: list[dict]) -> dict:
        with self._lock:
            wid = secrets.token_hex(6)
            now = time.time()
            wf = {"id": wid, "name": (name.strip() or "Workflow")[:MAX_NAME], "description": "", "created": now,
                  "updated": now, "source": source, "prompt": prompt, "sources": sources, "inputs": inputs}
            self._write(wf)
            return wf

    def update(self, wid: str, *, name: str | None = None, description: str | None = None,
               inputs: dict | None = None) -> dict:
        """inputs: key -> {"on", "label", "description"} for the inputs the workflow has; others are ignored."""
        with self._lock:
            wf = self.get(wid)
            if name is not None:
                wf["name"] = re.sub(r"\s+", " ", name).strip()[:MAX_NAME] or wf["name"]
            if description is not None:
                wf["description"] = re.sub(r"\s+", " ", description).strip()[:MAX_DESCRIPTION]
            if inputs is not None:
                for item in wf.get("inputs") or []:
                    change = inputs.get(item["key"])
                    if not isinstance(change, dict):
                        continue
                    if isinstance(change.get("on"), bool):
                        item["on"] = change["on"]
                    for k, limit in (("label", MAX_LABEL), ("description", MAX_DESCRIPTION)):
                        if isinstance(change.get(k), str):
                            item[k] = re.sub(r"\s+", " ", change[k]).strip()[:limit]
            wf["updated"] = time.time()
            self._write(wf)
            return wf

    def delete(self, wid: str) -> None:
        with self._lock:
            p = self._path(wid)
            if not p.is_file():
                raise WorkflowStoreError("No such workflow.", 404)
            p.unlink()
