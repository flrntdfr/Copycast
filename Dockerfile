# syntax=docker/dockerfile:1.7
#
# Copycast image: one image, two processes (`copycast api`, `copycast worker`).
#
# Three stages:
#   web      builds the React bundle with pnpm
#   lock     splits uv.lock into base.txt (everything but yt-dlp) and engine.txt (yt-dlp only)
#   runtime  python:3.13-slim + ffmpeg; installs base.txt, then engine.txt, then the project
#
# The engine layer is installed on its own so that an engine bump (a lock change touching
# only yt-dlp) invalidates a single small layer instead of the whole dependency set.

ARG APP_VERSION=1.2.0
ARG ENGINE_VERSION=unknown

# ---- web -------------------------------------------------------------------------------
FROM node:22.23.2-alpine3.24@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS web
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml web/openapi.json ./
RUN corepack enable && corepack prepare pnpm@11.25.0 --activate \
    && pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm run build

# ---- lock ------------------------------------------------------------------------------
FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS lock
COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /usr/local/bin/uv
WORKDIR /lock
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --no-emit-package yt-dlp -o base.txt \
    && uv export --frozen --no-dev --no-emit-project --only-emit-package yt-dlp -o engine.txt

# ---- deno ------------------------------------------------------------------------------
# yt-dlp needs a JavaScript runtime for YouTube (signature and "n" challenges); deno is the
# one it enables by default. Pinned by version and checksum for both architectures.
FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS deno
ARG TARGETARCH
ARG DENO_VERSION=2.9.6
ARG DENO_SHA256_AMD64=394f07f4da2bebe6ce6f1e7ce0fa16429b29b08c35e3fac3fe25972676dff4b2
ARG DENO_SHA256_ARM64=9a46afc6c392c7cd2ff71a31558935545b46408d0e87f7a86908c712721c046e
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) triple=x86_64-unknown-linux-gnu; sum="$DENO_SHA256_AMD64" ;; \
      arm64) triple=aarch64-unknown-linux-gnu; sum="$DENO_SHA256_ARM64" ;; \
      *) echo "unsupported TARGETARCH $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${triple}.zip', '/tmp/deno.zip')"; \
    echo "$sum  /tmp/deno.zip" | sha256sum -c -; \
    python3 -c "import zipfile; zipfile.ZipFile('/tmp/deno.zip').extract('deno', '/tmp')"; \
    install -m 0755 /tmp/deno /usr/local/bin/deno; \
    /usr/local/bin/deno --version

# ---- runtime ---------------------------------------------------------------------------
FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS runtime
ARG APP_VERSION
ARG ENGINE_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deno /usr/local/bin/deno /usr/local/bin/deno

RUN useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin copycast \
    && mkdir -p /app /data /config \
    && chown copycast:copycast /data

COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /usr/local/bin/uv

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN uv venv /opt/venv

# 1. Everything except the engine (changes rarely).
COPY --from=lock /lock/base.txt /tmp/base.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv --require-hashes -r /tmp/base.txt

# 2. The engine alone: the only layer an engine bump invalidates.
COPY --from=lock /lock/engine.txt /tmp/engine.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv --no-deps --require-hashes -r /tmp/engine.txt

# 3. The application itself.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv --no-deps . \
    && rm -rf /tmp/base.txt /tmp/engine.txt

# 4. The web bundle, served by the api process when COPYCAST_WEB_DIR is set.
COPY --from=web /web/dist /app/web

ENV COPYCAST_CONFIG=/config/copycast.toml \
    COPYCAST_WEB_DIR=/app/web \
    COPYCAST_LOG_FORMAT=json \
    COPYCAST__DATA_DIR=/data

LABEL org.opencontainers.image.title="Copycast" \
      org.opencontainers.image.description="Self-hosted podcast mirroring and archiving service" \
      org.opencontainers.image.source="https://github.com/flrntdfr/copycast" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}" \
      xyz.dufour.copycast.engine="yt-dlp" \
      xyz.dufour.copycast.engine-version="${ENGINE_VERSION}"

EXPOSE 8080 8081
USER copycast
ENTRYPOINT ["tini", "--", "copycast"]
CMD ["api"]
