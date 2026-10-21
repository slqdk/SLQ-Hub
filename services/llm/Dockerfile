# llama.cpp (ROCm) plus a small supervisor that starts and stops one
# llama-server per model when the Hub tells it to. The image is upstream's
# prebuilt ROCm server image (gfx1100 included), pinned by build tag in .env.
ARG LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:server-rocm-b11783
FROM ${LLAMA_IMAGE}

# Upstream sets LLAMA_ARG_HOST/LLAMA_ARG_PORT for its single-server
# entrypoint; the supervisor drops every LLAMA_ARG_* from a child's
# environment and passes --host/--port itself.
RUN (command -v python3 >/dev/null 2>&1 || \
        (apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*)) \
    && mkdir -p /logs

COPY supervisor.py /opt/supervisor/supervisor.py

EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=4)" || exit 1
ENTRYPOINT ["python3", "-u", "/opt/supervisor/supervisor.py"]
