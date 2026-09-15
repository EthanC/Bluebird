# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:alpine

FROM ${PYTHON_IMAGE} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /bluebird

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM ${PYTHON_IMAGE}

ENV PATH="/bluebird/.venv/bin:$PATH" \
    BLUEBIRD_HEALTHCHECK_FILE=/tmp/bluebird-health \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/bluebird/.venv

RUN addgroup -S -g 1000 bluebird \
    && adduser -S -D -H -u 1000 -G bluebird bluebird \
    && mkdir -p /bluebird/data \
    && chown bluebird:bluebird /bluebird/data

WORKDIR /bluebird

COPY --from=builder /bluebird/.venv .venv
COPY bluebird.py ./
COPY core ./core
COPY docker-entrypoint.py /usr/local/bin/docker-entrypoint.py

USER 0

ENTRYPOINT ["python", "/usr/local/bin/docker-entrypoint.py"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, time; path = os.environ['BLUEBIRD_HEALTHCHECK_FILE']; raise SystemExit(not (os.path.isfile(path) and time.time() - os.path.getmtime(path) < 10))"]
CMD ["python", "bluebird.py"]
