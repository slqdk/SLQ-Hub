# SLQ Hub - the web UI and the backend in one image. The frontend is built
# here and served by FastAPI; nothing else runs beside it.
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
COPY backend/app /app/app
COPY VERSION /app/VERSION
COPY --from=web /web/dist /app/static
RUN useradd -m -u 1000 hub && mkdir -p /data /models && chown 1000:1000 /data /models
USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
# --no-proxy-headers: the app reads X-Forwarded-For itself, and only from the
# proxy named in HUB_TRUSTED_PROXIES - uvicorn must not trust it from anyone.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", \
     "--no-proxy-headers", "--timeout-keep-alive", "75"]
