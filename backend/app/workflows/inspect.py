"""What a workflow needs and offers (0.67.0): the node kinds this ComfyUI lacks, the model files that are not on
the disk (and where they come from, when the workflow says), the values the assistant could set - a prompt, a
picture, a seed, a size - and what it makes."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from .convert import _widget_kind, ordered_inputs

MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft", ".pt2")
# ComfyUI's model folders a workflow's file may be fetched into (folder_paths.py, 0.42.3); "clip" and "unet" are
# its older names for two of them.
FOLDERS = {"audio_encoders", "background_removal", "checkpoints", "classifiers", "clip_vision", "controlnet",
           "detection", "diffusion_models", "embeddings", "frame_interpolation", "geometry_estimation", "gligen",
           "hypernetworks", "latent_upscale_models", "loras", "model_patches", "optical_flow", "photomaker",
           "style_models", "text_encoders", "upscale_models", "vae", "vae_approx"}
OLD_FOLDERS = {"clip": "text_encoders", "unet": "diffusion_models"}
# Where ComfyUI's own loaders look, for a file the workflow does not say where to fetch
LOADER_FOLDERS = {
    ("CheckpointLoaderSimple", "ckpt_name"): "checkpoints", ("ImageOnlyCheckpointLoader", "ckpt_name"): "checkpoints",
    ("UNETLoader", "unet_name"): "diffusion_models", ("VAELoader", "vae_name"): "vae",
    ("CLIPLoader", "clip_name"): "text_encoders", ("DualCLIPLoader", "clip_name1"): "text_encoders",
    ("DualCLIPLoader", "clip_name2"): "text_encoders", ("CLIPVisionLoader", "clip_name"): "clip_vision",
    ("LoraLoader", "lora_name"): "loras", ("LoraLoaderModelOnly", "lora_name"): "loras",
    ("UpscaleModelLoader", "model_name"): "upscale_models", ("ControlNetLoader", "control_net_name"): "controlnet",
    ("DiffControlNetLoader", "control_net_name"): "controlnet", ("StyleModelLoader", "style_model_name"): "style_models",
    ("LoadBackgroundRemovalModel", "bg_removal_name"): "background_removal",
    ("LoadMoGeModel", "model_name"): "geometry_estimation", ("ModelPatchLoader", "name"): "model_patches",
}
UPLOAD_KINDS = {"image_upload": "image", "audio_upload": "audio", "video_upload": "video"}
SIZE_NAMES = {"width", "height", "length", "seconds", "duration", "frames", "num_frames"}
SKIP_TEXT = {"filename_prefix", "control_after_generate"}
KINDS = ("image", "audio", "video", "text", "seed", "number", "boolean", "choice")
MAX_CHOICES = 500


def _link(value) -> bool:
    return (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
            and isinstance(value[1], int) and not isinstance(value[1], bool))


def _choices(kind, opts: dict) -> list:
    if isinstance(kind, list):
        return list(kind)
    out = opts.get("options")
    return [o.get("key") if isinstance(o, dict) else o for o in out] if isinstance(out, list) else []


def _upload(opts: dict) -> str | None:
    return next((k for flag, k in UPLOAD_KINDS.items() if opts.get(flag)), None)


def model_sources(editor: dict | None) -> dict[str, dict]:
    """The model files the editor's file names with where to fetch them (properties.models of its nodes, as
    ComfyUI's templates have them): file name -> {"url", "directory"}."""
    out: dict[str, dict] = {}
    if not isinstance(editor, dict):
        return out
    nodes = list(editor.get("nodes") or [])
    definitions = editor.get("definitions") if isinstance(editor.get("definitions"), dict) else {}
    for sub in definitions.get("subgraphs") or []:
        nodes += list(sub.get("nodes") or []) if isinstance(sub, dict) else []
    for node in nodes:
        props = node.get("properties") if isinstance(node, dict) else None
        models = props.get("models") if isinstance(props, dict) else None
        for m in models if isinstance(models, list) else []:
            if isinstance(m, dict) and isinstance(m.get("name"), str) and isinstance(m.get("url"), str):
                out.setdefault(m["name"], {"url": m["url"], "directory": str(m.get("directory") or "")})
    return out


def hf_source(url: str) -> dict | None:
    """A Hugging Face file link (…/resolve/<revision>/<path>) as the downloads take it, or None: only Hugging Face
    links are fetched by the Hub."""
    try:
        u = urlparse(url)
    except ValueError:
        return None
    if u.scheme != "https" or (u.hostname or "").lower() != "huggingface.co":
        return None
    parts = [unquote(p) for p in u.path.split("/") if p]
    if len(parts) < 5 or parts[2] != "resolve" or any(p in (".", "..") for p in parts):
        return None
    repo, rev, path = "/".join(parts[:2]), parts[3], "/".join(parts[4:])
    if rev == "main":
        return {"repo": repo, "path": path}
    return {"url": f"https://huggingface.co/{repo}/resolve/{rev}/{path}"}


def folder_for(directory: str) -> str | None:
    d = OLD_FOLDERS.get(directory, directory)
    return d if d in FOLDERS else None


def check(prompt: dict, info: dict, sources: dict | None = None, controls: dict | None = None) -> dict:
    """{"unknown": node kinds ComfyUI lacks, "missing": model files not there, "wrong": values not among a list's
    choices, "inputs": what the assistant could set, "outputs": the nodes that save something}. `controls`: the
    editor's seed controls (convert.to_api), so a seed its author fixed stays fixed."""
    sources = sources if isinstance(sources, dict) else {}
    controls = controls if isinstance(controls, dict) else {}
    nodes = {str(k): v for k, v in prompt.items() if isinstance(v, dict) and isinstance(v.get("inputs", {}), dict)}
    unknown = sorted({str(n.get("class_type")) for n in nodes.values() if n.get("class_type") not in info})
    missing, wrong, inputs, outputs = [], [], [], []
    consumers: dict[str, set[str]] = {}
    feeding: dict[str, set[tuple[str, str]]] = {}           # node -> (the node, its input) its outputs go into
    for nid, node in nodes.items():
        for name, value in (node.get("inputs") or {}).items():
            if _link(value):
                consumers.setdefault(value[0], set()).add(str(name))
                feeding.setdefault(value[0], set()).add((nid, str(name)))

    def encoder(nid: str) -> bool:
        entry = info.get((nodes.get(nid) or {}).get("class_type"))
        return isinstance(entry, dict) and "CONDITIONING" in (entry.get("output") or [])

    def text_input(nid: str, name: str) -> bool:
        """An input text can go into: a string, or one that takes whatever comes (a switch)."""
        entry = info.get((nodes.get(nid) or {}).get("class_type"))
        for n, kind, opts in ordered_inputs(entry) if isinstance(entry, dict) else ():
            if n == name:
                return kind in ("STRING", "*", "COMFY_MATCHTYPE_V3") or opts.get("widgetType") == "STRING"
        return False

    def encoders_reached(nid: str) -> list[str]:
        """The text encoders a node's text reaches - through switches and the like, a few nodes at most."""
        found, seen, todo = [], {nid}, [(nid, 0)]
        while todo:
            at, depth = todo.pop()
            for c, name in feeding.get(at) or ():
                if c in seen or not text_input(c, name):
                    continue
                seen.add(c)
                if encoder(c):
                    found.append(c)
                elif depth < 4 and {"STRING", "*", "COMFY_MATCHTYPE_V3"} & set(
                        (info.get((nodes.get(c) or {}).get("class_type")) or {}).get("output") or []):
                    todo.append((c, depth + 1))
        return found

    def negative(nid: str) -> bool:
        feeds = consumers.get(nid) or set()
        return "negative" in feeds and "positive" not in feeds
    keys: dict[str, int] = {}
    for nid, node in sorted(nodes.items(), key=lambda kv: _order(kv[0])):
        entry = info.get(node.get("class_type"))
        if not isinstance(entry, dict):
            continue
        cls = node["class_type"]
        if entry.get("output_node"):
            outputs.append({"node": nid, "class_type": cls, "title": _title(node, entry)})
        values = node.get("inputs") or {}
        feeds = consumers.get(nid) or set()
        # text a text encoder reads, or that becomes it on the way (a prompt-writing model's prompt)
        into = [] if encoder(nid) else encoders_reached(nid)
        encodes = encoder(nid) or bool(into)
        for name, kind, opts in ordered_inputs(entry):
            if name not in values or _link(values[name]):
                continue
            value = values[name]
            widget = _widget_kind(kind, opts)
            if widget is None:
                continue
            upload = _upload(opts)
            if widget == "COMBO" and upload:
                inputs.append(_input(keys, upload, nid, name, upload, node, entry, value, on=True))
                continue
            if widget == "COMBO":
                choices = _choices(kind, opts)
                if isinstance(value, str) and value.lower().endswith(MODEL_EXTS) and value not in choices:
                    src = sources.get(value) or sources.get(PurePosixPath(value).name) or {}
                    src = src if isinstance(src, dict) else {}
                    folder = folder_for(str(src.get("directory") or "")) or LOADER_FOLDERS.get((cls, name))
                    url = src.get("url") if isinstance(src.get("url"), str) and hf_source(src["url"]) else None
                    missing.append({"node": nid, "class_type": cls, "input": name, "file": value, "folder": folder,
                                    "url": url})
                    continue
                if isinstance(value, str) and value.lower().endswith(MODEL_EXTS):
                    continue                                         # a model file: not the assistant's to pick
                if choices and value not in choices and not opts.get("remote"):
                    wrong.append({"node": nid, "class_type": cls, "input": name, "value": value,
                                  "choices": [str(c) for c in choices[:20]]})
                if choices:
                    inputs.append(_input(keys, name, nid, name, "choice", node, entry, value, on=False,
                                         options=[c for c in choices if isinstance(c, (str, int, float))][:MAX_CHOICES]))
                continue
            if widget == "STRING":
                if name in SKIP_TEXT or not isinstance(value, str):
                    continue
                # a prompt is text a text encoder reads (its output is conditioning); the negative one feeds a
                # sampler's negative and no positive
                if encodes and opts.get("multiline"):
                    key = "negative" if (negative(nid) if encoder(nid) else all(negative(c) for c in into)) \
                        else "prompt"
                    inputs.append(_input(keys, key, nid, name, "text", node, entry, value, on=True))
                else:                                  # "prompt" and "negative" are kept for what an encoder reads
                    key = f"{name}_text" if name in ("prompt", "negative") else name
                    inputs.append(_input(keys, key, nid, name, "text", node, entry, value, on=False))
                continue
            if widget in ("INT", "FLOAT") and isinstance(value, (int, float)) and not isinstance(value, bool):
                seed = widget == "INT" and (name in ("seed", "noise_seed") or bool(feeds & {"seed", "noise_seed"}))
                limits = {k: opts[k] for k in ("min", "max", "step") if isinstance(opts.get(k), (int, float))
                          and not isinstance(opts.get(k), bool)}
                extra = {"control": (controls.get(nid) or {}).get(name)} if seed else {}
                inputs.append(_input(keys, "seed" if seed else name, nid, name, "seed" if seed else "number", node,
                                     entry, value, on=seed or name in SIZE_NAMES, integer=widget == "INT",
                                     **limits, **extra))
                continue
            if widget == "BOOLEAN" and isinstance(value, bool):
                inputs.append(_input(keys, name, nid, name, "boolean", node, entry, value, on=False))
    return {"unknown": unknown, "missing": missing, "wrong": wrong, "inputs": inputs, "outputs": outputs}


def _order(nid: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in str(nid).split(":"))


def clean(text, limit: int = 60) -> str:
    """A name from the file, as the assistant may read it: one line, short (a node's title is the file's text, and
    it goes into a tool's description)."""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _title(node: dict, entry: dict) -> str:
    meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
    return clean(meta.get("title") or entry.get("display_name") or node.get("class_type"))


def _input(keys: dict, base: str, nid: str, name: str, kind: str, node: dict, entry: dict, value, *, on: bool,
           **extra) -> dict:
    base = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_") or "value"
    n = keys.get(base, 0) + 1
    keys[base] = n
    key = base if n == 1 else f"{base}_{n}"
    return {"key": key, "node": nid, "input": name, "kind": kind, "title": _title(node, entry), "default": value,
            "on": on, "label": "", "description": "", **extra}
