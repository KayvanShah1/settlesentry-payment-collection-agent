# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Copy workspace and package metadata first for better dependency-layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY settlesentry/pyproject.toml settlesentry/README.md ./settlesentry/

# Copy package source.
COPY settlesentry/src ./settlesentry/src

# Install runtime dependencies and the installable package.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package settlesentry


FROM python:3.12-slim-trixie AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_CONSOLE_ENABLED=false \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/var /app/logs \
    && chown -R app:app /app

COPY --from=builder /opt/venv /opt/venv

USER app

ENTRYPOINT ["settlesentry"]

# Default to deterministic mode so the image runs without an OpenRouter key.
CMD ["chat", "--mode", "deterministic-workflow"]