# syntax=docker/dockerfile:1

# ==============================================================================
# Stage 1: frontend-dev — Vite dev server + frontend lint/typecheck/unit runner
# ==============================================================================
FROM node:22-bookworm-slim AS frontend-dev

RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

EXPOSE 5173
CMD ["npm", "run", "dev"]

# ==============================================================================
# Stage 2: frontend-build — Compile static SPA assets for production runtime
# ==============================================================================
FROM frontend-dev AS frontend-build

RUN npm run build

# ==============================================================================
# Stage 3: python-base — Patched Python 3.13 + FFmpeg + uv + non-root appuser
# ==============================================================================
FROM python:3.13-slim-bookworm AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/home/appuser/.venv \
    PATH="/home/appuser/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade pip

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/appuser --shell /bin/bash appuser \
    && mkdir -p \
        /workspace \
        /data/db \
        /data/photos \
        /data/previews \
        /data/cache/renders \
    && chown -R appuser:appuser /workspace /data /home/appuser

# ==============================================================================
# Stage 4: backend-dev — FastAPI dev server + ruff / mypy / pytest / alembic
# ==============================================================================
FROM python-base AS backend-dev

WORKDIR /workspace

COPY --chown=appuser:appuser pyproject.toml uv.lock README.md alembic.ini ./
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser tests/ ./tests/

USER appuser
RUN uv sync --frozen

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/workspace/backend"]

# ==============================================================================
# Stage 5: runtime — Non-root single-container production image (API + SPA)
# ==============================================================================
FROM python-base AS runtime

ARG APP_VERSION=0.1.0-alpha
ENV APP_VERSION=${APP_VERSION} \
    UV_NO_DEV=1

WORKDIR /workspace

COPY --chown=appuser:appuser pyproject.toml uv.lock README.md alembic.ini ./
COPY --chown=appuser:appuser backend/ ./backend/
COPY --from=frontend-build --chown=appuser:appuser /workspace/frontend/dist /workspace/frontend/dist

USER appuser
RUN uv sync --frozen --no-dev

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
