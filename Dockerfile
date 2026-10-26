# Правила по технике (task/rules.md): multi-stage, без root, порт 8080, healthcheck, версии закреплены.
FROM node:22.23.4-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12.15-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

FROM python:3.12.15-slim-bookworm
RUN useradd --create-home --uid 10001 app
WORKDIR /app/backend
COPY --from=build --chown=app:app /app/backend /app/backend
COPY --from=web --chown=app:app /web/dist /app/web/dist
ENV PATH=/app/backend/.venv/bin:$PATH PYTHONUNBUFFERED=1 APP_PORT=8080
USER app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${APP_PORT}"]
