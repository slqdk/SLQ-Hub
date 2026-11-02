"""A 3D model from one picture (0.67.0): TRELLIS.2 in ComfyUI's own nodes (0.42.3), after its official template
3d_pixal3d_trellis2_image_to_model with TRELLIS.2 chosen - shape only, for printing: the subject cut out (BiRefNet),
encoded (DINOv3), a coarse structure, the shape, its finer pass, then ComfyUI's remesh into one closed solid and
fewer triangles. ComfyUI saves a GLB; mesh.py makes the STL and the picture of it."""
from __future__ import annotations

import io
import random
import time

from PIL import Image, ImageOps

from ..gpu.comfy import ComfyError
from ..gpu.scheduler import SchedulerError, Ticket
from ..state import Hub

DETAILS = {"standard": 1024, "high": 1536}       # Trellis2UpsampleStage's voxel grid: more detail, more memory
MAX_PICTURE_BYTES = 50 * 1024 * 1024


class Model3DError(RuntimeError):
    def __init__(self, message: str = "", hub: bool = False):
        super().__init__(message)
        self.hub = hub


def comfy_name(entry: dict, role: str) -> str:
    f = next((x for x in entry.get("files") or [] if x.get("role") == role), None)
    if f is None:
        raise Model3DError(f"{entry.get('name')} has no {role} file in the catalog")
    parts = f["dest"].split("/")
    return "/".join(parts[2:]) if len(parts) > 2 and parts[0] == "comfy" else parts[-1]


def trellis2_graph(*, image: str, unet: str, encoder: str, shape_vae: str, background: str, seed: int,
                   resolution: int = 1024, faces: int = 300_000, prefix: str = "3d/hub") -> dict:
    """The shape-only TRELLIS.2 graph (API format). Its samplers and their settings are the template's: the
    structure pass at 12 steps with cfg 7.5 (rescaled 0.7, cfg 1 after 66.7 %, shift 5), the shape and its finer
    pass at 20 and 12 steps (rescaled 0.5, cfg 1 after 76.9 %). The picture is cut out and squared with
    pad_factor 1.0, which Trellis2Conditioning asks for. RemeshMesh "solid" makes one watertight solid (its
    double shells and small holes gone), DecimateMesh keeps `faces` triangles."""
    def ksampler(seed_: int, steps: int, scheduler: str, model: list, cond: str, latent: list) -> dict:
        return {"class_type": "KSampler", "inputs": {"seed": seed_, "steps": steps, "cfg": 7.5, "sampler_name": "euler",
                                                     "scheduler": scheduler, "denoise": 1, "model": model,
                                                     "positive": [cond, 0], "negative": [cond, 1],
                                                     "latent_image": latent}}
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": background}},
        "3": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "ImageCropToMask", "inputs": {"width": 1024, "height": 1024, "pad_factor": 1.0,
                                                          "grow_mask": 0, "background": "#000000",
                                                          "images": ["1", 0], "masks": ["3", 0]}},
        "5": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": encoder}},
        "6": {"class_type": "Trellis2Conditioning", "inputs": {"clip_vision_model": ["5", 0], "image": ["4", 0]}},
        "7": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "8": {"class_type": "CFGOverride", "inputs": {"cfg": 1, "start_percent": 0.667, "end_percent": 1,
                                                      "model": ["7", 0]}},
        "9": {"class_type": "RescaleCFG", "inputs": {"multiplier": 0.7, "model": ["8", 0]}},
        "10": {"class_type": "ModelSamplingSD3", "inputs": {"shift": 5, "model": ["9", 0]}},
        "11": {"class_type": "EmptyTrellis2LatentStructure", "inputs": {"batch_size": 1}},
        "12": ksampler(seed, 12, "normal", ["10", 0], "6", ["11", 0]),
        "13": {"class_type": "VAELoader", "inputs": {"vae_name": shape_vae}},
        "14": {"class_type": "VaeDecodeStructureTrellis2", "inputs": {"resolution": "32", "samples": ["12", 0],
                                                                      "vae": ["13", 0]}},
        "15": {"class_type": "Trellis2ShapeStage", "inputs": {"positive": ["6", 0], "negative": ["6", 1],
                                                              "voxel": ["14", 0]}},
        "16": {"class_type": "CFGOverride", "inputs": {"cfg": 1, "start_percent": 0.769, "end_percent": 1,
                                                       "model": ["7", 0]}},
        "17": {"class_type": "RescaleCFG", "inputs": {"multiplier": 0.5, "model": ["16", 0]}},
        "18": ksampler(seed + 1, 20, "normal", ["17", 0], "15", ["15", 2]),
        "19": {"class_type": "Trellis2UpsampleStage", "inputs": {"target_resolution": resolution,
                                                                 "positive": ["15", 0], "negative": ["15", 1],
                                                                 "shape_latent": ["18", 0], "vae": ["13", 0]}},
        "20": ksampler(seed + 2, 12, "simple", ["17", 0], "19", ["19", 2]),
        "21": {"class_type": "VaeDecodeShapeTrellis", "inputs": {"samples": ["20", 0], "vae": ["13", 0]}},
        "22": {"class_type": "RemeshMesh", "inputs": {"resolution": 768, "sign_mode": "solid", "sign_mode.fill": 20,
                                                      "sign_mode.qef": False, "band": 1, "project_back": 0,
                                                      "fix_poles": False, "smooth_iters": 20,
                                                      "drop_small_components": 0.01,
                                                      "precluster_max_verts": 20000000, "mesh": ["21", 0]}},
        "23": {"class_type": "DecimateMesh", "inputs": {"target_face_count": faces, "placement_mode": "midpoint",
                                                        "mesh": ["22", 0]}},
        "24": {"class_type": "SaveGLB", "inputs": {"filename_prefix": prefix, "mesh": ["23", 0]}},
    }


def as_png(data: bytes) -> bytes:
    """The picture upright as PNG - its transparency kept (a cut-out already is what BiRefNet would make)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info else "RGB")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - PIL raises many kinds
        raise Model3DError(f"the picture could not be read ({exc.__class__.__name__})")


class Model3DPipeline:
    def __init__(self, hub: Hub):
        self.hub = hub

    def installed(self) -> list[dict]:
        return [e for e in self.hub.catalog.entries("model3d") if self.hub.catalog.installed(e)]

    def available(self) -> bool:
        return bool(self.installed())

    async def make(self, picture: bytes, *, detail: str = "standard", seed: int | None = None,
                   ticket: Ticket | None = None, owner: str = "web", priority: str = "interactive",
                   on_progress=None, keep: bool = True) -> dict:
        models = self.installed()
        if not models:
            raise Model3DError("no 3D model is downloaded (Settings -> Models -> 3D)")
        entry = models[0]
        if len(picture) > MAX_PICTURE_BYTES:
            raise Model3DError("the picture is over 50 MB")
        if detail not in DETAILS:
            raise Model3DError(f"detail must be one of {', '.join(DETAILS)}")
        s = entry.get("settings") or {}
        sd = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        own = None
        name = None
        t0 = time.monotonic()
        try:
            name = await self.hub.comfy.upload_image(as_png(picture), f"hub-{int(time.time() * 1000)}-3d.png")
            graph = trellis2_graph(image=name, unet=comfy_name(entry, "unet"), encoder=comfy_name(entry, "encoder"),
                                   shape_vae=comfy_name(entry, "shape_vae"),
                                   background=comfy_name(entry, "background"), seed=sd,
                                   resolution=int((s.get("resolution") or DETAILS).get(detail, DETAILS[detail])),
                                   faces=int(s.get("faces") or 300_000), prefix=f"3d/hub-{int(time.time())}")
            if ticket is not None:
                await ticket.switch("comfy")
            else:
                own = await self.hub.scheduler.acquire(owner, "3d", "comfy", priority, label="3D model",
                                                       max_wait=float(self.hub.settings.get("queue", "max_wait_s")))
            info = await self.hub.comfy.object_info()
            missing = sorted({n["class_type"] for n in graph.values()} - set(info))
            if missing:
                raise Model3DError(f"ComfyUI does not know these nodes: {', '.join(missing)} - update ComfyUI "
                                   "(COMFYUI_REF in .env)", hub=True)
            t0 = time.monotonic()
            items, _ = await self.hub.comfy.run_any(graph, on_progress=on_progress, timeout=3600, keep=keep)
        except (ComfyError, SchedulerError) as exc:
            raise Model3DError(str(exc), hub=True) from exc
        finally:
            if own is not None:
                await own.release()
            if name:
                self.hub.comfy.delete_files([("input", "", name)])
        glb = next((i["data"] for i in items if i["kind"] == "3d"), None)
        if glb is None:
            raise Model3DError("ComfyUI finished without a 3D file", hub=True)
        return {"glb": glb, "seed": sd, "detail": detail, "model": entry, "compute": round(time.monotonic() - t0, 1)}
