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
