"""The model catalog: which models exist, where their files are, how to
start them, and what names apps may use for them.

The bundled catalog is app/catalog/models.json. Per-model changes made in
Settings -> Models (context, slots, extra llama-server flags) are stored in
<data>/models.override.json and merged over it. Language models added from
Hugging Face (Settings -> Models -> Add from Hugging Face, app/hf.py) and SDXL
picture models added from a link (Add image model, app/images/sources.py)
live in <data>/models.custom.json and are marked "custom".
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent / "catalog" / "models.json"
# Sampling for a model added from Hugging Face, whose entry has none: what its family recommends, as the
# bundled Qwen cards use (Qwen3 and the many fine-tunes built on it: presence_penalty 1.5 against endless
# repetition). Without it llama-server's own defaults apply, with no presence penalty at all.
QWEN3_SAMPLING = {"thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5},
                  "instruct": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}}


# Sampling values that may be set per model in Settings -> Models, and their limits.
SAMPLING_LIMITS = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0), "top_k": (0, 500), "min_p": (0.0, 1.0),
                   "presence_penalty": (-2.0, 2.0), "repeat_penalty": (0.5, 2.0)}
SAMPLING_MODES = ("instruct", "thinking")
THINKING_MODES = ("follow", "on", "off")


def clean_sampling(values) -> dict:
    """One mode's sampling values from Settings, checked; empty fields are left out."""
    if not isinstance(values, dict):
        raise ValueError("sampling values must be an object")
    out = {}
    for key, raw in values.items():
        if key not in SAMPLING_LIMITS:
            raise ValueError(f"not a sampling value: {key}")
        if raw is None or raw == "":
            continue
        lo, hi = SAMPLING_LIMITS[key]
        try:
            v = int(raw) if key == "top_k" else float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if isinstance(raw, bool) or not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        out[key] = v
    return out


def family_sampling(architecture: str | None) -> dict | None:
    if (architecture or "").lower().startswith("qwen3"):          # qwen3, qwen3moe, qwen35, qwen35moe, qwen3next
        return copy.deepcopy(QWEN3_SAMPLING)
    return None
EDITABLE_SERVER_KEYS = {"ctx", "parallel", "kv_unified", "cache_type", "gpu_layers", "flash_attn",
                        "cache_ram_mib", "extra"}


class Catalog:
    def __init__(self, models_dir: Path, override_path: Path, bundled: Path = BUNDLED,
                 custom_path: Path | None = None):
        self.models_dir = models_dir
        self.override_path = override_path
        self.custom_path = custom_path or override_path.with_name("models.custom.json")
        self.bundled = bundled
        self._lock = threading.Lock()
        self._data = self._load()

    # ------------------------------------------------------------ loading
    @staticmethod
    def _read(path: Path) -> dict:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _load(self) -> dict:
        data = json.loads(self.bundled.read_text(encoding="utf-8"))
        over = self._read(self.override_path)
        # Models added from Hugging Face (Settings -> Models), after the bundled ones.
        known = {e["id"] for g in ("llm", "aux", "comfy") for e in data.get(g, [])}
        custom = self._read(self.custom_path)
        for c in custom.get("llm") or []:
            if isinstance(c, dict) and c.get("id") and c["id"] not in known and c.get("files") and c.get("server"):
                entry = {**c, "custom": True}
                if not entry.get("sampling"):
                    arch = entry.get("architecture") or self._architecture(entry)
                    if arch:
                        entry["architecture"] = arch
                    fam = family_sampling(arch)
                    if fam:
                        entry["sampling"] = fam
                data.setdefault("llm", []).append(entry)
                known.add(c["id"])
        for c in custom.get("comfy") or []:
            if isinstance(c, dict) and c.get("id") and c["id"] not in known and c.get("files") \
                    and c.get("family") == "sdxl":
                data.setdefault("comfy", []).append({**c, "custom": True})
                known.add(c["id"])
        for entry in data.get("llm", []):
            o = (over.get("llm") or {}).get(entry["id"]) or {}
            if isinstance(o.get("server"), dict):
                entry["server"].update({k: v for k, v in o["server"].items() if k in EDITABLE_SERVER_KEYS})
            if isinstance(o.get("aliases"), list):
                entry["aliases"] = [str(a) for a in o["aliases"]]
            if isinstance(o.get("sampling"), dict):          # set in Settings: it replaces the card's, per mode
                entry["sampling_card"] = copy.deepcopy(entry.get("sampling") or {})
                own = {m: v for m, v in o["sampling"].items() if m in SAMPLING_MODES and isinstance(v, dict)}
                entry["sampling"] = {**(entry.get("sampling") or {}), **own}
                entry["sampling_own"] = True
            if o.get("thinking_mode") in ("on", "off"):          # think in chats regardless of Settings -> Assistant
                entry["thinking_mode"] = o["thinking_mode"]
        return data

    def _architecture(self, entry: dict) -> str | None:
        """From the model file's own header (models added before 0.9.1 have none stored)."""
        from . import gguf
        first = next((f for f in entry.get("files") or [] if not f.get("role")), None)
        if not first or not first.get("dest"):
            return None
        path = self.models_dir / first["dest"]
        return gguf.architecture(path) if path.is_file() else None

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()

    def save_override(self, model_id: str, server: dict | None = None, aliases: list | None = None,
                      sampling: dict | None = None, reset_sampling: bool = False,
                      thinking_mode: str | None = None) -> dict:
        with self._lock:
            if not any(e["id"] == model_id for e in self._data.get("llm", [])):
                raise KeyError(model_id)
            try:
                over = json.loads(self.override_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                over = {}
            entry = over.setdefault("llm", {}).setdefault(model_id, {})
            if server is not None:
                bad = set(server) - EDITABLE_SERVER_KEYS
                if bad:
                    raise ValueError(f"not editable: {', '.join(sorted(bad))}")
                entry.setdefault("server", {}).update(server)
            if aliases is not None:
                entry["aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
            if reset_sampling:
                entry.pop("sampling", None)
            elif sampling is not None:
                bad = set(sampling) - set(SAMPLING_MODES)
                if bad:
                    raise ValueError(f"sampling modes are {' and '.join(SAMPLING_MODES)}, not {', '.join(sorted(bad))}")
                entry["sampling"] = {mode: clean_sampling(values) for mode, values in sampling.items()}
            if thinking_mode is not None:
                if thinking_mode not in THINKING_MODES:
                    raise ValueError(f"thinking_mode is one of {', '.join(THINKING_MODES)}")
                if thinking_mode == "follow":
                    entry.pop("thinking_mode", None)
                else:
                    entry["thinking_mode"] = thinking_mode
            self.override_path.parent.mkdir(parents=True, exist_ok=True)
            self.override_path.write_text(json.dumps(over, indent=1), encoding="utf-8")
        self.reload()
        return self.get_llm(model_id) or {}

    def add_custom(self, entry: dict, group: str = "llm") -> dict:
        """Add a language model from Hugging Face (hf.make_entry) or, with
        group "comfy", an SDXL picture model from a link (sources.make_entry)."""
        with self._lock:
            taken = {e["id"] for g in ("llm", "aux", "comfy") for e in self._data.get(g, [])}
            if entry["id"] in taken:
                raise ValueError(f"There is already a model called {entry['id']}.")
            data = self._read(self.custom_path)
            data.setdefault(group, []).append({k: v for k, v in entry.items() if k != "custom"})
            self._write(self.custom_path, data)
        self.reload()
        return self.get(group, entry["id"]) or {}

    def update_custom(self, model_id: str, changes: dict) -> dict:
        """Change an added picture model's name or SDXL settings."""
        with self._lock:
            data = self._read(self.custom_path)
            entry = next((e for e in data.get("comfy") or [] if isinstance(e, dict) and e.get("id") == model_id), None)
            if entry is None:
                raise KeyError(model_id)
            entry.update({k: v for k, v in changes.items() if k in ("name", "sdxl")})
            self._write(self.custom_path, data)
        self.reload()
        return self.get("comfy", model_id) or {}

    def remove_custom(self, model_id: str) -> dict:
        """Forget an added model; returns its entry (files are the caller's)."""
        with self._lock:
            data = self._read(self.custom_path)
            gone = None
            for group in ("llm", "comfy"):
                items = data.get(group) or []
                gone = next((e for e in items if isinstance(e, dict) and e.get("id") == model_id), None)
                if gone is not None:
                    data[group] = [e for e in items if e is not gone]
                    break
            if gone is None:
                raise KeyError(model_id)
            self._write(self.custom_path, data)
            over = self._read(self.override_path)
            if model_id in (over.get("llm") or {}):
                del over["llm"][model_id]
                self._write(self.override_path, over)
        self.reload()
        return gone

    # ------------------------------------------------------------ lookups
    def entries(self, group: str) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._data.get(group, []))

    def llm_models(self) -> list[dict]:
        return self.entries("llm")

    def get(self, group: str, entry_id: str) -> dict | None:
        return next((e for e in self.entries(group) if e["id"] == entry_id), None)

    def get_llm(self, model_id: str) -> dict | None:
        return self.get("llm", model_id)

    def path(self, f: dict) -> Path:
        return self.models_dir / f["dest"]

    def installed(self, entry: dict) -> bool:
        return all(self.path(f).is_file() for f in entry.get("files", []))

    def resolve(self, name: str | None, fallback: str = "") -> str | None:
        """A catalog id for a name an app sent: the id itself, an alias, either
        with an Ollama-style ':latest', or the fallback (may be '')."""
        raw = (name or "").strip()
        if not raw:
            return fallback or None
        cands = {raw, raw.lower()}
        for suffix in (":latest",):
            if raw.lower().endswith(suffix):
                cands.add(raw[: -len(suffix)])
                cands.add(raw[: -len(suffix)].lower())
        models = self.llm_models()
        for m in models:
            names = {m["id"], m["id"].lower(), *m.get("aliases", []), *(a.lower() for a in m.get("aliases", []))}
            if cands & names:
                return m["id"]
        if fallback and any(m["id"] == fallback for m in models):
            return fallback
        return None

    # ------------------------------------------------------------ starting
    def server_args(self, model_id: str, container_models_dir: str = "/models") -> list[str]:
        m = self.get_llm(model_id)
        if not m:
            raise KeyError(model_id)
        s = m["server"]
        files = m["files"]
        main = next(f for f in files if f.get("role") != "mmproj")
        args = ["-m", f"{container_models_dir}/{main['dest']}"]
        mm = next((f for f in files if f.get("role") == "mmproj"), None)
        if mm:
            args += ["--mmproj", f"{container_models_dir}/{mm['dest']}"]
        args += ["-c", str(int(s.get("ctx", 32768))), "-np", str(int(s.get("parallel", 1)))]
        if s.get("kv_unified"):
            args.append("-kvu")
            per = self.context_per_request(model_id)
            if per < int(s.get("ctx", 32768)):           # one request may not take the whole shared pool
                args += ["--kv-unified-per-slot", str(per)]
        if s.get("cache_type"):
            args += ["-ctk", str(s["cache_type"]), "-ctv", str(s["cache_type"])]
        args += ["-ngl", str(s.get("gpu_layers", "all")), "-fa", str(s.get("flash_attn", "on"))]
        args += ["--jinja", "--reasoning-format", "deepseek", "--metrics", "--no-webui",
                 "--alias", model_id]
        if s.get("cache_ram_mib") is not None:
            args += ["--cache-ram", str(int(s["cache_ram_mib"]))]
        extra = [str(a) for a in s.get("extra", [])]
        if not {"--cache-idle-slots", "--no-cache-idle-slots"} & set(extra):
            # llama-server's default: every new request moves the idle slots into the RAM prompt cache and clears
            # them from the shared KV cache, and a conversation too big for that cache is simply lost - a chat's
            # title on the other slot made its next message read 30K tokens again (chat 11). Idle slots stay on
            # the card instead, until the room is needed.
            args.append("--no-cache-idle-slots")
        return args + extra

    def aux_args(self, aux_id: str, container_models_dir: str = "/models") -> list[str]:
        a = self.get("aux", aux_id)
        if not a:
            raise KeyError(aux_id)
        files = a["files"]
        main = next(f for f in files if f.get("role") != "mmproj")
        args = ["-m", f"{container_models_dir}/{main['dest']}"]
        mm = next((f for f in files if f.get("role") == "mmproj"), None)
        if mm:
            args += ["--mmproj", f"{container_models_dir}/{mm['dest']}"]
        args += [str(x) for x in a.get("args", [])]
        args += ["--no-webui", "--alias", aux_id]
        return args

    def slots(self, model_id: str) -> int:
        m = self.get_llm(model_id)
        return max(1, int((m or {}).get("server", {}).get("parallel", 1)))

    def context_per_request(self, model_id: str) -> int:
        m = self.get_llm(model_id) or {}
        s = m.get("server", {})
        if m.get("context_per_request"):
            return int(m["context_per_request"])
        ctx = int(s.get("ctx", 32768))
        return ctx if s.get("kv_unified") else ctx // max(1, int(s.get("parallel", 1)))
