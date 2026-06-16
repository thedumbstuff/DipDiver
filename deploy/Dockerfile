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
# `packaging` is also declared in the brain extras, but we install it
# explicitly here too: qlib imports it undeclared (qlib/utils/__init__.py) and
# the transitive chain that normally carries it (matplotlib/mlflow) doesn't
# survive the slim --prefix install reliably. Pinning it on this line also
# guarantees the fix lands even if pyproject.toml is out of sync on the host.
RUN pip install --upgrade pip wheel && \
    pip install --prefix=/install ".[ui,m2,m3,brain-lite]" "packaging>=21"

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

# qlib imports `packaging` but never declares it. The build stage's
# `pip install --prefix=/install` SKIPS it because the build base already
# provides a top-level `packaging` (pulled in by the pip/wheel upgrade), so it
# never lands in /install and never reaches this runtime image. Install it
# directly here — pure-python wheel, no build tools needed; --ignore-installed
# so it can't be no-op'd by a partial copy. This is the reliable home for it.
RUN pip install --no-cache-dir --ignore-installed "packaging>=21"

# Persistent state lives outside the image so it survives container rebuilds.
RUN mkdir -p /var/lib/dipdiver/{scoreboard,db,logs,config,rendered,secrets,data}

# Make the source tree at /app authoritative for `import dipdiver` / `import
# scripts`. Without this the app imports dipdiver from the pip-installed wheel
# in site-packages, so a stale/incomplete wheel (or a partially-synced source)
# silently breaks submodule imports (e.g. dipdiver.adapters.lean) at runtime.
# Pinning PYTHONPATH=/app means runtime uses exactly the COPYied source — the
# same tree the smoke test below validates — so build and runtime can't diverge.
ENV PYTHONPATH=/app

# Build-time smoke test: import the FULL chain the "Add market" onboarding
# exercises — the qlib training stack AND our own training/export modules — so
# a missing transitive dep (e.g. packaging), an unloadable native lib (lightgbm
# without libgomp1), OR an incomplete source tree (e.g. a missing
# dipdiver.adapters.lean) fails the BUILD here instead of at click-time. qlib
# prints harmless "CatBoostModel/XGBModel skipped" warnings for optional models.
RUN python -c "import packaging.version, qlib; \
from qlib.utils import init_instance_by_config; \
from qlib.workflow import R; \
from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord; \
from qlib.contrib.data.handler import Alpha158; \
from qlib.contrib.model.gbdt import LGBModel; \
from qlib.contrib.strategy import TopkDropoutStrategy; \
import lightgbm, yfinance, pandas, numpy; \
import dipdiver.adapters.lean, dipdiver.adapters.alpaca, dipdiver.harness.picks; \
import scripts.m3_export_signals; \
print('brain + dipdiver import chain OK')"

# qlib tracks training via mlflow's file store; newer mlflow raises on it
# unless this opt-out is set. Baked in here (not just docker-compose.yml) so
# the app runs regardless of how it's launched. Placed late to keep the heavy
# layers above cached. docker-compose.yml additionally persists /app/mlruns.
ENV MLFLOW_ALLOW_FILE_STORE=true

EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["dipdiver-ui", "serve"]
