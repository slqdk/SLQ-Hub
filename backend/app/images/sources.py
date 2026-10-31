"""Picture models from a link (Settings -> Models -> Add image model): an SDXL
checkpoint on Hugging Face or Civitai - SDXL itself and the families built on
it (Pony, Illustrious, NoobAI) with their many finetunes.

lookup() reads what a link points at and says which files (Hugging Face) or
versions (Civitai) the Hub can run. make_entry() turns the choice into a
catalog entry (family "sdxl") with its own sampler settings, from a preset
per kind that can be changed later; the download queue then fetches the file
to models/comfy/checkpoints/hub/<id>.safetensors.

Hugging Face says nothing about a file's architecture, so the chosen file's
header is read (two Range requests, a few hundred KB of a 7 GB file): an SDXL
checkpoint has the UNet with SDXL's 2816-wide class embedding, both text
encoders and the VAE. Civitai's model API names each version's base model.
Gated or login-only files need HF_TOKEN or CIVITAI_TOKEN in the VM's .env.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from urllib.parse import parse_qs, quote, urlparse

import httpx

from .. import hf
from . import workflows as W

CIVITAI = "https://civitai.com"
# Civitai's names for base models with the SDXL architecture (src/server/common/constants.ts).
SDXL_BASES = {"SDXL 0.9", "SDXL 1.0", "SDXL 1.0 LCM", "SDXL Distilled", "SDXL Turbo", "SDXL Lightning", "SDXL Hyper",
              "Pony", "Illustrious", "NoobAI"}
CHECKPOINT_MIN = 1_500_000_000            # an fp16 SDXL checkpoint is 6.5-7 GB, fp32 about 13 GB
CHECKPOINT_MAX = 16_000_000_000
HEADER_MAX = 64 * 1024 * 1024
COMPONENT_DIRS = {"unet", "text_encoder", "text_encoder_2", "vae", "tokenizer", "tokenizer_2", "transformer",
                  "scheduler", "vae_decoder", "vae_encoder"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
CIVITAI_TOKEN_HELP = ("Civitai wants a signed-in user for this file. Create an API key at civitai.com (Account "
                      "settings -> API keys), put CIVITAI_TOKEN=... in /srv/slq-hub/.env and restart the Hub "
                      "(update.sh compose up -d).")

# Settings per kind of SDXL model, from where they are documented:
#   sdxl:        ComfyUI's official SDXL template (image_sdxl_simple.json); its negative prompt without the two
#                words that belonged to its black-and-white example.
#   pony:        the Pony Diffusion V6 XL model card: the score tags, clip skip 2, Euler a, 25 steps; it names no
#                CFG, so A1111's default of 7; "designed to not need negative prompts".
#   illustrious: the NoobAI-XL model card: its quality tags and negative prompt (without nsfw and the furry
#                terms), Euler a, CFG 4-5, 28-35 steps.
PRESETS: dict[str, dict] = {
    "sdxl": {"label": "SDXL (photo and general)", "steps": 25, "cfg": 7.0, "sampler": "dpmpp_2m",
             "scheduler": "karras", "clip_skip": 1, "prefix": "",
             "negative": "lowres, blurry, out of focus, deformed, bad anatomy, extra limbs, mutated, watermark, text, "
                         "logo, signature"},
    "pony": {"label": "Pony", "steps": 25, "cfg": 7.0, "sampler": "euler_ancestral", "scheduler": "normal",
             "clip_skip": 2, "prefix": "score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up",
             "negative": ""},
    "illustrious": {"label": "Illustrious / NoobAI", "steps": 28, "cfg": 5.0, "sampler": "euler_ancestral",
                    "scheduler": "normal", "clip_skip": 1, "prefix": "masterpiece, best quality, newest, absurdres, highres",
                    "negative": "worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, "
                                "mutated hands"},
}


class SourceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- links

def parse_link(text: str) -> dict:
    """{"site": "hf", "repo", "file"} or {"site": "civitai", "model_id", "version_id"}."""
    t = (text or "").strip()
    if not t:
        raise SourceError("Paste a link to a model on Hugging Face or Civitai.")
    if "://" not in t and "civitai" not in t.lower() and re.match(r"^[A-Za-z0-9][\w.-]*/[\w.-]+", t):
        t = "https://huggingface.co/" + t                   # owner/name
    u = urlparse(t if "://" in t else "https://" + t)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    if host in ("huggingface.co", "www.huggingface.co", "hf.co"):
        repo = hf.parse_repo("/".join(parts[:2]))
        file = None
        if len(parts) > 4 and parts[2] in ("blob", "resolve"):
            file = "/".join(parts[4:])                      # parts[3] is the revision
        return {"site": "hf", "repo": repo, "file": file}
    if re.fullmatch(r"(www\.)?civitai\.(com|green|red)", host):
        q = parse_qs(u.query)
        mid = vid = None
        if len(parts) >= 2 and parts[0] == "models" and parts[1].isdigit():
            mid = int(parts[1])
        if parts[:3] == ["api", "download", "models"] and len(parts) >= 4 and parts[3].isdigit():
            vid = int(parts[3])
        if parts[:2] == ["api", "v1"] and len(parts) >= 4 and parts[3].isdigit():
            if parts[2] == "models":
                mid = int(parts[3])
            elif parts[2] == "model-versions":
                vid = int(parts[3])
        mv = (q.get("modelVersionId") or [""])[0]
        if mv.isdigit():
            vid = int(mv)
        if mid is None and vid is None:
            raise SourceError("That Civitai link names no model. Use the model's page, e.g. "
                              "https://civitai.com/models/257749.")
        return {"site": "civitai", "model_id": mid, "version_id": vid}
    raise SourceError("The Hub adds picture models from Hugging Face and Civitai links.")


def preset_for(text: str) -> str:
    """The kind of SDXL model from Civitai's base model, or from a name."""
    t = (text or "").lower()
    if "pony" in t:
        return "pony"
    if "illustrious" in t or "noob" in t:
        return "illustrious"
    return "sdxl"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", (text or "").lower()).strip("-.")[:60] or "image-model"


# ---------------------------------------------------------------- safetensors headers

async def _range(c: httpx.AsyncClient, url: str, headers: dict, start: int, end: int) -> bytes:
    """Bytes start..end of a file; a server that ignores Range still only
    gets read that far."""
    want = end - start + 1
    async with c.stream("GET", url, headers={**headers, "Range": f"bytes={start}-{end}"}) as r:
        if r.status_code in (401, 403):
            raise SourceError("The file is gated or private: accept its terms on huggingface.co and put an access "
                              "token in /srv/slq-hub/.env as HF_TOKEN=..., then restart the Hub.", 403)
        if r.status_code >= 400:
            raise SourceError(f"Reading the file's header failed ({r.status_code}).", 502)
        skip = start if r.status_code == 200 else 0
        buf = bytearray()
        async for chunk in r.aiter_bytes(65536):
            if skip:
                drop = min(skip, len(chunk))
                chunk, skip = chunk[drop:], skip - drop
            buf += chunk
            if len(buf) >= want:
                break
    return bytes(buf[:want])


async def sniff(c: httpx.AsyncClient, url: str, headers: dict) -> dict:
    """The JSON header of a .safetensors file: tensor names and shapes."""
    head = await _range(c, url, headers, 0, 7)
    if len(head) < 8:
        raise SourceError("Could not read the file's header.", 502)
    n = int.from_bytes(head, "little")
    if not 2 <= n <= HEADER_MAX:
        raise SourceError("That is not a .safetensors file the Hub can read.")
    raw = await _range(c, url, headers, 8, 8 + n - 1)
    try:
        header = json.loads(raw)
    except ValueError:
        raise SourceError("That is not a .safetensors file the Hub can read.")
    if not isinstance(header, dict):
        raise SourceError("That is not a .safetensors file the Hub can read.")
    return header


def classify(header: dict) -> tuple[bool, str]:
    """(an SDXL checkpoint the Hub can run, a note): the note says why not, or
    that it is a v-prediction model (ComfyUI reads that from the file)."""
    keys = [k for k in header if k != "__metadata__"]

    def has(prefix: str) -> bool:
        return any(k.startswith(prefix) for k in keys)

    label = (header.get("model.diffusion_model.label_emb.0.0.weight") or {}).get("shape") or []
    if label and label[-1] == 2816 and has("model.diffusion_model.input_blocks."):
        if not has("conditioner.embedders.1."):
            return False, ("This file has SDXL's image model but not its text encoders. Pick the full checkpoint "
                           "(about 6.5-7 GB).")
        if not has("first_stage_model."):
            return False, "This SDXL checkpoint has no VAE baked in. Pick a version that has one."
        return True, "v-prediction" if "v_pred" in header else ""
    if label and label[-1] == 2560:
        return False, "This is the SDXL refiner; it does not make pictures on its own."
    if has("double_blocks.") or has("model.diffusion_model.double_blocks."):
        return False, "This is a FLUX-type model, not SDXL."
    if has("cond_stage_model.") and has("model.diffusion_model."):
        return False, ("This is a Stable Diffusion 1.x or 2.x checkpoint. The Hub runs SDXL ones (SDXL, Pony, "
                       "Illustrious, NoobAI).")
    if any("lora_" in k for k in keys[:50]):
        return False, "This is a LoRA, not a whole model."
    return False, ("The Hub cannot tell what this file is. It runs SDXL checkpoints: one .safetensors with the image "
                   "model, both text encoders and the VAE.")


# ---------------------------------------------------------------- Hugging Face

def _source_path_ok(path: str) -> bool:
    segs = (path or "").split("/")
    return (0 < len(path) < 400 and all(s not in ("", ".", "..") for s in segs)
            and not any(ord(ch) < 32 for ch in path))


async def _hf(c: httpx.AsyncClient, repo: str, file: str | None, pick: str | None) -> dict:
    items = await hf._tree(c, repo)
    files = [(x["path"], int((x.get("lfs") or {}).get("size") or x.get("size") or 0))
             for x in items if x.get("type") == "file" and isinstance(x.get("path"), str)]
    options = [{"key": path, "label": path, "size": size, "supported": None, "reason": "", "base": "",
                "preset": preset_for(f"{repo} {path}")}
               for path, size in sorted(files)
               if path.lower().endswith(".safetensors") and CHECKPOINT_MIN <= size <= CHECKPOINT_MAX
               and _source_path_ok(path) and path.split("/")[0] not in COMPONENT_DIRS]
    if not options:
        if any(path == "model_index.json" for path, _ in files):
            raise SourceError(f"{repo} holds its model in diffusers format (folders), not as one .safetensors "
                              "checkpoint. Look for the single file on the model's Civitai page or in another "
                              "repository.")
        raise SourceError(f"{repo} has no single-file checkpoint (a .safetensors of about 2-16 GB).")
    keys = {o["key"] for o in options}
    if pick and pick not in keys:
        raise SourceError(f"{pick} is not a checkpoint in {repo}.")
    if file and not pick and file not in keys:
        raise SourceError(f"{file} is not a single-file checkpoint the Hub can use.")
    choice = pick or file or (options[0]["key"] if len(options) == 1 else None)
    if choice:
        url = f"{hf.HF}/{repo}/resolve/main/{quote(choice)}"
        header = await sniff(c, url, {k: v for k, v in hf._headers().items() if k != "Accept"})
        ok, note = classify(header)
        for o in options:
            if o["key"] == choice:
                o["supported"], o["reason"] = ok, ("" if ok else note)
                o["vpred"] = ok and note == "v-prediction"
    return {"site": "hf", "repo": repo, "url": f"{hf.HF}/{repo}", "title": repo, "nsfw": None, "creator": None,
            "options": options, "pick": choice}


# ---------------------------------------------------------------- Civitai

def _civitai_headers() -> dict:
    h = {"User-Agent": "slq-hub/1", "Accept": "application/json"}
    if os.environ.get("CIVITAI_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['CIVITAI_TOKEN']}"
    return h


async def _civitai_json(c: httpx.AsyncClient, path: str, what: str) -> dict:
    r = await c.get(f"{CIVITAI}{path}", headers=_civitai_headers())
    if r.status_code in (401, 403):
        raise SourceError(CIVITAI_TOKEN_HELP, 403)
    if r.status_code == 404:
        raise SourceError(f"Civitai has no {what} (or does not show it here).", 404)
    if r.status_code >= 400:
        raise SourceError(f"Civitai answered {r.status_code} for {what}.", 502)
    try:
        data = r.json()
    except ValueError:
        raise SourceError("Civitai sent something unexpected.", 502)
    if not isinstance(data, dict):
        raise SourceError("Civitai sent something unexpected.", 502)
    return data


def _civitai_file(version: dict) -> dict | None:
    """The version's checkpoint file: a SafeTensor model file, the primary one first."""
    files = [f for f in version.get("files") or [] if isinstance(f, dict)]
    ok = [f for f in files if f.get("type") in ("Model", "Pruned Model")
          and (f.get("metadata") or {}).get("format") == "SafeTensor"]
    return next((f for f in ok if f.get("primary")), None) or (ok[0] if ok else None)


async def _civitai(c: httpx.AsyncClient, model_id: int | None, version_id: int | None, pick: str | None) -> dict:
    if model_id is None:
        v = await _civitai_json(c, f"/api/v1/model-versions/{version_id}", f"model version {version_id}")
        if not str(v.get("modelId") or "").isdigit():
            raise SourceError("Civitai sent something unexpected.", 502)
        model_id = int(v["modelId"])
    m = await _civitai_json(c, f"/api/v1/models/{model_id}", f"model {model_id}")
    mtype = str(m.get("type") or "")
    options = []
    for v in m.get("modelVersions") or []:
        if not isinstance(v, dict) or not str(v.get("id") or "").isdigit():
            continue
        base = str(v.get("baseModel") or "")
        f = _civitai_file(v)
        size = int(float(f.get("sizeKB") or 0) * 1024) if f else 0
        reason = ""
        if mtype != "Checkpoint":
            reason = f"This is a {mtype or 'resource'}, not a checkpoint; the Hub adds whole models."
        elif base not in SDXL_BASES:
            reason = (f"This version is made for {base or 'another model'}. The Hub runs SDXL checkpoints (SDXL, "
                      "Pony, Illustrious, NoobAI).")
        elif f is None:
            reason = "This version has no .safetensors model file."
        elif not CHECKPOINT_MIN <= size <= CHECKPOINT_MAX:
            reason = "The file is not the size of an SDXL checkpoint."
        url = str((f or {}).get("downloadUrl") or "")
        if f is not None and not url.startswith(f"{CIVITAI}/api/download/models/"):
            url = f"{CIVITAI}/api/download/models/{v['id']}?type=Model&format=SafeTensor"
        options.append({"key": str(v["id"]), "label": str(v.get("name") or v["id"]), "base": base, "size": size,
                        "supported": not reason, "reason": reason, "preset": preset_for(base),
                        "file": {"name": str((f or {}).get("name") or ""), "url": url,
                                 "sha256": str(((f or {}).get("hashes") or {}).get("SHA256") or "").lower()}})
    if not options:
        raise SourceError("That Civitai model has no published versions.", 404)
    want = str(pick or version_id or "")
    if pick and not any(o["key"] == want for o in options):
        raise SourceError(f"Version {pick} is not part of this Civitai model.")
    chosen = next((o for o in options if o["key"] == want), None) or \
        next((o for o in options if o["supported"]), options[0])
    return {"site": "civitai", "model_id": model_id, "url": f"{CIVITAI}/models/{model_id}",
            "title": str(m.get("name") or f"Civitai model {model_id}"), "nsfw": bool(m.get("nsfw")),
            "creator": ((m.get("creator") or {}).get("username") if isinstance(m.get("creator"), dict) else None),
            "options": options, "pick": chosen["key"]}


# ---------------------------------------------------------------- the API

async def lookup(link: str, pick: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    src = parse_link(link)
    site = "Hugging Face" if src["site"] == "hf" else "Civitai"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                                 transport=transport) as c:
        try:
            if src["site"] == "hf":
                res = await _hf(c, src["repo"], src["file"], pick)
            else:
                res = await _civitai(c, src["model_id"], src["version_id"], pick)
        except hf.HFError as exc:
            raise SourceError(str(exc), exc.status)
        except httpx.HTTPError as exc:
            raise SourceError(f"Could not reach {site} ({exc.__class__.__name__}).", 502)
    chosen = next((o for o in res["options"] if o["key"] == res["pick"]), None)
    several = sum(1 for o in res["options"] if o["supported"] is not False) > 1
    name = re.sub(r"[-_]+", " ", res["title"].split("/", 1)[-1] if res["site"] == "hf" else res["title"]).strip()
    if chosen and several:
        # The file tells it from the others - but not by repeating the repository's name, or by its number format
        # ("RealVisXL_V5.0" / "RealVisXL_V5.0_fp16" was "RealVisXL V5.0 RealVisXL V5.0 fp16", 2026-10-30)
        stem = re.sub(r"[-_]+", " ", chosen["label"].rsplit("/", 1)[-1].removesuffix(".safetensors")).strip()
        if stem.lower().startswith(name.lower()):
            stem = stem[len(name):].strip()
        extra = " ".join(w for w in stem.split() if w.lower() not in ("fp16", "fp32", "bf16", "fp8"))
        name = f"{name} {extra}".strip()
    res["suggested"] = {"id": _slug(name), "name": name[:80], "preset": chosen["preset"] if chosen else "sdxl"}
    return res


def clean_settings(raw: dict | None) -> dict:
    """An SDXL model's settings, checked; missing ones come from its preset."""
    raw = raw or {}
    preset = str(raw.get("preset") or "sdxl")
    if preset not in PRESETS:
        raise SourceError("Unknown kind of SDXL model.")
    base = PRESETS[preset]

    def num(key: str, lo: float, hi: float, cast):
        try:
            v = cast(raw.get(key, base[key]))
        except (TypeError, ValueError):
            raise SourceError(f"{key} must be a number.")
        if not lo <= v <= hi:
            raise SourceError(f"{key} must be between {lo:g} and {hi:g}.")
        return v

    sampler = str(raw.get("sampler") or base["sampler"])
    scheduler = str(raw.get("scheduler") or base["scheduler"])
    if sampler not in W.SDXL_SAMPLERS:
        raise SourceError(f"Unknown sampler {sampler}.")
    if scheduler not in W.SDXL_SCHEDULERS:
        raise SourceError(f"Unknown scheduler {scheduler}.")
    return {"preset": preset, "steps": num("steps", 1, 100, int), "cfg": round(num("cfg", 0.0, 30.0, float), 2),
            "sampler": sampler, "scheduler": scheduler, "clip_skip": num("clip_skip", 1, 2, int),
            "prefix": str(raw.get("prefix", base["prefix"]))[:1000].strip(),
            "negative": str(raw.get("negative", base["negative"]))[:2000].strip()}


def make_local_entry(model_id: str, name: str, settings: dict, file_name: str, size: int) -> dict:
    """A catalog entry (family sdxl) for a checkpoint uploaded from the owner's computer (0.62.0) - no source to
    download it from again."""
    if not ID_RE.match(model_id):
        raise SourceError("The name for apps may use a-z, 0-9, dots, dashes and underscores (up to 63).")
    kind = PRESETS[settings["preset"]]["label"]
    return {"id": model_id, "name": (name or model_id).strip()[:80],
            "description": f"{kind} checkpoint uploaded from your computer ({file_name[:120]}).", "default": False,
            "family": "sdxl", "abilities": ["generate"],
            "source": {"site": "upload", "file": file_name[:200], "added": dt.date.today().isoformat()},
            "files": [{"dest": f"comfy/checkpoints/hub/{model_id}.safetensors", "size": int(size)}], "sdxl": settings}


def make_entry(res: dict, model_id: str, name: str, settings: dict) -> dict:
    """A catalog entry (family sdxl) for the chosen file or version."""
    opt = next((o for o in res["options"] if o["key"] == res.get("pick")), None)
    if opt is None:
        raise SourceError("Pick a file or version first.")
    if opt["supported"] is not True:
        raise SourceError(opt["reason"] or "The Hub cannot run this file.")
    if not ID_RE.match(model_id):
        raise SourceError("The name for apps may use a-z, 0-9, dots, dashes and underscores (up to 63).")
    dest = f"comfy/checkpoints/hub/{model_id}.safetensors"
    if res["site"] == "hf":
        file = {"repo": res["repo"], "path": opt["key"], "dest": dest, "size": opt["size"]}
        source = {"site": "huggingface", "url": res["url"], "repo": res["repo"], "file": opt["key"]}
        where = f"Hugging Face: {res['repo']}"
    else:
        file = {"url": opt["file"]["url"], "dest": dest, "size": opt["size"]}
        if opt["file"].get("sha256"):
            file["sha256"] = opt["file"]["sha256"]
        source = {"site": "civitai", "url": f"{res['url']}?modelVersionId={opt['key']}", "model_id": res["model_id"],
                  "version_id": int(opt["key"]), "base": opt["base"], "nsfw": bool(res.get("nsfw"))}
        where = f"Civitai: {res['title']} ({opt['label']})"
    source["added"] = dt.date.today().isoformat()
    kind = PRESETS[settings["preset"]]["label"]
    return {"id": model_id, "name": (name or model_id).strip()[:80],
            "description": f"{kind} checkpoint from {where}.", "default": False, "family": "sdxl",
            "abilities": ["generate"], "source": source, "files": [file], "sdxl": settings}
