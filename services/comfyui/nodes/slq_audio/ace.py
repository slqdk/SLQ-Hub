"""ACE-Step 1.5: a reference sound and a cover source as two separate things.

ComfyUI's own "Set Reference Audio" (ReferenceTimbreAudio, v0.42.3) hands one latent to the model for both: the
timbre encoder hears it, and its semantic codes become the structure of the new piece (cover mode). That is what a
cover of the reference needs - and wrong for a short voice or style clip under a new song, which would then follow
the clip for its length and silence after it.

SLQAceStepReference patches the model so that
  - timbre: a clip only the timbre encoder hears (up to 30 s: a singer, another song's sound), while the structure
    comes from the planner's audio codes as usual, and
  - source: a song whose semantic codes (the model's own tokenizer, 5 a second) become the structure - a cover -
    with the timbre from the timbre clip when there is one.
ACE-Step 1.5 itself keeps these apart (its reference audio and its source audio); the patch only routes them.

SLQAceStepEncode is TextEncodeAceStepAudio1.5 without the planner and with the metadata optional: a cover takes
its codes from the source, and a bpm or key nobody knows is better left out ("N/A") than guessed."""
from __future__ import annotations

import torch

import comfy.model_base
import comfy.utils
from comfy_api.latest import IO

MAX_TIMBRE_FRAMES = 750          # 30 s at 25 latent frames a second, what the timbre encoder was given in training


def audio_codes(base, samples: torch.Tensor, dev: torch.device) -> torch.Tensor:
    """A song latent [1, 64, frames] -> its semantic codes [1, ceil(frames / 5)], in the form the planner's codes
    have: the model's own tokenizer (AceStepAudioTokenizer.tokenize), with the residual FSQ step done in float32.
    In bfloat16 the code index - a sum of digits times up to 12800 - lands on the wrong code or past the end
    (bf16 holds 8 significant bits); ComfyUI's cover mode uses the quantized values, never the index, so it does
    not meet this."""
    tok = base.diffusion_model.tokenizer
    dtype = base.get_dtype_inference()
    x = samples[:1].to(device=dev, dtype=dtype).movedim(-1, -2)               # [1, frames, 64]
    pool = tok.pool_window_size
    if x.shape[1] % pool:
        x = torch.nn.functional.pad(x, (0, 0, 0, pool - x.shape[1] % pool))
    x = x.view(1, x.shape[1] // pool, pool, x.shape[-1])
    if not torch.isfinite(x).all():
        raise ValueError("SLQAceStepReference: the source's latent is not finite - the song could not be encoded")
    q = tok.quantizer
    with torch.no_grad():
        z = q.project_in(tok.attention_pooler(tok.audio_acoustic_proj(x))).float()
        if hasattr(q, "soft_clamp_input_value"):
            clamp = q.soft_clamp_input_value.to(device=z.device, dtype=torch.float32)
            z = (z / clamp).tanh() * clamp
        residual, found = z, []
        for layer, scale in zip(q.layers, q.scales):
            scale = scale.to(device=z.device, dtype=torch.float32)
            u = residual / scale
            if not isinstance(layer.project_in, torch.nn.Identity):
                u = layer.project_in(u.to(dtype)).float()
            codes = layer.bound(u)                       # float32 in, float32 out
            found.append(layer.codes_to_indices(codes))
            out = codes if isinstance(layer.project_out, torch.nn.Identity) else layer.project_out(codes.to(dtype)).float()
            residual = residual - out * scale
    if len(found) != 1:
        raise ValueError(f"SLQAceStepReference: expected one code a step, the tokenizer has {len(found)} quantizers")
    return found[0].long()


class AceStepReference(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SLQAceStepReference",
            display_name="ACE-Step 1.5 timbre and cover source (SLQ)",
            category="SLQ Hub/audio",
            description="Give an ACE-Step 1.5 model a timbre clip (heard by the timbre encoder only) and/or a "
                        "cover source (its semantic codes become the structure).",
            inputs=[
                IO.Model.Input("model"),
                IO.Latent.Input("timbre", optional=True,
                                tooltip="VAE-encoded clip whose sound to take on (the first 30 s are used). With "
                                        "no source the planner must write audio codes."),
                IO.Latent.Input("source", optional=True,
                                tooltip="VAE-encoded song to cover: its codes replace the planner's."),
            ],
            outputs=[IO.Model.Output()],
        )

    @classmethod
    def execute(cls, model, timbre=None, source=None) -> IO.NodeOutput:
        if timbre is None and source is None:
            return IO.NodeOutput(model)
        m = model.clone()
        previous = m.model_options.get("model_function_wrapper")
        timbre_samples = None if timbre is None else timbre["samples"][:1, :, :MAX_TIMBRE_FRAMES]
        source_samples = None if source is None else source["samples"][:1]
        cache: dict = {}

        def wrapper(apply_model, args):
            x, c = args["input"], dict(args["c"])
            base = getattr(apply_model, "__self__", None)
            base = m.model if base is None else base
            if not isinstance(base, comfy.model_base.ACEStep15):
                raise ValueError("SLQAceStepReference works with ACE-Step 1.5 models only")
            batch = x.shape[0]
            if source_samples is not None:
                codes = cache.get("codes")
                if codes is None or codes.device != x.device:
                    codes = cache["codes"] = audio_codes(base, source_samples, x.device)
                c["audio_codes"] = comfy.utils.repeat_to_batch_size(codes, batch)
                c["is_covers"] = True
            if timbre_samples is not None:
                if "audio_codes" not in c:
                    # without codes the model takes the reference as the source of the structure as well
                    raise ValueError("SLQAceStepReference: a timbre clip needs the planner's audio codes "
                                     "(generate_audio_codes) or a source")
                c["refer_audio"] = comfy.utils.repeat_to_batch_size(timbre_samples.to(x.device), batch)
            if previous is not None:
                return previous(apply_model, {**args, "c": c})
            return apply_model(x, args["timestep"], **c)

        m.set_model_unet_function_wrapper(wrapper)
        return IO.NodeOutput(m)


class AceStepEncode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SLQAceStepEncode",
            display_name="ACE-Step 1.5 text encode, no planner (SLQ)",
            category="SLQ Hub/audio",
            description="The caption, lyrics and metadata for ACE-Step 1.5 without the planner's audio codes (a "
                        "cover takes its codes from the source). bpm 0, an empty key or time signature: unknown.",
            inputs=[
                IO.Clip.Input("clip"),
                IO.String.Input("tags", multiline=True),
                IO.String.Input("lyrics", multiline=True),
                IO.String.Input("language", default="en"),
                IO.Float.Input("duration", default=120.0, min=1.0, max=2000.0, step=0.1),
                IO.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff),
                IO.Int.Input("bpm", default=0, min=0, max=300, tooltip="0: unknown"),
                IO.String.Input("keyscale", default="", tooltip="e.g. A minor; empty: unknown"),
                IO.String.Input("timesignature", default="", tooltip="2, 3, 4 or 6; empty: unknown"),
            ],
            outputs=[IO.Conditioning.Output()],
        )

    @classmethod
    def execute(cls, clip, tags, lyrics, language, duration, seed, bpm, keyscale, timesignature) -> IO.NodeOutput:
        kw = {"lyrics": lyrics, "duration": float(duration), "language": (language or "").strip() or "unknown",
              "seed": int(seed), "generate_audio_codes": False}
        if int(bpm) > 0:
            kw["bpm"] = int(bpm)
        if (keyscale or "").strip():
            kw["keyscale"] = keyscale.strip()
        ts = (timesignature or "").strip()
        if ts:
            if ts not in ("2", "3", "4", "6"):
                raise ValueError(f"SLQAceStepEncode: time signature {ts!r} is not 2, 3, 4 or 6")
            kw["timesignature"] = int(ts)
        tokens = clip.tokenize(tags, **kw)
        return IO.NodeOutput(clip.encode_from_tokens_scheduled(tokens))
