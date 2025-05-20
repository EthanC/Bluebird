FROM python:3.13-slim-bookworm

WORKDIR /bluebird
COPY . .

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
RUN uv sync --frozen --no-dev

CMD [ "uv", "run", "bluebird.py" ]
