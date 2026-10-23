#!/usr/bin/env python3
"""Download SLQ Hub's models from Hugging Face (standard library only).

Reads backend/app/catalog/models.json. Resumes interrupted downloads
(<file>.part + HTTP Range), shows progress, and skips files that are
already complete. Used by install.sh; the Hub's Settings -> Models page
does the same from the browser.

    python3 tools/models.py list
    python3 tools/models.py pull --defaults            everything marked default
    python3 tools/models.py pull qwen3.8-27b flux2-klein-4b
    python3 tools/models.py pull --defaults --root /srv/slq-hub/models

HF_TOKEN in the environment is sent along (only gated models need it).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "backend" / "app" / "catalog" / "models.json"
CHUNK = 4 * 1024 * 1024
RETRIES = 8


def load_catalog(path: Path = CATALOG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def all_entries(cat: dict) -> list[dict]:
    out = []
    for group in ("llm", "aux", "comfy", "audio"):
        for e in cat.get(group, []):
            out.append({**e, "group": group})
    return out


def file_url(f: dict) -> str:
    return f"https://huggingface.co/{f['repo']}/resolve/main/{f['path']}?download=true"


def _request(url: str, start: int = 0, method: str = "GET") -> urllib.request.Request:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "slq-hub-models/1"})
    if start:
        req.add_header("Range", f"bytes={start}-")
    token = os.environ.get("HF_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def remote_size(url: str) -> int | None:
    try:
        with urllib.request.urlopen(_request(url, method="HEAD"), timeout=60) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n} B"


def download(f: dict, models_root: Path) -> str:
    """'ok' when the file is complete (downloaded now or before)."""
    dest = models_root / f["dest"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = file_url(f)
    size = remote_size(url)
    if dest.is_file() and (size is None or dest.stat().st_size == size):
        print(f"  [OK]   {f['dest']} ({human(dest.stat().st_size)}, already here)")
        return "ok"
    part = dest.with_name(dest.name + ".part")
    expected = size or f.get("size") or 0
    for attempt in range(1, RETRIES + 1):
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:
            part.unlink()
            have = 0
        if size and have == size:
            break
        try:
            with urllib.request.urlopen(_request(url, have), timeout=120) as r:
                if have and r.status != 206:      # no range support: start over
                    have = 0
                    mode = "wb"
                else:
                    mode = "ab" if have else "wb"
                with open(part, mode) as out:
                    t0, last, done0 = time.monotonic(), 0.0, have
                    while True:
                        buf = r.read(CHUNK)
                        if not buf:
                            break
                        out.write(buf)
                        have += len(buf)
                        now = time.monotonic()
                        if now - last >= 1.0 or (expected and have >= expected):
                            last = now
                            rate = (have - done0) / max(0.001, now - t0)
                            pct = f"{100 * have / expected:5.1f}%" if expected else "  ?  "
                            eta = (f"{int((expected - have) / rate // 60)}m{int((expected - have) / rate % 60):02d}s"
                                   if expected and rate > 0 else "")
                            sys.stdout.write(f"\r  ...    {f['dest']}  {pct}  {human(have)}"
                                             f"  {human(rate)}/s  {eta}     ")
                            sys.stdout.flush()
            sys.stdout.write("\n")
            if size is None or have >= size:
                break
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            sys.stdout.write("\n")
            wait = min(60, 5 * attempt)
            print(f"  [WARN] {f['dest']}: {exc} - retry {attempt}/{RETRIES} in {wait} s")
            time.sleep(wait)
    else:
        print(f"  [FAIL] {f['dest']}: gave up after {RETRIES} attempts")
        return "failed"
    final = part.stat().st_size
    if size and final != size:
        print(f"  [FAIL] {f['dest']}: got {final} bytes, expected {size}")
        return "failed"
    os.replace(part, dest)
    print(f"  [OK]   {f['dest']} ({human(final)})")
    return "ok"


def pick(cat: dict, ids: list[str], defaults: bool) -> list[dict]:
    entries = all_entries(cat)
    by_id = {e["id"]: e for e in entries}
    chosen: list[dict] = []
    wanted = [e["id"] for e in entries if e.get("default")] if defaults else []
    wanted += ids
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        raise SystemExit(f"unknown model id(s): {', '.join(unknown)} - see `models.py list`")
    seen: set[str] = set()

    def add(i: str) -> None:
        if i in seen:
            return
        seen.add(i)
        for dep in by_id[i].get("requires", []):
            add(dep)
        chosen.append(by_id[i])

    for i in wanted:
        add(i)
    return chosen


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SLQ Hub model downloads")
    p.add_argument("--catalog", type=Path, default=CATALOG)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pl = sub.add_parser("pull")
    pl.add_argument("ids", nargs="*")
    pl.add_argument("--defaults", action="store_true")
    pl.add_argument("--root", type=Path, default=Path(os.environ.get("HUB_MODELS_ROOT", "/srv/slq-hub/models")))
    args = p.parse_args(argv)
    cat = load_catalog(args.catalog)

    if args.cmd == "list":
        for e in all_entries(cat):
            total = sum(f.get("size", 0) for f in e["files"])
            print(f"  {e['id']:<22} {'default' if e.get('default') else '       '}  "
                  f"{human(total):>9}  {e['name']} - {e.get('description', '')}")
        return 0

    chosen = pick(cat, args.ids, args.defaults)
    if not chosen:
        print("Nothing to download (give model ids or --defaults).")
        return 0
    files = [f for e in chosen for f in e["files"]]
    need = sum(f.get("size", 0) for f in files
               if not (args.root / f["dest"]).is_file())
    args.root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(args.root).free
    print(f"Models: {', '.join(e['id'] for e in chosen)}")
    print(f"About {human(need)} to download into {args.root} ({human(free)} free)")
    if need > free:
        print("  [FAIL] not enough disk space")
        return 1
    failed = [f["dest"] for f in files if download(f, args.root) != "ok"]
    if failed:
        print(f"\n{len(failed)} file(s) failed - run the same command again to resume:")
        for d in failed:
            print(f"  {d}")
        return 1
    print("\nAll model files are in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
