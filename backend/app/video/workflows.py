"""ComfyUI workflows for video (API format), built in code from the official templates
(Comfy-Org/workflow_templates 0.11.93 for ComfyUI v0.42.3; read 2026-10-24). Only ComfyUI's own nodes.

Wan 2.2 5B (video_wan2_2_5B_ti2v.json): one model makes video from a description or from a picture.
UNETLoader -> ModelSamplingSD3 (shift 8); CLIPLoader (umt5-xxl, type "wan"); VAELoader (the Wan 2.2 VAE, 16x
spatial); Wan22ImageToVideoLatent (1280x704, 121 frames, the picture as its first frame when there is one);
KSampler (20 steps, cfg 5, uni_pc/simple); VAEDecodeTiled (below); CreateVideo (24 fps); SaveVideo.
Wan 2.2 5B Turbo (0.25.0) is the same graph with the Turbo LoRA (quanhaol's 4-step, CFG-distilled 5B, as Kijai's
rank-64 LoRA) in a LoraLoaderModelOnly after the UNETLoader, and 4 steps at cfg 1 - ComfyUI then skips the
negative pass - with UniPC (bh2) at shift 5, as the Turbo's own examples (diffusers' UniPC, flow_shift 5).

Wan 2.2 14B (video_wan2_2_14B_t2v.json, _i2v.json, _flf2v.json): two experts, high noise for the first half of the
steps and low noise for the rest, each a KSamplerAdvanced; with the lightx2v 4-step LoRAs (the templates' "fast"
switch: 4 steps split at 2, cfg 1) or without (20 steps split at 10, cfg 3.5); ModelSamplingSD3 shift 5 on both;
the Wan 2.1 VAE (8x). Text: EmptyHunyuanLatentVideo. A picture: WanImageToVideo. A first and a last picture:
WanFirstLastFrameToVideo. 16 fps, 81 frames for 5 s.

All end in VAEDecodeTiled -> CreateVideo -> SaveVideo (MP4, H.264). The templates decode the whole clip at once
(VAEDecode): on the 7900 XTX a 5-s 720p clip from the 5B then asked for 18.56 GiB in one piece and ran out of
memory, four times, after 10 minutes of sampling each (test run, 2026-10-25). ComfyUI's own fall-back sizes its
tiles from its estimate of the whole decode (13.4 GiB for that clip in bf16), which lets a tile be 1024 px - most
of the frame - and is probably why it did not save it. Tiles of 512 px (overlapping by 64) are under a third of
that. Time is not tiled: both Wan VAEs decode a latent frame or two at a time and carry a cache to the next, so a
shorter time tile saves nothing and would show a seam where two tiles meet.
"""
from __future__ import annotations

# Wan's own negative prompt, as every Wan template carries it (garish colours, overexposed, static, blurry detail,
# subtitles, ... extra fingers, deformed limbs, a still frame, a cluttered background, three legs, walking
# backwards). The user's negative goes after it.
WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，"
                "丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
                "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")


def frames(seconds: float, fps: int) -> int:
    """Wan's frame count for a length: 4n + 1 (81 at 16 fps and 121 at 24 fps for 5 s)."""
    return 4 * max(1, int(round(float(seconds) * fps)) // 4) + 1


def negative(extra: str) -> str:
    extra = (extra or "").strip()
    return f"{WAN_NEGATIVE}，{extra}" if extra else WAN_NEGATIVE


def _decode(samples: list, vae: list) -> dict:
    """The frames from the latent in 512-px tiles (VAEDecodeTiled's default size), each over the whole clip: the
    node's largest temporal_size, so the clip is one tile in time (its overlap then does nothing)."""
    return {"class_type": "VAEDecodeTiled", "inputs": {"samples": samples, "vae": vae, "tile_size": 512, "overlap": 64,
                                                        "temporal_size": 4096, "temporal_overlap": 8}}


def _save(video_ref: list, prefix: str) -> dict:
    return {"class_type": "SaveVideo",
            "inputs": {"video": video_ref, "filename_prefix": prefix, "format": "mp4", "format.codec": "h264"}}


def wan_5b(*, prompt: str, negative_prompt: str = "", seconds: float, width: int, height: int, seed: int, unet: str,
           text_encoder: str, vae: str, steps: int = 20, cfg: float = 5.0, shift: float = 8.0,
           sampler: str = "uni_pc", scheduler: str = "simple", fps: int = 24, start_image: str | None = None,
           lora: str | None = None, lora_strength: float = 1.0, prefix: str = "video/hub") -> dict:
    """lora given: the Turbo (or another LoRA on the 5B) between the model and its sampling."""
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": float(shift)}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": "wan", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": negative(negative_prompt)}},
        "8": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["4", 0], "width": int(width), "height": int(height),
                         "length": frames(seconds, fps), "batch_size": 1}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["8", 0],
                         "seed": int(seed) % 2**63, "steps": int(steps), "cfg": float(cfg), "sampler_name": sampler,
                         "scheduler": scheduler, "denoise": 1.0}},
        "10": _decode(["9", 0], ["4", 0]),
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": float(fps)}},
        "12": _save(["11", 0], prefix),
    }
    if start_image:
        graph["7"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
        graph["8"]["inputs"]["start_image"] = ["7", 0]
    if lora:
        graph["13"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": ["1", 0], "lora_name": lora, "strength_model": float(lora_strength)}}
        graph["2"]["inputs"]["model"] = ["13", 0]
    return graph


def wan_14b(*, prompt: str, negative_prompt: str = "", seconds: float, width: int, height: int, seed: int,
            high: str, low: str, text_encoder: str, vae: str, lora_high: str | None = None,
            lora_low: str | None = None, steps: int = 4, split: int = 2, cfg: float = 1.0, shift: float = 5.0,
            sampler: str = "euler", scheduler: str = "simple", fps: int = 16, start_image: str | None = None,
            end_image: str | None = None, prefix: str = "video/hub") -> dict:
    """The 14B pair: lora_high/lora_low given = the 4-step LoRAs. A start_image makes it picture-to-video (the
    i2v experts), an end_image too first-to-last-frame; neither, text-to-video (the t2v experts)."""
    if end_image and not start_image:
        raise ValueError("a last frame needs a first frame")
    steps, split = int(steps), max(1, min(int(split), int(steps) - 1))
    graph: dict = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": high, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": low, "weight_dtype": "default"}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": "wan", "device": "default"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": prompt}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": negative(negative_prompt)}},
    }
    high_ref, low_ref = ["1", 0], ["2", 0]
    if lora_high and lora_low:
        graph["3"] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": ["1", 0], "lora_name": lora_high, "strength_model": 1.0}}
        graph["4"] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": ["2", 0], "lora_name": lora_low, "strength_model": 1.0}}
        high_ref, low_ref = ["3", 0], ["4", 0]
    graph["5"] = {"class_type": "ModelSamplingSD3", "inputs": {"model": high_ref, "shift": float(shift)}}
    graph["6"] = {"class_type": "ModelSamplingSD3", "inputs": {"model": low_ref, "shift": float(shift)}}
    size = {"width": int(width), "height": int(height), "length": frames(seconds, fps), "batch_size": 1}
    if start_image:
        graph["11"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
        cond = {"positive": ["9", 0], "negative": ["10", 0], "vae": ["8", 0], **size, "start_image": ["11", 0]}
        if end_image:
            graph["12"] = {"class_type": "LoadImage", "inputs": {"image": end_image}}
            graph["13"] = {"class_type": "WanFirstLastFrameToVideo", "inputs": {**cond, "end_image": ["12", 0]}}
        else:
            graph["13"] = {"class_type": "WanImageToVideo", "inputs": cond}
        positive, negative_ref, latent = ["13", 0], ["13", 1], ["13", 2]
    else:
        graph["13"] = {"class_type": "EmptyHunyuanLatentVideo", "inputs": size}
        positive, negative_ref, latent = ["9", 0], ["10", 0], ["13", 0]
    common = {"steps": steps, "cfg": float(cfg), "sampler_name": sampler, "scheduler": scheduler,
              "positive": positive, "negative": negative_ref}
    graph["14"] = {"class_type": "KSamplerAdvanced",
                   "inputs": {"model": ["5", 0], "add_noise": "enable", "noise_seed": int(seed) % 2**63, **common,
                              "latent_image": latent, "start_at_step": 0, "end_at_step": split,
                              "return_with_leftover_noise": "enable"}}
    graph["15"] = {"class_type": "KSamplerAdvanced",
                   "inputs": {"model": ["6", 0], "add_noise": "disable", "noise_seed": 0, **common,
                              "latent_image": ["14", 0], "start_at_step": split, "end_at_step": 10000,
                              "return_with_leftover_noise": "disable"}}
    graph["16"] = _decode(["15", 0], ["8", 0])
    graph["17"] = {"class_type": "CreateVideo", "inputs": {"images": ["16", 0], "fps": float(fps)}}
    graph["18"] = _save(["17", 0], prefix)
    return graph


def class_types(graph: dict) -> set[str]:
    return {n["class_type"] for n in graph.values()}
