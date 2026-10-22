"""ComfyUI workflows (API format), built in code from the official templates
(Comfy-Org/workflow_templates, ComfyUI v0.42.3) so the number of reference
images can vary.

Qwen-Image 2.1 is one checkpoint for generation and editing:
UNETLoader -> QwenImage21Cache -> KSampler, CLIPLoader(type qwen_image) +
VAELoader -> TextEncodeQwenImage21 (outputs: positive, negative, latent).
The turbo checkpoint samples 8 fixed sigmas with SamplerCustomAdvanced.

ERNIE-Image (and Turbo) is generation only; its 3B prompt enhancer runs in
the same graph (TextGenerate) and its text is read back through PreviewAny.
"""
from __future__ import annotations

import json

QWEN = {
    "unet": "qwen_image_2.1_int8_convrot.safetensors",
    "unet_turbo": "qwen_image_2.1_turbo_int8_convrot.safetensors",
    "clip": "qwen3vl_8b_int8_convrot.safetensors",
    "vae": "qwen_image_2.1_vae_bf16.safetensors",
}
QWEN_TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
KLEIN = {"unet": "flux-2-klein-4b.safetensors", "clip": "qwen_3_4b.safetensors", "vae": "flux2-vae.safetensors"}


def snap(v: int, step: int = 32, lo: int = 256, hi: int = 2048) -> int:
    return max(lo, min(hi, int(round(int(v) / step)) * step))


def _save(images_ref: list, prefix: str, png_alpha: bool) -> dict:
    if png_alpha:
        return {"class_type": "SaveImageAdvanced",
                "inputs": {"images": images_ref, "filename_prefix": prefix, "format": "png",
                           "format.bit_depth": "8-bit", "format.input_color_space": "sRGB"}}
    return {"class_type": "SaveImage", "inputs": {"images": images_ref, "filename_prefix": prefix}}


def _qwen_base(turbo: bool) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": QWEN["unet_turbo" if turbo else "unet"],
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN["clip"], "type": "qwen_image",
                                                     "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN["vae"]}},
    }


def _sample(g: dict, latent_ref: list, seed: int, steps: int, cfg: float, turbo: bool) -> list:
    """Adds the sampler; returns the reference to the sampled latent."""
    if turbo:
        g["20"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
        g["21"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
        g["22"] = {"class_type": "CFGGuider", "inputs": {"model": ["2", 0], "positive": ["5", 0],
                                                         "negative": ["5", 1], "cfg": 1.0}}
        g["23"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": QWEN_TURBO_SIGMAS}}
        g["24"] = {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["20", 0], "guider": ["22", 0], "sampler": ["21", 0], "sigmas": ["23", 0],
                              "latent_image": latent_ref}}
        return ["24", 0]
    g["7"] = {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["5", 0], "negative": ["5", 1],
                                                   "latent_image": latent_ref, "seed": seed, "steps": int(steps),
                                                   "cfg": float(cfg), "sampler_name": "euler",
                                                   "scheduler": "simple", "denoise": 1.0}}
    return ["7", 0]


def qwen_t2i(prompt: str, width: int, height: int, seed: int, steps: int = 25, cfg: float = 1.0,
             turbo: bool = False, negative: str = "", prefix: str = "hub") -> dict:
    g = _qwen_base(turbo)
    g["5"] = {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["3", 0], "prompt": prompt, "negative_prompt": negative, "resolution": 1024}}
    g["6"] = {"class_type": "EmptyLatentImage", "inputs": {"width": snap(width), "height": snap(height),
                                                           "batch_size": 1}}
    out = _sample(g, ["6", 0], seed, steps, cfg, turbo)
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": out, "vae": ["4", 0]}}
    g["9"] = _save(["8", 0], prefix, png_alpha=False)
    return g


def qwen_edit(prompt: str, image_names: list[str], seed: int, steps: int = 25, cfg: float = 1.0,
              turbo: bool = False, negative: str = "", resolution: int = 1024, prefix: str = "hub",
              png_alpha: bool = False) -> dict:
    """Edit with 1..10 reference images (uploaded to ComfyUI's input folder).
    The output takes the first reference's size (scaled to ~resolution^2)."""
    if not image_names:
        raise ValueError("an edit needs at least one image")
    g = _qwen_base(turbo)
    enc_inputs: dict = {"clip": ["3", 0], "prompt": prompt, "negative_prompt": negative, "vae": ["4", 0],
                        "resolution": int(resolution)}
    for i, name in enumerate(image_names[:10], start=1):
        nid = str(100 + i)
        g[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
        enc_inputs[f"images.image_{i}"] = [nid, 0]
    g["5"] = {"class_type": "TextEncodeQwenImage21", "inputs": enc_inputs}
    out = _sample(g, ["5", 2], seed, steps, cfg, turbo)
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": out, "vae": ["4", 0]}}
    g["9"] = _save(["8", 0], prefix, png_alpha)
    return g


def qwen_remove_background(image_name: str, seed: int, steps: int = 25, resolution: int = 1024,
                           prefix: str = "hub-cutout") -> dict:
    return qwen_edit("Remove the background, and output a PNG image", [image_name], seed, steps=steps,
                     resolution=resolution, prefix=prefix, png_alpha=True)


def _klein_base() -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": KLEIN["unet"], "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": KLEIN["clip"], "type": "flux2", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": KLEIN["vae"]}},
    }


def klein_t2i(prompt: str, width: int, height: int, seed: int, steps: int = 4, prefix: str = "hub") -> dict:
    w, h = snap(width, 16), snap(height, 16)
    g = _klein_base()
    g["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}}
    g["6"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}}
    g["7"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    g["10"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    g["11"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    g["12"] = {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0],
                                                     "cfg": 1.0}}
    g["13"] = {"class_type": "Flux2Scheduler", "inputs": {"steps": int(steps), "width": w, "height": h}}
    g["14"] = {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["10", 0], "guider": ["12", 0], "sampler": ["11", 0], "sigmas": ["13", 0],
                          "latent_image": ["7", 0]}}
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["4", 0]}}
    g["9"] = _save(["8", 0], prefix, png_alpha=False)
    return g


def klein_edit(prompt: str, image_names: list[str], seed: int, steps: int = 4, prefix: str = "hub") -> dict:
    if not image_names:
        raise ValueError("an edit needs at least one image")
    g = _klein_base()
    g["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}}
    g["6"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}}
    pos, neg = ["5", 0], ["6", 0]
    first_scaled = None
    for i, name in enumerate(image_names[:6], start=1):
        load, scale, enc = str(100 + i), str(110 + i), str(120 + i)
        g[load] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[scale] = {"class_type": "ImageScaleToTotalPixels",
                    "inputs": {"image": [load, 0], "upscale_method": "nearest-exact", "megapixels": 1.0,
                               "resolution_steps": 1}}
        g[enc] = {"class_type": "VAEEncode", "inputs": {"pixels": [scale, 0], "vae": ["4", 0]}}
        rp, rn = str(130 + i), str(140 + i)
        g[rp] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": pos, "latent": [enc, 0]}}
        g[rn] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": neg, "latent": [enc, 0]}}
        pos, neg = [rp, 0], [rn, 0]
        first_scaled = first_scaled or [scale, 0]
    g["15"] = {"class_type": "GetImageSize", "inputs": {"image": first_scaled}}
    g["7"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": ["15", 0], "height": ["15", 1],
                                                                "batch_size": 1}}
    g["10"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    g["11"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    g["12"] = {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": pos, "negative": neg, "cfg": 1.0}}
    g["13"] = {"class_type": "Flux2Scheduler", "inputs": {"steps": int(steps), "width": ["15", 0], "height": ["15", 1]}}
    g["14"] = {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["10", 0], "guider": ["12", 0], "sampler": ["11", 0], "sigmas": ["13", 0],
                          "latent_image": ["7", 0]}}
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["4", 0]}}
    g["9"] = _save(["8", 0], prefix, png_alpha=False)
    return g


# ---------------------------------------------------------------- ERNIE-Image

ERNIE = {
    "unet": "ernie-image.safetensors",
    "unet_turbo": "ernie-image-turbo.safetensors",
    "clip": "ministral-3-3b.safetensors",
    "enhancer": "ernie-image-prompt-enhancer.safetensors",
    "vae": "flux2-vae.safetensors",
}
# The prompt enhancer's own instruction, word for word from the official
# templates (image_ernie_image*.json): "You are a professional text-to-image
# prompt-enhancement assistant. You get a user's short image description and the
# target resolution; expand it into a rich, detailed visual description that
# helps the model make a high-quality image. Output only the enhanced
# description, with no explanation or prefix."
ERNIE_ENHANCER_SYSTEM = ("你是一个专业的文生图 Prompt 增强助手。你将收到用户的简短图片描述及目标生成分辨率，请据此扩写为一段内容丰富、"
                         "细节充分的视觉描述，以帮助文生图模型生成高质量的图片。仅输出增强后的描述，不要包含任何解释或前缀。")
ERNIE_PROMPT_NODE = "31"          # PreviewAny: the prompt the image was made from, read back from the history


def ernie_enhancer_input(prompt: str, width: int, height: int) -> str:
    """The enhancer's chat-formatted input (Mistral tokens, as in the templates);
    the request goes in as JSON, so quotes and newlines in it stay harmless."""
    request = json.dumps({"prompt": prompt, "width": int(width), "height": int(height)}, ensure_ascii=False)
    return f"<s>[SYSTEM_PROMPT]{ERNIE_ENHANCER_SYSTEM}[/SYSTEM_PROMPT][INST]{request}[/INST]"


def ernie_t2i(prompt: str, width: int, height: int, seed: int, steps: int | None = None, turbo: bool = False,
              enhance: bool = True, cfg: float | None = None, prefix: str = "hub") -> dict:
    """ERNIE-Image text-to-image, after the official templates: the optional
    prompt enhancer (TextGenerate on the 3B enhancer) writes the prompt that
    ministral-3-3b encodes; base 25 steps at cfg 4 with an empty negative,
    Turbo 8 steps at cfg 1 with a zeroed negative; euler/simple."""
    w, h = snap(width, 16), snap(height, 16)
    g: dict = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": ERNIE["unet_turbo" if turbo else "unet"],
                                                     "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": ERNIE["clip"], "type": "flux2", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": ERNIE["vae"]}},
    }
    text: str | list = prompt
    if enhance:
        g["30"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": ERNIE["enhancer"], "type": "flux2",
                                                          "device": "default"}}
        g["32"] = {"class_type": "TextGenerate",
                   "inputs": {"clip": ["30", 0], "prompt": ernie_enhancer_input(prompt, w, h), "max_length": 2048,
                              "sampling_mode": "on", "sampling_mode.temperature": 0.6, "sampling_mode.top_k": 64,
                              "sampling_mode.top_p": 0.8, "sampling_mode.min_p": 0.05,
                              "sampling_mode.repetition_penalty": 1.05,
                              "sampling_mode.seed": int(seed) % 2**63, "sampling_mode.presence_penalty": 0.0,
                              "thinking": False, "use_default_template": True}}
        text = ["32", 0]
        g[ERNIE_PROMPT_NODE] = {"class_type": "PreviewAny", "inputs": {"source": ["32", 0]}}
    g["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": text}}
    if turbo:
        g["6"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}}
    else:
        g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ""}}
    g["7"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    g["10"] = {"class_type": "KSampler",
               "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                          "seed": int(seed), "steps": int(steps or (8 if turbo else 25)),
                          "cfg": float(cfg if cfg is not None else (1.0 if turbo else 4.0)),
                          "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}}
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}}
    g["9"] = _save(["8", 0], prefix, png_alpha=False)
    return g


def class_types(graph: dict) -> set[str]:
    return {n["class_type"] for n in graph.values()}
