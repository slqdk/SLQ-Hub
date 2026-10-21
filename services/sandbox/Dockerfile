# The agent's sandbox: where bash and Python run, in /workspace (the Hub's
# project and chat folders). No Docker socket, no GPU, uid 1000, its own
# network that the VM's firewall keeps off the LAN.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates curl wget git jq ripgrep file tree less nano zip unzip xz-utils \
        build-essential pkg-config sqlite3 \
        python3 python3-pip python3-venv python3-dev \
        nodejs npm \
        imagemagick ghostscript poppler-utils ffmpeg \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-dan tesseract-ocr-deu \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# The default "ubuntu" user is uid 1000; the sandbox user takes its place.
RUN userdel -r ubuntu 2>/dev/null || true \
    && useradd -m -u 1000 -s /bin/bash sandbox

# A Python the agent can extend (pip install works as the sandbox user).
# uv brings uvx, which most Python MCP servers are started with.
RUN python3 -m venv /opt/py \
    && /opt/py/bin/pip install --upgrade pip wheel \
    && /opt/py/bin/pip install \
        numpy pandas scipy matplotlib pillow opencv-python-headless \
        openpyxl python-docx pymupdf pytesseract requests beautifulsoup4 lxml \
        pyyaml tabulate uv \
    && chown -R sandbox:sandbox /opt/py

ENV PATH=/opt/py/bin:$PATH

COPY execd.py /opt/execd/execd.py

USER sandbox
WORKDIR /workspace
EXPOSE 7000
ENTRYPOINT ["python3", "-u", "/opt/execd/execd.py"]
