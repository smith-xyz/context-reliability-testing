# CRT container image.
# Swap the base image for your project's toolchain (e.g. golang:latest, rust:latest).

FROM node:latest
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 git \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN cd /app && uv sync --no-dev --frozen

RUN npm install -g @anthropic-ai/claude-code

ENTRYPOINT ["uv", "run", "--project", "/app", "crt"]
