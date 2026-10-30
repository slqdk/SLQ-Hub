"""LoRAs for the picture models (0.49.0): small add-ons that teach a picture model a person, a thing or a style.

The admin's alone (Søren: "LoRa is Admin only! also for using"): added in Settings -> Models, used in the admin's own
chats. People (members), their devices and apps on the model API never see or use one.

A LoRA belongs to one model family - `base`, the family of the picture models it fits ("flux2-klein", "qwen-image",
"z-image", "chroma", "ernie-image", "sdxl"). Its file is models/comfy/loras/hub/<id>.safetensors, which ComfyUI's
loaders list as hub/<id>.safetensors; the list is data/loras.json. A file that arrives is read for its header: it
must be a .safetensors LoRA, and its family is guessed from its tensor names and metadata (the admin can change it).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import shutil
import struct
import threading
from pathlib import Path

FAMILIES = {"flux2-klein": "FLUX.2 klein", "qwen-image": "Qwen-Image", "z-image": "Z-Image", "chroma": "Chroma",
            "ernie-image": "ERNIE-Image", "sdxl": "SDXL"}
MAX_BYTES = 3 * 1024**3           # LoRAs are 10-300 MB as a rule; a rank-128 Qwen-Image one is about 1.2 GB
HEADER_MAX = 64 * 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
NAME_MAX, TRIGGER_MAX, NOTE_MAX = 80, 120, 500
# Strength: a LoRA as trained is 1 (0-2). A slider LoRA (0.55.0) moves one quality along a line - plus for more,
# minus for the opposite - and is made weak per step, so its makers give strengths like 4 or -3.
RANGE, SLIDER_RANGE = (0.0, 2.0), (-10.0, 10.0)


def strength_range(slider: bool) -> tuple[float, float]:
    return SLIDER_RANGE if slider else RANGE


class LoraError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def read_header(path: Path) -> dict:
    """The JSON header of a .safetensors file: tensor names, shapes and __metadata__."""
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise LoraError("That is not a .safetensors file.")
        (n,) = struct.unpack("<Q", head)
        if not 2 <= n <= HEADER_MAX:
            raise LoraError("That is not a .safetensors file.")
        raw = f.read(n)
    try:
        header = json.loads(raw)
    except ValueError:
        raise LoraError("That is not a .safetensors file.")
    if not isinstance(header, dict):
        raise LoraError("That is not a .safetensors file.")
    return header


def is_lora(header: dict) -> bool:
    keys = [k for k in header if k != "__metadata__"]
    return any(("lora" in k.lower() or k.endswith(".alpha")) for k in keys[:5000]) and len(keys) > 0


def guess_base(header: dict) -> str:
    """The family a LoRA was trained for, from its metadata and tensor names - "" when it cannot be told."""
    meta = header.get("__metadata__") or {}
    said = " ".join(str(v) for k, v in meta.items()
                    if k in ("ss_base_model_version", "modelspec.architecture", "ss_sd_model_name", "base_model",
                             "base_model_name", "model_type", "architecture", "name")).lower()
    keys = "\n".join(k for k in list(header)[:6000] if k != "__metadata__")
    if "qwen" in said or "img_mlp" in keys or "txt_mlp" in keys:
        return "qwen-image"
    if ("sdxl" in said or "stable-diffusion-xl" in said or "lora_te1_" in keys or "lora_te2_" in keys
            or "lora_unet_input_blocks" in keys or "lora_unet_down_blocks" in keys):
        return "sdxl"
    if any(w in said for w in ("z_image", "z-image", "zimage", "lumina")) or "noise_refiner" in keys \
            or "context_refiner" in keys:
        return "z-image"
    if "chroma" in said or "distilled_guidance_layer" in keys:
        return "chroma"
    if "ernie" in said:
        return "ernie-image"
    if any(w in said for w in ("klein", "flux2", "flux.2", "flux_2")) or "to_qkv_mlp_proj" in keys \
            or "double_blocks" in keys or "single_blocks" in keys or "single_transformer_blocks" in keys:
        # FLUX-shaped: klein 4B only when its blocks and sizes say so - a FLUX.1 LoRA (deeper) is left for the
        # admin to say (Chroma, perhaps); klein 9B is refused when it is added (flux_size)
        return "" if flux_size(header) == "flux1" else "flux2-klein"
    return ""


def flux_size(header: dict) -> str:
    """Which FLUX a FLUX-shaped LoRA was trained on, from its block numbers and layer widths - "klein-9b", "flux1",
    or "" (klein 4B, or it cannot be told). The sizes are ai-toolkit's (flux2/src/model.py): klein 4B has hidden
    size 3072, 5 double and 20 single blocks; klein 9B 4096, 8 and 24; FLUX.1 3072, 19 and 38. A LoRA works only
    with the size it was trained on."""
    double = single = -1
    widths: set[int] = set()
    for k, v in header.items():
        if k == "__metadata__":
            continue
        m = re.search(r"single(?:_transformer)?_blocks[._](\d+)[._]", k)
        if m:
            single = max(single, int(m.group(1)))
        else:
            m = re.search(r"(?:double_blocks|transformer_blocks)[._](\d+)[._]", k)
            if m:
                double = max(double, int(m.group(1)))
        shape = v.get("shape") if isinstance(v, dict) else None
        if m and re.search(r"lora_(?:A|down)", k) and isinstance(shape, list) and len(shape) == 2:
            widths.add(int(shape[1]))
    if double >= 8 or single >= 24:
        return "flux1"
    if 4096 in widths or double >= 5 or single >= 20:
        return "klein-9b"
    return ""


def guess_trigger(header: dict) -> str:
    """A trigger word the trainer wrote into the file, if any (ai-toolkit and others: trigger_word)."""
    meta = header.get("__metadata__") or {}
    for key in ("trigger_word", "ss_trigger_word", "trigger", "instance_prompt"):
        v = str(meta.get(key) or "").strip()
        if v:
            return v[:TRIGGER_MAX]
    return ""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (s or "lora")[:48]


class Loras:
    def __init__(self, data_dir: Path, models_dir: Path):
        self.list_path = Path(data_dir) / "loras.json"
        self.dir = Path(models_dir) / "comfy" / "loras" / "hub"
        self._lock = threading.Lock()

    # ------------------------------------------------------------ the list
    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.list_path.read_text())
        except (OSError, ValueError):
            return []
        return [x for x in data if isinstance(x, dict) and ID_RE.match(str(x.get("id", "")))] \
            if isinstance(data, list) else []

    def _save(self, items: list[dict]) -> None:
        self.list_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.list_path.with_name(f".loras.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1))
        tmp.replace(self.list_path)

    def list(self) -> list[dict]:
        """Every LoRA whose file is there, with its family's label."""
        out = []
        for x in self._load():
            if self.path(x["id"]).is_file():
                out.append({**x, "base_label": FAMILIES.get(x.get("base", ""), "Unknown model")})
        return out

    def get(self, lid: str) -> dict | None:
        return next((x for x in self.list() if x["id"] == lid), None)

    def path(self, lid: str) -> Path:
        return self.dir / f"{lid}.safetensors"

    @staticmethod
    def comfy_name(lid: str) -> str:
        return f"hub/{lid}.safetensors"

    def fitting(self, fam: str) -> list[dict]:
        return [x for x in self.list() if x.get("base") == fam]

    # ------------------------------------------------------------ changes
    @staticmethod
    def _clean(name: str | None = None, base: str | None = None, trigger: str | None = None,
               strength: float | None = None, note: str | None = None, slider: bool | None = None,
               is_slider: bool = False) -> dict:
        out: dict = {}
        if slider is not None:
            out["slider"] = bool(slider)
            is_slider = bool(slider)
        if name is not None:
            n = " ".join(str(name).split())[:NAME_MAX]
            if not n:
                raise LoraError("Give the LoRA a name.")
            out["name"] = n
        if base is not None:
            if base and base not in FAMILIES:
                raise LoraError(f"Unknown picture model family {base!r}.")
            out["base"] = base
        if trigger is not None:
            out["trigger"] = " ".join(str(trigger).split())[:TRIGGER_MAX]
        if strength is not None:
            try:
                s = float(strength)
            except (TypeError, ValueError):
                raise LoraError("The strength must be a number.")
            lo, hi = strength_range(is_slider)
            if not lo <= s <= hi:
                raise LoraError("A slider LoRA's strength is between -10 and 10." if is_slider
                                else "The strength is between 0 and 2 (1 is as it was trained) - or make it a slider "
                                     "LoRA in its settings.")
            out["strength"] = round(s, 2)
        if note is not None:
            out["note"] = str(note).strip()[:NOTE_MAX]
        return out

    def add_file(self, src: Path, filename: str = "", name: str = "", base: str = "", trigger: str = "",
                 strength: float = 1.0, note: str = "", source: dict | None = None, move: bool = True,
                 slider: bool | None = None) -> dict:
        """A .safetensors LoRA into the list (moved, or copied with move=False). Its family is guessed when not
        given; so is its trigger word."""
        src = Path(src)
        size = src.stat().st_size
        if size > MAX_BYTES:
            raise LoraError(f"The file is larger than {MAX_BYTES // 1024**3} GB - that is not a LoRA.")
        header = read_header(src)
        if not is_lora(header):
            raise LoraError("This .safetensors file is not a LoRA (a whole model is added under Picture models).")
        if flux_size(header) == "klein-9b":
            raise LoraError("This LoRA is for FLUX.2 klein 9B. The Hub's klein is the 4B, and a LoRA only works with "
                            "the size it was trained on - look for one made for klein 4B.")
        if slider is None:                    # "natural-beauty-slider-zit": its maker says what it is
            said = f"{filename} {src.name} {name} {json.dumps(header.get('__metadata__') or {})[:4000]}".lower()
            slider = "slider" in said
        fields = self._clean(name=name or Path(filename or src.name).stem, base=base or guess_base(header),
                             trigger=trigger or guess_trigger(header), strength=strength, note=note, slider=slider)
        with self._lock:
            items = self._load()
            taken = {x["id"] for x in items}
            lid, n = _slug(fields["name"]), 2
            while lid in taken or self.path(lid).exists():
                lid, n = f"{_slug(fields['name'])}-{n}", n + 1
            self.dir.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(src), self.path(lid))
            else:
                shutil.copyfile(src, self.path(lid))
            entry = {"id": lid, **fields, "size": size, "added": dt.date.today().isoformat(),
                     "source": source or {"kind": "upload", "file": Path(filename or src.name).name[:200]}}
            items.append(entry)
            self._save(items)
        return self.get(lid) or entry

    def update(self, lid: str, **changes) -> dict:
        with self._lock:
            items = self._load()
            entry = next((x for x in items if x["id"] == lid), None)
            if entry is None:
                raise LoraError("No such LoRA.", 404)
            fields = self._clean(**{k: v for k, v in changes.items() if k in ("name", "base", "trigger", "strength",
                                                                             "note", "slider") and v is not None},
                                 is_slider=bool(entry.get("slider")))
            if "strength" not in fields:                      # no longer a slider: its strength back into 0-2
                lo, hi = strength_range(fields.get("slider", bool(entry.get("slider"))))
                fields["strength"] = max(lo, min(hi, float(entry.get("strength", 1.0))))
            entry.update(fields)
            self._save(items)
        return self.get(lid) or entry

    def delete(self, lid: str) -> None:
        with self._lock:
            items = self._load()
            if not any(x["id"] == lid for x in items):
                raise LoraError("No such LoRA.", 404)
            self._save([x for x in items if x["id"] != lid])
            self.path(lid).unlink(missing_ok=True)

    # ------------------------------------------------------------ use
    def resolve(self, wanted: list, fam: str) -> tuple[list[tuple[str, float]], list[str], list[str], list[str]]:
        """The LoRAs asked for ([{"id", "strength"}] or ids) that fit a model family -> (ComfyUI names with their
        strength, their trigger words, their names, notes on any left out)."""
        nodes: list[tuple[str, float]] = []
        triggers, names, notes = [], [], []
        seen: set[str] = set()
        for w in wanted or []:
            lid = w.get("id") if isinstance(w, dict) else w
            if not isinstance(lid, str) or lid in seen:
                continue
            seen.add(lid)
            entry = self.get(lid)
            if entry is None:
                notes.append(f"the LoRA {lid} is not there (Settings -> Models)")
                continue
            if entry.get("base") != fam:
                notes.append(f"the LoRA {entry['name']} is for {entry['base_label']} models, not this one - left out")
                continue
            strength = w.get("strength") if isinstance(w, dict) and w.get("strength") is not None \
                else entry.get("strength", 1.0)
            lo, hi = strength_range(bool(entry.get("slider")))
            try:
                strength = max(lo, min(hi, float(strength)))
            except (TypeError, ValueError):
                strength = float(entry.get("strength", 1.0))
            nodes.append((self.comfy_name(lid), strength))
            names.append(entry["name"])
            if entry.get("trigger"):
                triggers.append(entry["trigger"])
        return nodes, triggers, names, notes


def has_word(text: str, word: str) -> bool:
    """`word` in `text` as a word of its own, any case ("lora" is not in "explorative")."""
    return bool(word) and re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text, re.I) is not None


def with_triggers(prompt: str, triggers: list[str]) -> str:
    """The prompt with each LoRA's trigger word in it (put first when the prompt does not have it)."""
    missing = [t for t in triggers if t and not has_word(prompt, t)]
    return (", ".join(missing) + ", " + prompt) if missing else prompt
