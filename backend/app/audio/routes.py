"""The speech samples of the last self-test (selftest --speech), for Settings -> Assistant -> Sound: the same
sentence in each speech-speed setting, with what each cost, to listen to before choosing. Owner only (people's
sessions reach only MEMBER_ROUTES)."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..state import get_hub

router = APIRouter()
SAMPLE = re.compile(r"^speech-[a-z0-9][a-z0-9._-]*\.mp3$")


def _folder(request: Request):
    return get_hub(request).env.data_dir / "selftest"


@router.get("/hub/api/sound/samples")
async def samples(request: Request):
    folder = _folder(request)
    try:
        meta = json.loads((folder / "speech.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    out = []
    for m in meta.get("samples") or []:             # in the self-test's order; only files it described (the
        if not isinstance(m, dict) or not SAMPLE.match(str(m.get("file") or "")):    # 0.13.1 run wrote no list,
            continue                                                                 # and its settings differ)
        p = folder / m["file"]
        if not p.is_file():
            continue
        out.append({"file": p.name, "model": m.get("model"), "name": m.get("name"), "setting": m.get("setting"),
                    "text": m.get("text"), "seconds": m.get("seconds"), "compute": m.get("compute"),
                    "factor": m.get("factor"), "capped": bool(m.get("capped")), "size": p.stat().st_size})
    return {"at": meta.get("at"), "cpu": meta.get("cpu"), "threads": meta.get("threads"), "samples": out}


@router.get("/hub/api/sound/samples/{name}")
async def sample(name: str, request: Request):
    p = _folder(request) / name
    if not SAMPLE.match(name) or not p.is_file():
        raise HTTPException(404, "No such sample.")
    return FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
