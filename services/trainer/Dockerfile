# LoRA training for SLQ Hub (0.50.0) on the RX 7900 XTX (gfx1100): Ostris' ai-toolkit, and a small service
# (traind.py) the Hub sends jobs to. Optional: built and started only with the compose profile "training"
# (COMPOSE_PROFILES=training in .env). Offline, like the other model containers: the Hub fetches the base model's
# files into Hugging Face's cache in models/hf.
#
# PyTorch as ai-toolkit's own installer sets it up for AMD (manager/spec.py: torch 2.13.0 / torchvision 0.28.0 /
# torchaudio 2.11.0 from the rocm7.1 index - "experimental and largely untested" there too), and, as that installer
# does, held in place by a constraint while ai-toolkit's requirements go in: a package that wants another torch fails
# the build instead of quietly swapping in PyPI's CUDA build. The config the Hub writes needs no quantisation and no
# 8-bit optimiser - the least sure parts on AMD. The first real run is the test.
FROM ubuntu:24.04
# (PYTORCH_HIP_ALLOC_CONF below is NOT passed on to ai-toolkit - traind.py's toolkit_env says why. It stays here so
# the layers built already are reused; it only affects the image's own torch check.)

ARG TORCH_INDEX=https://download.pytorch.org/whl/rocm7.1
ARG TORCH_VERSION=2.13.0
ARG TORCHVISION_VERSION=0.28.0
ARG TORCHAUDIO_VERSION=2.11.0
# The ai-toolkit commit the Hub's training config was checked against (2026-10-27). "main" for the newest.
ARG AI_TOOLKIT_REF=b69b98314557f7c8b08e11bbb14e48d959e6ab26

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    WANDB_MODE=disabled \
    AI_TOOLKIT=/opt/ai-toolkit \
    TRAINER_ROOT=/training \
    PYTORCH_HIP_ALLOC_CONF=expandable_segments:True

# ffmpeg: the shared libraries torchcodec (one of ai-toolkit's requirements) loads
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev build-essential git ca-certificates curl ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

RUN pip install --upgrade pip wheel setuptools \
    && pip install "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}" --index-url "${TORCH_INDEX}" \
    && pip freeze | grep -iE '^(torch|torchvision|torchaudio)==' > /opt/torch-constraints.txt \
    && cat /opt/torch-constraints.txt

RUN git init -q /opt/ai-toolkit \
    && git -C /opt/ai-toolkit remote add origin https://github.com/ostris/ai-toolkit \
    && git -C /opt/ai-toolkit fetch -q --depth 1 origin "${AI_TOOLKIT_REF}" \
    && git -C /opt/ai-toolkit checkout -q FETCH_HEAD \
    && git -C /opt/ai-toolkit rev-parse HEAD > /opt/ai-toolkit.ref

RUN cd /opt/ai-toolkit \
    && pip install -r requirements.txt -c /opt/torch-constraints.txt \
    && python -c "import torch; print('torch', torch.__version__, 'hip', torch.version.hip)"

COPY traind.py /opt/traind/traind.py
# As uid 1000, like the Hub: what ai-toolkit writes into a run's folder (checkpoints, samples, its latent cache) the
# Hub can read and delete. ai-toolkit's own folder is its: it may write into it.
RUN mkdir -p /models/hf /training && chown -R 1000:1000 /opt/ai-toolkit /models/hf /training
USER 1000:1000
WORKDIR /opt/ai-toolkit
EXPOSE 7300
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD curl -fsS -o /dev/null http://127.0.0.1:7300/health || exit 1
CMD ["python", "/opt/traind/traind.py"]
