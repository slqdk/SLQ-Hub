#!/usr/bin/env bash
# Starts ComfyUI headless. Extra flags come from COMFY_ARGS in .env, e.g.
#   COMFY_ARGS=--reserve-vram 1.5 --disable-pinned-memory
set -euo pipefail
cd /opt/ComfyUI
for d in diffusion_models text_encoders vae loras checkpoints upscale_models model_patches; do
    mkdir -p "models/$d"
done
# --disable-metadata: no prompt and workflow inside the pictures and sound files the Hub hands out (in an MP3 the
# whole graph as a tag, which ffmpeg also copies into anything mixed from it).
# shellcheck disable=SC2086  # COMFY_ARGS is a list of flags on purpose
exec python main.py --listen 0.0.0.0 --port 8188 --log-stdout --disable-partner-nodes \
    --disable-auto-launch --disable-metadata ${COMFY_ARGS:-}
