# Headless ComfyUI for SLQ Hub on an RX 7900 XTX (gfx1100).
# PyTorch's ROCm wheels bring their own ROCm runtime, so a plain Ubuntu
# base is enough; the host only needs the amdgpu driver (/dev/kfd, /dev/dri).
# ComfyUI turns on its dynamic VRAM manager for AMD only with ROCm >= 7.14,
# hence the rocm7.14 index.
FROM ubuntu:24.04

ARG COMFYUI_REF=v0.42.3
ARG TORCH_INDEX=https://download.pytorch.org/whl/rocm7.14
ARG TORCH_VERSION=2.14.1
ARG TORCHVISION_VERSION=0.29.1
# The code SLQ Hub's own nodes (nodes/slq_audio) load: Seed-VC (voice conversion, GPL-3.0) and ZFTurbo's
# Music-Source-Separation-Training (its Mel-Band RoFormer, MIT), each at a commit the nodes were written for.
ARG SEEDVC_REF=51383efd921027683c89e5348211d93ff12ac2a8
ARG MSST_REF=6fdad13686bb7e84d029ec66f9dbe76e65c93ebf

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev build-essential git ca-certificates curl \
        libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

RUN pip install --upgrade pip wheel setuptools \
    && pip install "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
        --index-url "${TORCH_INDEX}"

RUN git clone --depth 1 --branch "${COMFYUI_REF}" https://github.com/comfyanonymous/ComfyUI /opt/ComfyUI \
    || (git clone https://github.com/comfyanonymous/ComfyUI /opt/ComfyUI \
        && git -C /opt/ComfyUI checkout "${COMFYUI_REF}")

# ComfyUI's requirements name torch/torchvision/torchaudio without the ROCm
# build; installing them from PyPI would replace the ROCm wheels.
RUN cd /opt/ComfyUI \
    && grep -viE '^(torch|torchvision|torchaudio)([<>=~! ;]|$)' requirements.txt > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && python -c "import torch; print('torch', torch.__version__, 'hip', torch.version.hip)"

# What the nodes import beyond ComfyUI's own requirements, at the versions they were tested with (librosa: mel
# filters for both; munch and matplotlib: Seed-VC's modules; beartype and rotary-embedding-torch: the RoFormer).
RUN pip install "librosa==1.0.0" "munch==4.0.0" "matplotlib==3.11.2" "beartype==0.23.1" \
        "rotary-embedding-torch==0.9.1"

# Fetched by commit, without history; Seed-VC's examples are not needed (its speaker encoder weights,
# campplus_cn_common.bin, are, and stay).
RUN git init -q /opt/seed-vc \
    && git -C /opt/seed-vc fetch -q --depth 1 https://github.com/Plachtaa/seed-vc "${SEEDVC_REF}" \
    && git -C /opt/seed-vc checkout -q FETCH_HEAD \
    && rm -rf /opt/seed-vc/.git /opt/seed-vc/examples /opt/seed-vc/assets \
    && test -f /opt/seed-vc/campplus_cn_common.bin \
    && git init -q /opt/msst \
    && git -C /opt/msst fetch -q --depth 1 https://github.com/ZFTurbo/Music-Source-Separation-Training "${MSST_REF}" \
    && git -C /opt/msst checkout -q FETCH_HEAD \
    && rm -rf /opt/msst/.git \
    && test -f /opt/msst/models/bs_roformer/mel_band_roformer.py

COPY nodes/slq_audio /opt/ComfyUI/custom_nodes/slq_audio

# The image's default "ubuntu" user is uid 1000 - the same uid owns the
# mounted folders on the VM.
RUN mkdir -p /opt/ComfyUI/models /opt/ComfyUI/input /opt/ComfyUI/output /opt/ComfyUI/user \
    && chown -R 1000:1000 /opt/ComfyUI

COPY start.sh /opt/start.sh
RUN chmod 755 /opt/start.sh

USER 1000:1000
WORKDIR /opt/ComfyUI
EXPOSE 8188
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD curl -fsS -o /dev/null http://127.0.0.1:8188/system_stats || exit 1
ENTRYPOINT ["/opt/start.sh"]
