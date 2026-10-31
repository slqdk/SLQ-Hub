"""Training LoRAs on the card (0.50.0) - the admin's alone, like the LoRAs themselves.

A run is a folder, data/training/<id>/: run.json (name, trigger word, kind, steps, state), images/ (the pictures,
each with its caption as a .txt beside it - ai-toolkit's layout) and output/ (what ai-toolkit writes: a checkpoint
every SAVE_EVERY steps and sample pictures). The trainer (services/trainer, compose profile "training") sees the
same folder as /training/<id>.

The steps, each started by the admin:
1. pictures in (turned upright, at most 2048 px, as JPEG);
2. captions - written by the chat model when it can see (through the GPU queue, as a chat would), or typed;
3. training: the base model's files are fetched first, by the Hub (the trainer, like the other model containers,
   cannot reach the internet), into Hugging Face's cache layout in models/hf, which the trainer reads offline; then
   the run takes the whole card through the GPU queue ("train") and holds it until ai-toolkit is done, cancelled
   or failed;
4. a checkpoint - chosen by its sample pictures - becomes a LoRA (app/images/loras.py, source "trained").

The base is FLUX.2 klein 4B: a LoRA is trained on its undistilled base (black-forest-labs/FLUX.2-klein-base-4B)
and used on the 4-step model the Hub has (BFL's and Hugging Face's guides). What ai-toolkit reads for it (checked in
its source at the commit the trainer image pins, AI_TOOLKIT_REF): the base's single file
flux-2-klein-base-4b.safetensors (not its diffusers folders), the VAE from ai-toolkit/flux2_vae (ae.safetensors) and
the text encoder from Qwen/Qwen3-4B - about 16 GB, fetched once. In bf16 it fits in 24 GB when the captions'
text embeddings are cached and the text encoder leaves the card (cache_text_embeddings), so the config needs no
quantisation and no 8-bit optimiser - the libraries for those are the least sure on AMD.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import secrets
import shutil
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ..images.loras import has_word

log = logging.getLogger("hub.training")

BASE = {"model": "flux2-klein-4b", "family": "flux2-klein", "label": "FLUX.2 klein 4B",
        "repo": "black-forest-labs/FLUX.2-klein-base-4B", "arch": "flux2_klein_4b", "te_repo": "Qwen/Qwen3-4B"}
# What ai-toolkit loads for the base, per repository: (repo, the files - None for all but read-mes and pictures)
DOWNLOADS = ((BASE["repo"], ("flux-2-klein-base-4b.safetensors",)),
             ("ai-toolkit/flux2_vae", ("ae.safetensors",)),
             (BASE["te_repo"], None))
# SDXL (0.56.0): a LoRA trained on one of the Hub's SDXL checkpoints (RealVisXL 5.0 ...), used with any SDXL model.
# ai-toolkit loads the checkpoint as one file (arch "sdxl": StableDiffusionXLPipeline.from_single_file, read in its
# stable_diffusion_model.py); diffusers takes the layout from SDXL base's config files - small, fetched by the Hub
# like the base models (weights none). The trainer sees the checkpoints read-only.
SDXL_CONFIG_REPO = "stabilityai/stable-diffusion-xl-base-1.0"
SDXL_CONFIG_FILES = re.compile(r"\.(json|txt|model)$", re.I)
CHECKPOINTS = "/models/comfy/checkpoints"
SDXL_NEGATIVE = "bad hands, bad anatomy, ugly, deformed"          # RealVisXL's card, shortened
KINDS = ("person", "thing", "style")
SAVE_EVERY = 250
MIN_IMAGES, MAX_IMAGES = 5, 80
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
MAX_SIDE = 2048
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}\.(jpg|txt)$")
ACTIVE = ("captioning", "queued", "preparing", "training")
POLL_S = 5.0
STOP_POLL_S = 2.0
MAX_MISSES = 12                  # polls without an answer before a training counts as lost (a minute)

CAPTION_PROMPTS = {
    "person": "Describe this photo in one or two plain English sentences for training a picture model: what the "
              "person is doing, their clothes, the setting, the light and how the photo is framed. Do not describe "
              "the face, age or who it is. Call them \"the person\".",
    "thing": "Describe this photo in one or two plain English sentences for training a picture model: where the "
             "object is and how it is placed, what is around it, the light and the camera angle. Call it \"the "
             "object\" and do not name its brand or model.",
    "style": "Describe only what this picture shows, in one or two plain English sentences for training a picture "
             "model: the subject, the scene and the composition. Do not describe the style, the colours or the "
             "technique.",
}
SAMPLES = {
    "person": ["{t}, smiling, a portrait photo outdoors in soft light", "{t} sitting in a café, reading a book",
               "{t} as a watercolour painting"],
    "thing": ["{t} on a wooden workbench, product photo", "{t} in a control cabinet, close-up",
              "{t} on a plain white background"],
    "style": ["{t}, a fox in a forest", "{t}, a city street at night", "{t}, a bowl of fruit on a table"],
}


class TrainingError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:n] or "lora"


def _drop_caches(folder: Path, stem: str) -> None:
    """ai-toolkit's caches of one picture (images/_latent_cache, images/_t_e_cache: <stem>_<22-character hash>
    .safetensors). The latents are keyed by the file's name and crop, not its content."""
    pattern = re.compile(rf"^{re.escape(stem)}_[A-Za-z0-9_-]{{22}}\.safetensors$")
    for cache in folder.glob("_*"):
        if cache.is_dir():
            for f in cache.iterdir():
                if pattern.match(f.name):
                    f.unlink(missing_ok=True)


def _size(path: Path) -> int:
    total = 0
    for p in path.rglob("*") if path.is_dir() else []:
        try:
            total += p.stat().st_size if p.is_file() else 0
        except OSError:
            pass
    return total


def _next_name(name: str) -> str:
    """"Rikke" -> "Rikke v2", "Rikke v2" -> "Rikke v3"."""
    m = re.fullmatch(r"(.*?)\s+v(\d+)", name.strip())
    return f"{m.group(1)} v{int(m.group(2)) + 1}" if m else f"{name.strip()[:76]} v2"


class Training:
    def __init__(self, hub):
        self.hub = hub
        self.root = Path(hub.env.data_dir) / "training"
        self.tasks: dict[str, asyncio.Task] = {}
        self.live: dict[str, dict] = {}                  # progress from the trainer, per run
        self.stop: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------ the trainer service
    def _client(self, timeout: float = 15) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.hub.env.trainer_url, transport=self.hub.transports.trainer,
                                 timeout=timeout)

    async def service(self) -> dict | None:
        """The trainer's /health, or None when it is not there (the compose profile is off)."""
        try:
            async with self._client(5) as c:
                r = await c.get("/health")
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _remote(self, rid: str) -> str:
        return f"{self.hub.env.trainer_root}/{rid}"

    # ------------------------------------------------------------ the runs
    def _dir(self, rid: str) -> Path:
        if not ID_RE.match(rid or ""):
            raise TrainingError("No such training.", 404)
        d = self.root / rid
        if not (d / "run.json").is_file():
            raise TrainingError("No such training.", 404)
        return d

    def _load(self, rid: str) -> dict:
        return json.loads((self._dir(rid) / "run.json").read_text())

    def _save(self, run: dict) -> None:
        d = self.root / run["id"]
        tmp = d / f".run.{secrets.token_hex(4)}.tmp"
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1))
        tmp.replace(d / "run.json")

    def _set(self, rid: str, **fields) -> dict:
        run = self._load(rid)
        run.update(fields)
        self._save(run)
        return run

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for d in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if (d / "run.json").is_file():
                run = json.loads((d / "run.json").read_text())
                out.append({**run, "images": len(list((d / "images").glob("*.jpg"))), "progress": self.live.get(d.name),
                            "size": _size(d), "base_label": self.base_label(run)})
        return out

    # ------------------------------------------------------------ the base a run trains on
    def sdxl_checkpoints(self) -> list[dict]:
        """The SDXL checkpoints the Hub has downloaded (Settings -> Models -> Add image model): id, name and the
        file under models/comfy/checkpoints."""
        out = []
        for e in self.hub.catalog.entries("comfy"):
            if e.get("family") != "sdxl" or not self.hub.catalog.installed(e):
                continue
            dest = next((str(f.get("dest")) for f in e.get("files") or []
                         if str(f.get("dest") or "").startswith("comfy/checkpoints/")), "")
            rel = dest[len("comfy/checkpoints/"):]
            if rel.endswith(".safetensors") and ".." not in Path(rel).parts:
                out.append({"id": e["id"], "name": e.get("name") or e["id"], "path": rel})
        return out

    def bases(self) -> list[dict]:
        return [{"id": BASE["model"], "label": BASE["label"], "family": BASE["family"]}] + [
            {"id": f"sdxl:{c['id']}", "label": f"{c['name']} (SDXL)", "family": "sdxl"} for c in self.sdxl_checkpoints()]

    @staticmethod
    def base_label(run: dict) -> str:
        return f"{run.get('checkpoint_name') or 'SDXL'} (SDXL)" if run.get("base") == "sdxl" else BASE["label"]

    @staticmethod
    def family(run: dict) -> str:
        return "sdxl" if run.get("base") == "sdxl" else BASE["family"]

    def _base_fields(self, base: str | None) -> dict:
        base = base or BASE["model"]
        if base == BASE["model"]:
            return {"base": BASE["model"]}
        if base.startswith("sdxl:"):
            ck = next((c for c in self.sdxl_checkpoints() if c["id"] == base[5:]), None)
            if ck is None:
                raise TrainingError("That SDXL model is not downloaded (Settings -> Models -> Add image model).")
            return {"base": "sdxl", "checkpoint": ck["id"], "checkpoint_name": ck["name"], "checkpoint_path": ck["path"]}
        raise TrainingError("Train on FLUX.2 klein 4B or on one of your SDXL models.")

    def create(self, name: str, trigger: str, kind: str = "thing", steps: int = 1500, rank: int = 16,
               base: str | None = None) -> dict:
        name = " ".join(str(name).split())[:80]
        trigger = str(trigger).strip()[:41]
        if not name:
            raise TrainingError("Give the LoRA a name.")
        if not re.fullmatch(r"[A-Za-z0-9_]{3,40}", trigger):
            raise TrainingError("The trigger word is one made-up word of 3-40 letters or digits, e.g. cx9020x.")
        if kind not in KINDS:
            raise TrainingError("What is it: a person, a thing or a style?")
        steps, rank = self._checked(steps, rank)
        on = self._base_fields(base)
        rid = f"{_slug(name, 30)}-{time.strftime('%Y%m%d-%H%M%S')}"
        if (self.root / rid).exists():
            rid = f"{rid}-{secrets.token_hex(2)}"
        d = self.root / rid
        (d / "images").mkdir(parents=True)
        (d / "output").mkdir()
        run = {"id": rid, "name": name, "trigger": trigger, "kind": kind, "steps": steps, "rank": rank,
               **on, "state": "draft", "error": "", "created": time.time(), "lora": None}
        self._save(run)
        return run

    @staticmethod
    def _checked(steps, rank) -> tuple[int | None, int | None]:
        try:
            steps = None if steps is None else int(steps)
            rank = None if rank is None else int(rank)
        except (TypeError, ValueError):
            raise TrainingError("Steps and rank are whole numbers.")
        if steps is not None and not 250 <= steps <= 6000:
            raise TrainingError("Between 250 and 6000 steps.")
        if rank is not None and rank not in (8, 16, 32, 64):
            raise TrainingError("Rank 8, 16, 32 or 64.")
        return steps, rank

    def update(self, rid: str, name: str | None = None, steps: int | None = None, rank: int | None = None) -> dict:
        """Name, steps and rank of a run that is not working (the trigger word and kind are in its captions)."""
        run = self._editable(rid)
        steps, rank = self._checked(steps, rank)
        fields: dict = {}
        if name is not None:
            fields["name"] = " ".join(str(name).split())[:80]
            if not fields["name"]:
                raise TrainingError("Give the LoRA a name.")
        if steps is not None:
            fields["steps"] = steps
            if run.get("trained") is None and (self._dir(rid) / "output" / rid / f"{rid}.safetensors").is_file():
                fields["trained"] = run["steps"]           # the last checkpoint keeps its step when Steps change
        if rank is not None:
            fields["rank"] = rank
            if rank != run["rank"] and "trained_rank" not in run and self.checkpoints(rid):
                fields["trained_rank"] = run["rank"]      # its checkpoints cannot be continued at another rank
        return self._set(rid, **fields)

    def duplicate(self, rid: str, name: str | None = None, trigger: str | None = None,
                  kind: str | None = None, base: str | None = None) -> dict:
        """A new training with this one's pictures, captions, steps and rank - not its checkpoints or samples, so a
        v2 can be tried while v1's results stay. A new trigger word replaces the old one in the captions. `base`
        (0.60.0) trains the copy on another model - an SDXL person moved to FLUX.2 klein without adding the pictures
        again; without it the copy keeps this one's."""
        src = self._load(rid)
        folder = self._dir(rid) / "images"
        run = self.create(name or _next_name(src["name"]), trigger or src["trigger"], kind or src["kind"],
                          src["steps"], src["rank"],
                          base=base or (f"sdxl:{src.get('checkpoint')}" if src.get("base") == "sdxl" else BASE["model"]))
        old, new = src["trigger"], run["trigger"]
        word = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", re.I)
        dest = self.root / run["id"] / "images"
        for p in sorted(folder.glob("*.jpg")):                     # not ai-toolkit's caches: they are rebuilt
            shutil.copyfile(p, dest / p.name)
            cap = p.with_suffix(".txt")
            if cap.is_file():
                text = cap.read_text(encoding="utf-8")
                (dest / cap.name).write_text(word.sub(new, text) if new != old else text, encoding="utf-8")
        return run

    def captioner(self) -> dict | None:
        """The chat model when it is downloaded and can see pictures - it writes the captions."""
        from ..agent.loop import vision
        entry = self.hub.catalog.get_llm(self.hub.settings.get("models", "default") or "")
        return entry if entry and vision(entry) and self.hub.catalog.installed(entry) else None

    def detail(self, rid: str) -> dict:
        d = self._dir(rid)
        run = self._load(rid)
        images = []
        for p in sorted((d / "images").glob("*.jpg")):
            cap = p.with_suffix(".txt")
            images.append({"name": p.name, "caption": cap.read_text(encoding="utf-8").strip() if cap.is_file() else "",
                           "v": int(p.stat().st_mtime)})
        cps = self.checkpoints(rid)
        return {**run, "images_list": images, "checkpoints": cps, "samples": self.samples(rid),
                "progress": self.live.get(rid), "freeable": self._freeable(rid), "base_label": self.base_label(run),
                "resume_step": self.resume_step(run, cps)}

    def resume_step(self, run: dict, cps: list[dict] | None = None) -> int | None:
        """The step Continue goes on from: the newest checkpoint, while it is short of the run's steps and made at its
        rank (a LoRA of another rank cannot be loaded). ai-toolkit itself finds that checkpoint in the output folder,
        with optimizer.pt when a stopped run left it."""
        if run["state"] in ACTIVE or run.get("trained_rank", run["rank"]) != run["rank"]:
            return None
        cps = self.checkpoints(run["id"]) if cps is None else cps
        step = cps[-1]["step"] if cps else None
        return step if step is not None and 0 < step < run["steps"] else None

    def _cache_dirs(self, rid: str) -> list[Path]:
        return [p for p in (self._dir(rid) / "images").glob("_*") if p.is_dir()]

    def _freeable(self, rid: str) -> int:
        """The bytes Free space gives back: the checkpoints, ai-toolkit's optimizer state and its caches."""
        out = self._dir(rid) / "output" / rid
        files = [*out.glob("*.safetensors"), out / "optimizer.pt"] if out.is_dir() else []
        return sum(p.stat().st_size for p in files if p.is_file()) + sum(_size(c) for c in self._cache_dirs(rid))

    def free_space(self, rid: str) -> int:
        """Free space (0.53.0): a training's checkpoints, optimizer state and caches go - its pictures, captions and
        sample pictures stay, and so do the LoRAs added from it (copies in the LoRA folder). Three trainings held
        10.5 GB on the VM (2026-10-30), all of it in every backup until then. Returns the bytes given back."""
        run = self._load(rid)
        if run["state"] in ACTIVE:
            raise TrainingError("Not while it is working.", 409)
        freed = self._freeable(rid)
        out = self._dir(rid) / "output" / rid
        for p in [*out.glob("*.safetensors"), out / "optimizer.pt"] if out.is_dir() else []:
            p.unlink(missing_ok=True)
        for c in self._cache_dirs(rid):
            shutil.rmtree(c, ignore_errors=True)
        return freed

    def delete(self, rid: str) -> None:
        d = self._dir(rid)
        if self._load(rid)["state"] in ACTIVE:
            raise TrainingError("Stop it first.", 409)
        shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------ pictures and captions
    def _editable(self, rid: str) -> dict:
        run = self._load(rid)
        if run["state"] in ACTIVE:
            raise TrainingError("Not while it is working.", 409)
        return run

    def add_images(self, rid: str, files: list[tuple[str, bytes]]) -> list[str]:
        self._editable(rid)
        folder = self._dir(rid) / "images"
        have = len(list(folder.glob("*.jpg")))
        if have + len(files) > MAX_IMAGES:
            raise TrainingError(f"At most {MAX_IMAGES} pictures.")
        if any(Path(n).suffix.lower() in (".heic", ".heif") for n, _ in files):
            try:
                import pillow_heif                     # iPhone photos, when the library is there
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
        saved = []
        for filename, data in files:
            if Path(filename).suffix.lower() not in IMAGE_EXT:
                raise TrainingError(f"{filename} is not a picture.")
            try:
                with Image.open(io.BytesIO(data)) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
                    if min(img.size) < 256:
                        raise TrainingError(f"{filename} is too small ({img.size[0]}x{img.size[1]}); 512 px at "
                                            "least, 1024 or more is better.")
                    stem, n = _slug(Path(filename).stem, 50), 2
                    name = f"{stem}.jpg"
                    while (folder / name).exists():
                        name, n = f"{stem}-{n}.jpg", n + 1
                    _drop_caches(folder, Path(name).stem)    # left by a picture of that name before
                    img.save(folder / name, "JPEG", quality=95)
            except TrainingError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise TrainingError(f"{filename} could not be read as a picture: {exc}")
            saved.append(name)
        self._set(rid, state="draft", error="")
        return saved

    def remove_image(self, rid: str, name: str) -> None:
        self._editable(rid)
        if not NAME_RE.match(name or "") or not name.endswith(".jpg"):
            raise TrainingError("No such picture.", 404)
        folder = self._dir(rid) / "images"
        (folder / name).unlink(missing_ok=True)
        (folder / name).with_suffix(".txt").unlink(missing_ok=True)
        _drop_caches(folder, Path(name).stem)

    def set_captions(self, rid: str, captions: dict) -> None:
        run = self._editable(rid)
        folder = self._dir(rid) / "images"
        for name, text in (captions or {}).items():
            if not NAME_RE.match(str(name)) or not str(name).endswith(".jpg") or not (folder / name).is_file():
                continue
            text = " ".join(str(text).split())[:600]
            if text and not has_word(text, run["trigger"]):
                text = f"{run['trigger']}, {text}"            # the trigger word is what the LoRA learns to answer
            (folder / name).with_suffix(".txt").write_text(text, encoding="utf-8")

    async def write_captions(self, rid: str, overwrite: bool = False) -> None:
        """Captions by the chat model, for the pictures without one (or all), in the background."""
        run = self._editable(rid)
        if rid in self.tasks and not self.tasks[rid].done():
            raise TrainingError("It is already working.", 409)
        entry = self.captioner()
        if entry is None:
            raise TrainingError("The chat model cannot see pictures - write the captions yourself.")
        if not list((self._dir(rid) / "images").glob("*.jpg")):
            raise TrainingError("Add the pictures first.")
        self._set(rid, state="captioning", error="")
        self.tasks[rid] = asyncio.create_task(self._captions(run, entry, overwrite))

    async def _captions(self, run: dict, entry: dict, overwrite: bool) -> None:
        from ..llm import payload as P
        from ..llm.messages import image_part
        from ..llm.stream import Accumulator
        rid = run["id"]
        folder = self.root / rid / "images"
        todo = [p for p in sorted(folder.glob("*.jpg")) if overwrite or not p.with_suffix(".txt").is_file()]
        ticket = None
        empty = 0
        try:
            ticket = await self.hub.scheduler.acquire("training", "caption", self.hub.model_need(entry["id"]),
                                                      "interactive", label=f"Captions for the LoRA {run['name']}",
                                                      meta={"who": "owner"})
            for i, p in enumerate(todo):
                self.live[rid] = {"what": "captions", "done": i, "total": len(todo)}
                msg = [{"role": "user", "content": [{"type": "text", "text": CAPTION_PROMPTS[run["kind"]]},
                                                    image_part(p.read_bytes())]}]
                pl = P.build(entry, msg, thinking=False, sampling={"max_tokens": 160, "temperature": 0.2})
                acc = Accumulator()
                async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
                    acc.feed(chunk)
                text = " ".join((acc.result().get("content") or "").split()).strip().strip('"')
                if text:
                    p.with_suffix(".txt").write_text(f"{run['trigger']}, {text}"[:600], encoding="utf-8")
                else:
                    empty += 1
            self._set(rid, state="draft", error=f"The chat model gave no caption for {empty} picture(s) - write "
                                                "those yourself." if empty else "")
        except Exception as exc:  # noqa: BLE001
            log.warning("captions for %s failed: %s", rid, exc)
            self._set(rid, state="draft", error=f"The captions stopped: {exc}")
        finally:
            self.live.pop(rid, None)
            if ticket is not None:
                await ticket.release()

    # ------------------------------------------------------------ training
    def config(self, run: dict) -> dict:
        """ai-toolkit's config (its JSON form) for a run - the keys of its FLUX.2 klein examples."""
        if run.get("base") == "sdxl":
            return self._sdxl_config(run)
        rid, t = run["id"], run["trigger"]
        remote = self._remote(rid)
        lr = 8e-5 if run["kind"] == "style" else 1e-4               # BFL: lower for style, higher for characters
        return {"job": "extension", "config": {"name": rid, "process": [{
            "type": "diffusion_trainer", "training_folder": f"{remote}/output", "device": "cuda",
            "trigger_word": t,
            "network": {"type": "lora", "linear": run["rank"], "linear_alpha": run["rank"]},
            "save": {"dtype": "bf16", "save_every": SAVE_EVERY, "max_step_saves_to_keep": 40},
            "datasets": [{"folder_path": f"{remote}/images", "caption_ext": "txt", "cache_latents_to_disk": True,
                          "resolution": [512, 768, 1024]}],
            # cache_text_embeddings: each caption (and sample prompt) is encoded once, then the 8 GB text encoder
            # leaves the card - what lets bf16 fit in 24 GB without quantising
            "train": {"batch_size": 1, "steps": run["steps"], "gradient_accumulation": 1, "train_unet": True,
                      "train_text_encoder": False, "gradient_checkpointing": True, "noise_scheduler": "flowmatch",
                      "optimizer": "adamw", "lr": lr, "timestep_type": "weighted", "dtype": "bf16",
                      "cache_text_embeddings": True,
                      "content_or_style": {"person": "content", "thing": "content"}.get(run["kind"], "style")},
            "model": {"name_or_path": BASE["repo"], "arch": BASE["arch"], "quantize": False, "quantize_te": False,
                      "low_vram": False},
            "sample": {"sampler": "flowmatch", "sample_every": SAVE_EVERY, "width": 1024, "height": 1024,
                       "prompts": [p.format(t=t) for p in SAMPLES[run["kind"]]], "neg": "", "seed": 42,
                       "walk_seed": True, "guidance_scale": 4, "sample_steps": 25}}]},
            "meta": {"name": run["name"], "version": "1.0"}}

    def _sdxl_config(self, run: dict) -> dict:
        """SDXL with ai-toolkit's own defaults for it (extensions_built_in/diffusion_models/ui.tsx: no quantising,
        ddpm noise scheduler, guidance 6; no timestep type) - the text encoders are small and stay. The samples use
        DPM++ 2M Karras (0.59.4), as the Hub's own SDXL pictures do. Some samples still show speckles of noise that
        the LoRA does not have in a chat (0.59.5) - their cause in ai-toolkit's sampling is open."""
        rid, t = run["id"], run["trigger"]
        remote = self._remote(rid)
        return {"job": "extension", "config": {"name": rid, "process": [{
            "type": "diffusion_trainer", "training_folder": f"{remote}/output", "device": "cuda",
            "trigger_word": t,
            "network": {"type": "lora", "linear": run["rank"], "linear_alpha": run["rank"]},
            "save": {"dtype": "fp16", "save_every": SAVE_EVERY, "max_step_saves_to_keep": 40},
            "datasets": [{"folder_path": f"{remote}/images", "caption_ext": "txt", "cache_latents_to_disk": True,
                          "resolution": [768, 1024]}],
            "train": {"batch_size": 1, "steps": run["steps"], "gradient_accumulation": 1, "train_unet": True,
                      "train_text_encoder": False, "gradient_checkpointing": True, "noise_scheduler": "ddpm",
                      "optimizer": "adamw", "lr": 8e-5 if run["kind"] == "style" else 1e-4, "dtype": "bf16"},
            "model": {"name_or_path": f"{CHECKPOINTS}/{run['checkpoint_path']}", "arch": "sdxl", "quantize": False,
                      "quantize_te": False},     # no vae_dtype: ai-toolkit runs the VAE in the train dtype (0.59.3)
            "sample": {"sampler": "k_dpmsolver++", "sample_every": SAVE_EVERY, "width": 1024, "height": 1024,
                       "prompts": [p.format(t=t) for p in SAMPLES[run["kind"]]], "neg": SDXL_NEGATIVE, "seed": 42,
                       "walk_seed": True, "guidance_scale": 6, "sample_steps": 30}}]},
            "meta": {"name": run["name"], "version": "1.0"}}

    def downloads(self, run: dict) -> list[tuple[str, tuple[str, ...] | None, re.Pattern | None]]:
        if run.get("base") == "sdxl":
            return [(SDXL_CONFIG_REPO, None, SDXL_CONFIG_FILES)]
        return [(repo, files, None) for repo, files in DOWNLOADS]

    async def start(self, rid: str, resume: bool = False) -> dict:
        """Train from the start (the output folder is emptied), or with resume go on from the newest checkpoint - a
        stopped run, or a finished one given more steps."""
        run = self._editable(rid)
        step = self.resume_step(run) if resume else None
        if resume and step is None:
            raise TrainingError("There is no checkpoint to go on from (or Steps are not above it) - start over instead.")
        if run.get("base") == "sdxl" and run.get("checkpoint") not in {c["id"] for c in self.sdxl_checkpoints()}:
            raise TrainingError(f"{run.get('checkpoint_name') or 'Its SDXL model'} is no longer downloaded.")
        folder = self._dir(rid) / "images"
        pics = list(folder.glob("*.jpg"))
        if len(pics) < MIN_IMAGES:
            raise TrainingError(f"At least {MIN_IMAGES} pictures (15-40 is best).")
        missing = [p.name for p in pics if not p.with_suffix(".txt").is_file()]
        if missing:
            raise TrainingError(f"{len(missing)} picture(s) have no caption yet.")
        health = await self.service()
        if health is None:
            raise TrainingError("The trainer is not running: add COMPOSE_PROFILES=training to /srv/slq-hub/.env "
                                "and run update.sh.", 503)
        if not health.get("toolkit"):
            raise TrainingError("The trainer has no ai-toolkit - rebuild it (update.sh).", 503)
        if step is not None:
            out = self._dir(rid) / "output" / rid
            final, numbered = out / f"{rid}.safetensors", out / f"{rid}_{step:09d}.safetensors"
            if final.is_file() and not numbered.exists():
                final.rename(numbered)      # the last round's end stays a checkpoint; renamed, it is ai-toolkit's newest
            fields: dict = {"trained": None}
        else:
            for old in (self._dir(rid) / "output").glob("*"):       # a run starts over
                shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
            fields = {"lora": None, "trained": None, "trained_rank": run["rank"]}
        run = self._set(rid, state="queued", error="", started=time.time(), **fields)
        self.stop[rid] = asyncio.Event()
        self.tasks[rid] = asyncio.create_task(self._train(run))
        return run

    async def cancel(self, rid: str) -> None:
        run = self._load(rid)
        if run["state"] not in ACTIVE:
            return
        ev = self.stop.get(rid)
        if ev is not None:
            ev.set()
        task = self.tasks.get(rid)
        if run["state"] in ("captioning", "queued", "preparing") and task is not None and not task.done():
            task.cancel()

    async def _train(self, run: dict) -> None:
        rid = run["id"]
        ticket = None
        try:
            # 1. the base model's files, before the card is taken (they are big, and need no GPU)
            self._set(rid, state="preparing")
            for repo, files, pattern in self.downloads(run):
                await self.fetch(repo, rid, files, pattern)
            # 2. the whole card, through the queue
            self._set(rid, state="queued")
            self.live[rid] = {"what": "queue"}
            stop = self.stop.setdefault(rid, asyncio.Event())
            ticket = await self.hub.scheduler.acquire("training", "train", "train", "background",
                                                      label=f"Training the LoRA {run['name']}",
                                                      meta={"cancel": stop.set, "who": "owner"})
            self._set(rid, state="training")
            async with self._client() as c:
                r = await c.post("/jobs", json={"id": rid, "config": self.config(run), "dir": self._remote(rid)})
                if r.status_code >= 400:
                    raise TrainingError(r.json().get("error") or f"the trainer said {r.status_code}")
                asked, misses = False, 0
                while True:
                    if stop.is_set() and not asked:        # once: the trainer stops it, then says "cancelled"
                        try:
                            await c.post(f"/jobs/{rid}/cancel")
                            asked = True
                        except httpx.HTTPError:
                            pass
                    try:
                        r = await c.get(f"/jobs/{rid}")
                        misses = 0
                    except httpx.HTTPError:                # a blip is waited out; a minute of silence is not
                        misses += 1
                        if misses >= MAX_MISSES:
                            raise TrainingError("The trainer stopped answering.")
                        await asyncio.sleep(POLL_S)
                        continue
                    if r.status_code == 404:
                        raise TrainingError("The trainer no longer knows the job (did it restart?) - start it again.")
                    st = r.json()
                    self.live[rid] = {"what": "stopping" if asked else "training", "step": st.get("step", 0),
                                      "total": st.get("total") or run["steps"], "loss": st.get("loss"),
                                      "eta_s": st.get("eta_s"), "log": (st.get("log") or [])[-12:]}
                    if st.get("state") in ("done", "failed", "cancelled"):
                        break
                    await asyncio.sleep(POLL_S)
            state = {"done": "done", "cancelled": "cancelled"}.get(st["state"], "failed")
            self._set(rid, state=state, error=(st.get("error") or "the trainer stopped") if state == "failed" else "",
                      ended=time.time(), log=(st.get("log") or [])[-30:],
                      **({"trained": run["steps"]} if state == "done" else {}))
        except asyncio.CancelledError:
            self._set(rid, state="cancelled", error="", ended=time.time())
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("training %s failed: %s", rid, exc)
            self._set(rid, state="failed", error=str(exc)[:500], ended=time.time())
        finally:
            self.live.pop(rid, None)
            # ai-toolkit's optimizer state lets Continue go on exactly where a stopped run was. A finished run's is
            # hundreds of MB that training further can do without (AdamW starts its averages again).
            try:
                done = self._load(rid).get("state") not in ("cancelled", "failed")
            except (OSError, ValueError):
                done = True
            if done:
                (self.root / rid / "output" / rid / "optimizer.pt").unlink(missing_ok=True)
            if ticket is not None:
                await ticket.release()

    # ------------------------------------------------------------ base models, into Hugging Face's cache
    def cache_dir(self, repo: str) -> Path:
        return Path(self.hub.env.models_dir) / "hf" / "hub" / f"models--{repo.replace('/', '--')}"

    async def fetch(self, repo: str, rid: str | None = None, only: tuple[str, ...] | None = None,
                    pattern: re.Pattern | None = None) -> None:
        """A Hugging Face repository (or `only` these files of it) into models/hf in the cache's own layout
        (refs/main and snapshots/<commit>), which the trainer's libraries read with HF_HUB_OFFLINE. Files there at
        their size are kept; a partial download goes on where it stopped. HF_TOKEN (the VM's .env) for a gated
        repository."""
        from ..hf import HF, _headers, _tree
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30), follow_redirects=True,
                                     transport=self.hub.transports.web) as c:
            info = await c.get(f"{HF}/api/models/{repo}", headers=_headers())
            if info.status_code in (401, 403):
                raise TrainingError(f"Hugging Face wants a signed-in user for {repo}: put HF_TOKEN=... in "
                                    "/srv/slq-hub/.env and run update.sh.", 403)
            if info.status_code >= 400:
                raise TrainingError(f"Hugging Face answered {info.status_code} for {repo}.", 502)
            sha = re.sub(r"[^0-9a-f]", "", str((info.json() or {}).get("sha") or ""))[:40] or "hub"
            files = [x for x in await _tree(c, repo) if x.get("type") == "file" and not re.search(
                r"\.(gguf|onnx|png|jpe?g|gif|webp|mp4|md)$|^\.", x.get("path", ""), re.I)]
            if pattern is not None:                         # SDXL base: its config files, not its weights
                files = [x for x in files if pattern.search(str(x.get("path") or ""))]
            if only is not None:
                files = [x for x in files if x.get("path") in only]
                missing = set(only) - {x.get("path") for x in files}
                if missing:
                    raise TrainingError(f"Hugging Face has no {', '.join(sorted(missing))} in {repo} - the trainer "
                                        "expects it there (has ai-toolkit changed?).", 502)
            base = self.cache_dir(repo)
            snap = base / "snapshots" / sha
            total = sum(int(x.get("size") or 0) for x in files)
            done = 0
            for f in files:
                path = str(f["path"])
                if ".." in Path(path).parts or path.startswith("/"):
                    continue
                dest = snap / path
                size = int(f.get("size") or 0)
                if dest.is_file() and (not size or dest.stat().st_size == size):
                    done += size
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(dest.name + ".part")
                have = part.stat().st_size if part.exists() else 0
                h = {**_headers(), **({"Range": f"bytes={have}-"} if have else {})}
                async with c.stream("GET", f"{HF}/{repo}/resolve/main/{path}", headers=h) as r:
                    if r.status_code >= 400 and r.status_code != 416:
                        raise TrainingError(f"Hugging Face answered {r.status_code} for {repo}/{path}.", 502)
                    if r.status_code != 206:
                        have = 0
                    with open(part, "ab" if have else "wb") as out:
                        async for chunk in r.aiter_bytes(1024 * 1024):
                            out.write(chunk)
                            if rid:
                                self.live[rid] = {"what": "download", "repo": repo, "done": done + out.tell(),
                                                  "total": total}
                part.replace(dest)
                done += size or dest.stat().st_size
            (base / "refs").mkdir(parents=True, exist_ok=True)
            (base / "refs" / "main").write_text(sha)

    async def resume_after_restart(self) -> None:
        """At start-up: a run the Hub was following when it stopped is ended. If ai-toolkit is still at work, it is
        stopped, and the card is held through the queue - ahead of anything else - until it has: in the new
        scheduler's eyes the card is free, so a model would load into memory the training still holds."""
        for run in self.list():
            if run["state"] in ACTIVE:
                self._set(run["id"], state="failed", error="The Hub restarted while it was working - start it again.")
        health = await self.service()
        if health and health.get("busy"):
            self.tasks["-restart"] = asyncio.create_task(self._stop_leftover(str(health.get("job") or "")))

    async def _stop_leftover(self, job: str) -> None:
        # Told to stop first: taking the card waits for its memory to be free (GpuBackend), which only happens as
        # ai-toolkit exits.
        try:
            async with self._client(10) as c:
                if job:
                    await c.post(f"/jobs/{job}/cancel")
        except httpx.HTTPError as exc:
            log.warning("could not stop the training left from before the restart: %s", exc)
        ticket = await self.hub.scheduler.acquire("training", "train", "train", "interactive",
                                                  label="Stopping a training from before the restart",
                                                  meta={"who": "owner"})
        try:
            async with self._client(10) as c:
                for _ in range(100):                     # the trainer gives ai-toolkit up to 85 s to stop
                    if not (await c.get("/health")).json().get("busy"):
                        break
                    await asyncio.sleep(STOP_POLL_S)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not see the training left from before the restart stop: %s", exc)
        finally:
            await ticket.release()

    # ------------------------------------------------------------ results
    def checkpoints(self, rid: str) -> list[dict]:
        """The saved LoRAs by step (ai-toolkit: <name>_000000250.safetensors; the last one <name>.safetensors)."""
        out_dir = self._dir(rid) / "output" / rid
        if not out_dir.is_dir():
            return []
        run = self._load(rid)
        found = []
        for p in out_dir.glob("*.safetensors"):
            m = re.search(r"_(\d{6,})\.safetensors$", p.name)
            step = int(m.group(1)) if m else ((run.get("trained") or run["steps"]) if p.stem == rid else None)
            if step is not None:
                found.append({"step": step, "file": p.name, "size": p.stat().st_size})
        return sorted(found, key=lambda x: x["step"])

    def samples(self, rid: str) -> list[dict]:
        """The sample pictures by step (ai-toolkit: samples/<time>__<step>_<n>.jpg)."""
        folder = self._dir(rid) / "output" / rid / "samples"
        if not folder.is_dir():
            return []
        found = []
        for p in sorted(folder.iterdir()):
            m = re.search(r"_(\d{6,})_(\d+)\.(jpg|jpeg|png|webp)$", p.name)
            if m:
                found.append({"step": int(m.group(1)), "n": int(m.group(2)), "path": f"output/{rid}/samples/{p.name}"})
        return sorted(found, key=lambda x: (x["step"], x["n"]))

    def file(self, rid: str, rel: str) -> Path:
        d = self._dir(rid).resolve()
        p = (d / rel).resolve()
        if d not in p.parents or not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            raise TrainingError("No such file.", 404)
        return p

    def use(self, rid: str, step: int | None = None) -> dict:
        """A checkpoint into the LoRAs (the last one unless a step is chosen)."""
        run = self._load(rid)
        cps = self.checkpoints(rid)
        if not cps:
            raise TrainingError("There is no checkpoint yet.", 409)
        cp = next((x for x in cps if x["step"] == step), None) if step is not None else cps[-1]
        if cp is None:
            raise TrainingError("No checkpoint at that step.", 404)
        store = self.hub.extras["loras"]
        src = self._dir(rid) / "output" / rid / cp["file"]
        label = run["name"] if cp == cps[-1] else f"{run['name']} (step {cp['step']})"
        entry = store.add_file(src, src.name, name=label, base=self.family(run), trigger=run["trigger"], move=False,
                               note=f"Trained here on {self.base_label(run)} from "
                                    f"{len(list((self._dir(rid) / 'images').glob('*.jpg')))} pictures ({run['kind']}, "
                                    f"step {cp['step']} of {run['steps']}).",
                               source={"kind": "trained", "run": rid, "step": cp["step"]})
        self._set(rid, lora=entry["id"])
        return entry
