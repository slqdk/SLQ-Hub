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

# ffmpeg and ffprobe without the banner (version and 2 KB of build flags) on every call, which buried what went
# wrong under text the model had to read every time.
RUN for t in ffmpeg ffprobe; do \
        printf '#!/bin/sh\nexec /usr/bin/%s -hide_banner "$@"\n' "$t" > "/usr/local/bin/$t" \
        && chmod 755 "/usr/local/bin/$t"; \
    done

# C# with the .NET 10 SDK (Ubuntu's own build, from the archive; without the recommended AOT compiler), in a
# layer of its own so adding it did not rebuild the ones above. No telemetry, banner, dev certificate or workload
# update checks; no build servers left running after a command (MSBuild nodes, the MSBuild server, the C# compiler
# server) - they would hold memory in the sandbox between commands. Windows Forms and WPF projects build here
# with <EnableWindowsTargeting>true</EnableWindowsTargeting> in the project (not set here: it makes every restore
# fetch the Windows pack from NuGet); they cannot run here. Each command's build output goes to .hub/dotnet in its
# working folder (ArtifactsPath, set by the Hub), not into bin/ and obj/ beside the project, where sync and the
# files card would pick it up.
RUN apt-get update && apt-get install -y --no-install-recommends dotnet-sdk-10.0 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_GENERATE_ASPNET_CERTIFICATE=false \
    DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=1 \
    MSBUILDDISABLENODEREUSE=1 \
    DOTNET_CLI_USE_MSBUILD_SERVER=0 \
    UseSharedCompilation=false \
    NUGET_XMLDOC_MODE=skip

COPY execd.py confine.py /opt/execd/

USER sandbox
WORKDIR /workspace
EXPOSE 7000
ENTRYPOINT ["python3", "-u", "/opt/execd/execd.py"]
