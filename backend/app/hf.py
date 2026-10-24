"""Hugging Face lookups for Settings -> Models -> Add from Hugging Face.

`lookup()` lists a model repository's GGUF files and groups them into the
choices a person makes: one quantisation (split files kept together), and,
apart from it, the vision projectors (mmproj). `make_entry()` turns a choice
into a catalog entry like the bundled ones; the Hub's own download queue then
fetches the files. Only public repositories work without a token; HF_TOKEN in
the environment (the VM's .env) is sent along for gated ones.
"""
from __future__ import annotations

import datetime as dt
import os
import re

import httpx

HF = "https://huggingface.co"
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]{0,200}$")
SPLIT_RE = re.compile(r"^(?P<stem>.+)-(?P<n>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)
QUANT_RE = re.compile(r"(?i)(?:^|[-_.])((?:UD-)?(?:I?Q\d[A-Z0-9_]*|BF16|F16|F32|MXFP4(?:_MOE)?|TQ\d_\d))$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


class HFError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_repo(text: str) -> str:
    """owner/name from 'owner/name' or a huggingface.co / hf.co link."""
    t = (text or "").strip()
    t = re.sub(r"^(https?://)?(www\.)?(huggingface\.co|hf\.co)/", "", t, flags=re.I)
    t = t.split("?")[0].split("#")[0].strip().strip("/")
    parts = [p for p in t.split("/") if p]
    if parts and parts[0].lower() in ("datasets", "spaces", "docs", "api"):
        raise HFError("That is not a model repository. Give a model as owner/name.")
    repo = "/".join(parts[:2])
    if not REPO_RE.match(repo) or ".." in repo:
        raise HFError("Give a Hugging Face model as owner/name (e.g. unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF) "
                      "or paste its link.")
    return repo


def safe_path(path: str) -> bool:
    """A repository path the Hub will write under its models folder."""
    segs = (path or "").split("/")
    return bool(segs) and len(path) < 400 and all(SEGMENT_RE.match(s) and s not in (".", "..") for s in segs)


def quant_label(stem: str) -> str:
    m = QUANT_RE.search(stem)
    return m.group(1) if m else stem


def _headers() -> dict:
    h = {"User-Agent": "slq-hub/1", "Accept": "application/json"}
    if os.environ.get("HF_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    return h


def _raise_for(r: httpx.Response, repo: str) -> None:
    if r.status_code in (401, 403):
        raise HFError(f"{repo} is gated or private. Accept its terms on huggingface.co, put an access token in "
                      "/srv/slq-hub/.env as HF_TOKEN=..., and restart the Hub (update.sh compose up -d).", 403)
    if r.status_code == 404:
        raise HFError(f"Hugging Face has no model called {repo}.", 404)
    if r.status_code >= 400:
        raise HFError(f"Hugging Face answered {r.status_code} for {repo}.", 502)


async def _tree(c: httpx.AsyncClient, repo: str) -> list[dict]:
    url: str | None = f"{HF}/api/models/{repo}/tree/main?recursive=true"
    items: list[dict] = []
    for _ in range(20):                                  # paginated (Link: rel="next")
        if not url:
            break
        r = await c.get(url, headers=_headers())
        _raise_for(r, repo)
        page = r.json()
        if not isinstance(page, list):
            raise HFError("Hugging Face sent something unexpected for the file list.", 502)
        items += [x for x in page if isinstance(x, dict)]
        url = (r.links.get("next") or {}).get("url")
        if url and not url.startswith(f"{HF}/api/models/{repo}/tree/"):
            url = None                                     # only ever follow Hugging Face's own next page
    return items


async def _meta(c: httpx.AsyncClient, repo: str) -> dict:
    """Architecture, size and what the chat template supports; {} if unknown."""
    try:
        r = await c.get(f"{HF}/api/models/{repo}", params={"expand[]": "gguf"}, headers=_headers())
        if r.status_code >= 400:
            return {}
        g = (r.json() or {}).get("gguf") or {}
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(g, dict) or not g:
        return {}
    tpl = g.get("chat_template") or ""
    if isinstance(tpl, list):                             # several named templates
        tpl = " ".join(str(t.get("template", "")) for t in tpl if isinstance(t, dict))
    tpl = str(tpl)
    return {"architecture": g.get("architecture"), "context_length": g.get("context_length"),
            "parameters": g.get("total"),
            "tools": ("tools" in tpl) if tpl else None,
            "thinking": ("enable_thinking" in tpl) if tpl else None}


HEAD_RE = re.compile(r"(?i)(?<![a-z])(?:fast)?mtp(?![a-z])|(?<![a-z])draft(?![a-z])")    # not "noMTP"
SIDE_RE = re.compile(r"(?i)vision|projector")


def _skip(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return (not base.endswith(".gguf") or "imatrix" in base or base.startswith("mtp-")
            or path.lower().startswith("mtp/") or not safe_path(path))


def _tell_apart(label: str, cs: list[dict]) -> None:
    """Labels for files of one quant: by what their names do not share (Model-Q4_K_M and Model-noMTP-Q4_K_M:
    Q4_K_M and noMTP-Q4_K_M), and by folder where they are in different ones."""
    stems = [re.sub(r"(?i)\.gguf$", "", SPLIT_RE.sub(r"\g<stem>", c["key"].rsplit("/", 1)[-1])) for c in cs]
    pres = [st[:len(st) - len(label)] if st.lower().endswith(label.lower()) else st for st in stems]
    head = os.path.commonprefix(pres)
    head = head[:max(head.rfind("-"), head.rfind("_"), head.rfind("."), -1) + 1]
    where = [c["key"].rsplit("/", 1)[0] if "/" in c["key"] else "top level" for c in cs]
    for c, pre, w in zip(cs, pres, where, strict=True):
        name = pre[len(head):] + label
        c["label"] = f"{name} ({w})" if len(set(where)) > 1 else name


def _label_projectors(mmproj: list[dict]) -> None:
    """Distinct labels when several projector files would otherwise read the same (by folder, else numbered)."""
    same: dict[str, list[dict]] = {}
    for m in mmproj:
        same.setdefault(m["label"], []).append(m)
    for label, ms in same.items():
        if len(ms) < 2:
            continue
        where = [m["path"].rsplit("/", 1)[0] if "/" in m["path"] else "top level" for m in ms]
        if len(set(where)) == len(ms):
            for m, w in zip(ms, where, strict=True):
                m["label"] = f"{label} ({w})"
        else:
            for i, m in enumerate(ms, 1):
                m["label"] = f"{label} #{i}"


def group(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(model choices, vision projectors) from a repository tree."""
    files = [(x["path"], int((x.get("lfs") or {}).get("size") or x.get("size") or 0))
             for x in items if x.get("type") == "file" and isinstance(x.get("path"), str)]
    choices: list[dict] = []
    mmproj: list[dict] = []
    splits: dict[tuple[str, str, int], dict[int, tuple[str, int]]] = {}
    biggest = max((size for path, size in files if path.lower().endswith(".gguf")), default=0)
    for path, size in files:
        if _skip(path):
            continue
        d, _, base = path.rpartition("/")
        if size < biggest / 8 and HEAD_RE.search(base):    # an MTP head or a draft model for speculative decoding
            continue
        side = quant_label(base[:-5]).upper() in ("F16", "BF16", "F32", "Q8_0", base[:-5].upper())
        if "mmproj" in base.lower() or (side and size < biggest / 4 and SIDE_RE.search(base)):   # Model-vision-f16
            label = re.sub(r"(?i)\.gguf$", "", base)
            label = quant_label(re.sub(r"(?i)^.*(?:mmproj|vision|projector)[-_.]?", "", label) or label)
            mmproj.append({"path": path, "size": size, "label": label})
            continue
        m = SPLIT_RE.match(base)
        if m:
            splits.setdefault((d, m["stem"], int(m["total"])), {})[int(m["n"])] = (path, size)
            continue
        choices.append({"key": path, "label": quant_label(base[:-5]), "split": False,
                        "files": [{"path": path, "size": size}], "size": size})
    for (d, stem, total), parts in splits.items():
        if sorted(parts) != list(range(1, total + 1)):
            continue                                       # an incomplete split: not loadable
        fs = [{"path": parts[n][0], "size": parts[n][1]} for n in range(1, total + 1)]
        choices.append({"key": fs[0]["path"], "label": quant_label(stem), "split": True, "files": fs,
                        "size": sum(f["size"] for f in fs)})
    same: dict[str, list[dict]] = {}
    for c in choices:
        same.setdefault(c["label"], []).append(c)
    for label, cs in same.items():                         # one quant, several files: tell them apart
        if len(cs) > 1:
            _tell_apart(label, cs)
    _label_projectors(mmproj)
    choices.sort(key=lambda c: c["size"])
    mmproj.sort(key=lambda m: m["size"])
    return choices, mmproj


def suggest(repo: str) -> dict:
    name = repo.split("/", 1)[1]
    name = re.sub(r"(?i)[-_.]?gguf$", "", name) or name
    mid = re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-.")[:60] or "model"
    return {"id": mid, "name": re.sub(r"[-_]+", " ", name).strip()}


async def lookup(repo: str, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                                 transport=transport) as c:
        try:
            items = await _tree(c, repo)
            meta = await _meta(c, repo)
        except httpx.HTTPError as exc:
            raise HFError(f"Could not reach Hugging Face ({exc.__class__.__name__}).", 502)
    choices, mmproj = group(items)
    if not choices:
        raise HFError(f"{repo} has no GGUF model files. The Hub runs GGUF models with llama.cpp; look for a "
                      "repository ending in -GGUF (unsloth, bartowski and ggml-org publish many).")
    params = meta.get("parameters")
    if isinstance(params, (int, float)) and params > 0 and min(c["size"] for c in choices) * 8 / params > 40:
        meta = {**meta, "parameters": None}     # more bits/param than even F32 (32): HF counted a small side file,
        #                                         e.g. an MTP head - not these weights. (A real F32 repo clears 40.)
    return {"repo": repo, "url": f"{HF}/{repo}", "choices": choices, "mmproj": mmproj, "meta": meta,
            "suggested": suggest(repo)}


def looks_like_repo(text: str) -> bool:
    """A repository (owner/name or a link) rather than words to search for."""
    try:
        parse_repo(text)
        return True
    except HFError:
        return False


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9.]+", " ", text.lower()).strip() + " "


async def search(query: str, transport: httpx.AsyncBaseTransport | None = None, limit: int = 20) -> list[dict]:
    """GGUF repositories whose name holds every word of the query, the most
    downloaded first. The API matches one string, so it is asked for the
    longest word and the rest are matched here ("Ornith 1.5 9B" finds
    ornith-ai/Ornith-1.5-9B-GGUF and the requantisations of it)."""
    words = [w for w in _words(query).split() if w]
    if not words:
        return []
    params = {"search": max(words, key=len), "filter": "gguf", "sort": "downloads", "direction": "-1",
              "limit": "100"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), follow_redirects=True,
                                 transport=transport) as c:
        try:
            r = await c.get(f"{HF}/api/models", params=params, headers=_headers())
        except httpx.HTTPError as exc:
            raise HFError(f"Could not reach Hugging Face ({exc.__class__.__name__}).", 502)
    if r.status_code >= 400:
        raise HFError(f"Hugging Face's search answered {r.status_code}.", 502)
    try:
        items = r.json()
    except ValueError:
        raise HFError("Hugging Face's search sent something that is not JSON.", 502)
    out = []
    for it in items if isinstance(items, list) else []:
        repo = str(it.get("id") or it.get("modelId") or "")
        name = _words(repo)
        if not REPO_RE.match(repo) or not all(f" {w} " in name or w in name.replace(" ", "") for w in words):
            continue
        out.append({"repo": repo, "downloads": int(it.get("downloads") or 0), "likes": int(it.get("likes") or 0),
                    "updated": it.get("lastModified") or it.get("createdAt") or None,
                    "gated": bool(it.get("gated"))})
        if len(out) >= limit:
            break
    return out


def make_entry(repo: str, choice: dict, mmproj: dict | None, model_id: str, name: str, ctx: int,
               tools: bool, thinking: bool, meta: dict) -> dict:
    """A catalog entry like the bundled ones: files under llm/hf/<owner>--<name>/."""
    if not ID_RE.match(model_id):
        raise HFError("The name for apps may use a-z, 0-9, dots, dashes and underscores (up to 63).")
    base = f"llm/hf/{repo.replace('/', '--')}"
    files = []
    for i, f in enumerate(choice["files"]):
        if not safe_path(f["path"]):
            raise HFError(f"Unsafe file name in the repository: {f['path']}")
        entry_file = {"repo": repo, "path": f["path"], "dest": f"{base}/{f['path']}", "size": f["size"]}
        if i:
            entry_file["role"] = "part"                    # llama.cpp finds the parts next to the first
        files.append(entry_file)
    if mmproj:
        if not safe_path(mmproj["path"]):
            raise HFError(f"Unsafe file name in the repository: {mmproj['path']}")
        files.append({"repo": repo, "path": mmproj["path"], "dest": f"{base}/{mmproj['path']}",
                      "size": mmproj["size"], "role": "mmproj"})
    caps = ["chat"] + (["vision"] if mmproj else []) + (["tools"] if tools else []) + (["thinking"] if thinking else [])
    params = meta.get("parameters")
    entry = {
        "id": model_id,
        "name": (name or model_id).strip()[:80],
        "description": f"Added from Hugging Face: {repo} ({choice['label']}).",
        "default": False,
        "source": {"repo": repo, "url": f"{HF}/{repo}", "file": choice["key"],
                   "added": dt.date.today().isoformat()},
        "files": files,
        "server": {"ctx": int(ctx), "parallel": 2, "kv_unified": True, "cache_type": "q8_0", "gpu_layers": "all",
                   "flash_attn": "on", "cache_ram_mib": 2048, "extra": []},
        "context_per_request": int(ctx),
        "capabilities": caps,
        "aliases": [],
        "quantization": choice["label"],
    }
    if isinstance(params, (int, float)) and params > 0:
        entry["parameter_size"] = f"{params / 1e9:.1f}B"
    if meta.get("architecture"):
        entry["architecture"] = str(meta["architecture"])      # picks the family's sampling (catalog.py)
    if thinking:
        entry["thinking"] = {"default": False, "efforts": [], "default_effort": None}
    return entry
