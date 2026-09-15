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
CMD ["python", "bluebird.py"]
