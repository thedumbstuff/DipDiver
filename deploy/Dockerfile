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

# brain-lite extra (qlib/lightgbm/yfinance, NO torch) enables in-container
# training for the /config "Add market" onboarding and m1_retrain — for
# LightGBM configs, which are the better-performing M1 baseline anyway. This
# keeps the image small (hundreds of MB vs several GB with torch). LSTM
# configs fail onboarding with a clean "brain dependencies missing: torch"
# message; swap to ".[ui,m2,m3,brain]" below if you need LSTM in-container.
RUN pip install --upgrade pip wheel && \
    pip install --prefix=/install ".[ui,m2,m3,brain-lite]"

# ---------------------------------------------------------------------------

FROM python:3.12-slim AS runtime

# DIPDIVER_REPO_ROOT must point at the source tree copied below (/app). The
# package is pip-installed under site-packages, so repo_root() can't discover
# pyproject.toml by walking up from the module — without this the app crashes
# at import. /app holds pyproject.toml + scripts/ + dipdiver/.../configs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DIPDIVER_UI_HOST=0.0.0.0 \
    DIPDIVER_UI_PORT=8765 \
    DIPDIVER_UI_DATA_ROOT=/var/lib/dipdiver \
    DIPDIVER_REPO_ROOT=/app

# tini: proper signal handling for graceful shutdowns.
# libgomp1: OpenMP runtime (libgomp.so.1) that LightGBM's native lib links
#   against. Without it `import lightgbm` raises OSError on slim images and
#   the "Add market" onboarding reports lightgbm as missing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /install /usr/local
COPY --from=build /build /app

# Persistent state lives outside the image so it survives container rebuilds.
RUN mkdir -p /var/lib/dipdiver/{scoreboard,db,logs,config,rendered,secrets,data}

EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["dipdiver-ui", "serve"]
