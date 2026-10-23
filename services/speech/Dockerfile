# Text to speech for SLQ Hub: Chatterbox models (Resemble AI's multilingual model and the Danish Røst models
# from the CoRal project) on the CPU. No GPU on purpose - speech runs while the chat model stays on the card.
# Internal only (the core network); the Hub sends the model files' paths and the text, and gets the audio back.
FROM python:3.12-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
# Resemble AI's chatterbox at a fixed commit (2026-07-21): Multilingual V3 and Turbo, without the alignment
# analyser of the 0.1.7 release, which fails on texts of a word or two.
ARG CHATTERBOX=https://github.com/resemble-ai/chatterbox/archive/5de7a54aa4e5e2baadb0182dde554908b48b85c2.tar.gz

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    TQDM_DISABLE=1 \
    MALLOC_MMAP_THRESHOLD_=1048576

RUN pip install "torch==2.11.0" "torchaudio==2.11.0" --index-url "${TORCH_INDEX}"
COPY requirements.txt /opt/speech/requirements.txt
RUN pip install -r /opt/speech/requirements.txt \
    && pip install --no-deps "s3tokenizer==0.3.0" "chatterbox-tts @ ${CHATTERBOX}" \
    && python -W ignore -c "import torch, av, perth, chatterbox.mtl_tts, chatterbox.tts_turbo; print('torch', torch.__version__)"

COPY server.py /opt/speech/server.py
RUN useradd -m -u 1000 speech
USER 1000:1000
WORKDIR /opt/speech
EXPOSE 7100
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7100/health', timeout=4)" || exit 1
ENTRYPOINT ["python", "-W", "ignore", "-u", "/opt/speech/server.py"]
