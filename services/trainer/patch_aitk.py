"""Patches the pinned ai-toolkit when the trainer image is built (0.63.0): FLUX.2 klein's text encoder comes from the
config's model.te_name_or_path when it names one - SLQ Hub's "FLUX.2 klein 9B uncensored" trains with
ponpoke/flux2-klein-9b-uncensored-text-encoder - and otherwise, as before, from the model class's own repository
(Qwen/Qwen3-4B or Qwen/Qwen3-8B). ai-toolkit takes it from a class attribute only (Flux2KleinModel.load_te in
extensions_built_in/diffusion_models/flux2/flux2_klein_model.py); te_name_or_path is a ModelConfig key it already has,
used by other models. The log says which encoder was loaded ("Loading Qwen3 from ...").

The block it replaces must be there exactly as it was at AI_TOOLKIT_REF b69b983. When it is not (another
AI_TOOLKIT_REF in .env), the file is left alone with a warning in the build's log, and the image is built all the
same: the trainer's /health then says "klein_te": false, and the Hub refuses only that base, saying why - a failed
build would have stopped the whole update. A newer ai-toolkit is read again before the patch is changed. Running it
twice changes nothing.

    python patch_aitk.py /opt/ai-toolkit
"""
from __future__ import annotations

import sys
from pathlib import Path

FILE = "extensions_built_in/diffusion_models/flux2/flux2_klein_model.py"
MARK = "SLQ Hub (patch_aitk.py)"

OLD = '''    def load_te(self):
        if self.flux2_klein_te_path is None:
            raise ValueError("flux2_klein_te_path must be set for Flux2KleinModel")
        dtype = self.torch_dtype
        self.print_and_status_update("Loading Qwen3")

        # load + quantize + offload + placement, all driven by model_config
        text_encoder = Qwen3TextEncoder.load(
            self.flux2_klein_te_path, subfolder="", **self.component_load_kwargs("te")
        )
        flush()

        tokenizer = Qwen2Tokenizer.from_pretrained(self.flux2_klein_te_path)
        return text_encoder, tokenizer
'''

NEW = f'''    def load_te(self):
        if self.flux2_klein_te_path is None:
            raise ValueError("flux2_klein_te_path must be set for Flux2KleinModel")
        # {MARK}: the config's te_name_or_path first, the class's own repository otherwise
        te_path = getattr(self.model_config, "te_name_or_path", None) or self.flux2_klein_te_path
        dtype = self.torch_dtype
        self.print_and_status_update(f"Loading Qwen3 from {{te_path}}")

        # load + quantize + offload + placement, all driven by model_config
        text_encoder = Qwen3TextEncoder.load(
            te_path, subfolder="", **self.component_load_kwargs("te")
        )
        flush()

        tokenizer = Qwen2Tokenizer.from_pretrained(te_path)
        return text_encoder, tokenizer
'''


def patch(toolkit: Path) -> str:
    """"patched", "already" (it had the patch), or "skipped": this ai-toolkit's load_te is not the one the patch was
    written for, and the file is left as it is."""
    path = toolkit / FILE
    try:
        text = path.read_text()
    except OSError:
        return "skipped"
    if MARK in text:
        return "already"
    if text.count(OLD) != 1:
        return "skipped"
    path.write_text(text.replace(OLD, NEW))
    return "patched"


def patched(toolkit: Path) -> bool:
    """Whether this ai-toolkit takes klein's text encoder from te_name_or_path (the trainer's /health)."""
    try:
        return MARK in (toolkit / FILE).read_text()
    except OSError:
        return False


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/ai-toolkit")
    done = patch(root)
    if done == "skipped":
        print(f"WARNING patch_aitk.py: {root / FILE} is not as at the ai-toolkit commit the patch was written for - "
              "left as it is. FLUX.2 klein 9B uncensored cannot be trained with this image (the trainer's /health "
              "says klein_te false); the other bases can.", file=sys.stderr)
    else:
        print(done, root / FILE)
        compile((root / FILE).read_text(), str(root / FILE), "exec")      # a patched file that does not parse: stop
