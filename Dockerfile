# --- Etapa 1: build del frontend ---
FROM node:24-slim AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build                                   # => /web/dist

# --- Etapa 2: runtime Python ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt && useradd --uid 10001 --no-create-home app
COPY backend/epp_api/ epp_api/
COPY --from=web /web/dist/ static/
USER app
CMD exec uvicorn epp_api.main:app --host 0.0.0.0 --port ${PORT:-8080} \
    --workers 1 --no-access-log --ws-per-message-deflate false --ws-max-size 1048576 \
    --proxy-headers --forwarded-allow-ips="*" --timeout-keep-alive 75
