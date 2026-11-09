# syntax=docker/dockerfile:1

# ---- Stylesheet build -------------------------------------------------------
FROM node:22-slim AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./frontend
COPY batchlight/templates ./batchlight/templates
RUN npx tailwindcss -i frontend/app.css -o app.css --minify

# ---- Application ------------------------------------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN useradd --create-home --uid 10001 batchlight
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY batchlight ./batchlight
COPY --from=css /build/app.css ./batchlight/static/app.css
COPY deploy/entrypoint.sh /usr/local/bin/batchlight-entrypoint
USER batchlight
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/healthz', timeout=3)"
ENTRYPOINT ["batchlight-entrypoint"]
CMD ["web"]
