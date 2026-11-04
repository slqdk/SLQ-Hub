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
# 0.77.0: through logpump.py, which passes ComfyUI's output on to docker logs and also to /logs/comfyui.log - the log
# the Hub shows in Settings -> System (on the VM: /srv/slq-hub/data/logs/comfyui).
# shellcheck disable=SC2086  # COMFY_ARGS is a list of flags on purpose
exec python /opt/logpump.py /logs/comfyui.log -- python main.py --listen 0.0.0.0 --port 8188 --log-stdout \
    --disable-partner-nodes --disable-auto-launch --disable-metadata ${COMFY_ARGS:-}
