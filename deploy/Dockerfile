# DipDiver Ops UI — minimal Python image.
# Two-stage build: build deps in a heavy image, copy a slim runtime.

FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy only the metadata first so dep installs are cached across code edits.
COPY pyproject.toml README.md LICENSE ./
COPY dipdiver ./dipdiver
COPY scripts ./scripts

RUN pip install --upgrade pip wheel && \
    pip install --prefix=/install ".[ui,m2,m3]"

# ---------------------------------------------------------------------------

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DIPDIVER_UI_HOST=0.0.0.0 \
    DIPDIVER_UI_PORT=8765 \
    DIPDIVER_UI_DATA_ROOT=/var/lib/dipdiver

# tini gives us proper signal handling for graceful shutdowns.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /install /usr/local
COPY --from=build /build /app

# Persistent state lives outside the image so it survives container rebuilds.
RUN mkdir -p /var/lib/dipdiver/{scoreboard,db,logs,config,rendered,secrets}

EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["dipdiver-ui", "serve"]
