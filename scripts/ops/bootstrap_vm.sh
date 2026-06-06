#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 22.04/24.04 VM for the DipDiver Ops UI.
#
# What it does:
#   1. apt update + install Docker engine + tailscale + git
#   2. Clone the DipDiver repo to /opt/dipdiver/DipDiver
#   3. Create the persistent state dir at /var/lib/dipdiver
#   4. Build the Docker image and start the UI via docker compose
#   5. Print next steps (tailscale up, drop secrets, etc.)
#
# After this script: the operator runs `sudo tailscale up`, authorises in
# browser, drops .env.m2 into /var/lib/dipdiver/secrets/, restarts the
# container, and is done.
#
# Run as root or with sudo. Idempotent — safe to re-run.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/thedumbstuff/DipDiver}"
INSTALL_DIR="${INSTALL_DIR:-/opt/dipdiver/DipDiver}"
DATA_ROOT="${DATA_ROOT:-/var/lib/dipdiver}"

log() { printf '\033[1;32m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap]\033[0m %s\n' "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
    warn "must run as root (e.g. via sudo)"
    exit 1
fi

log "1/5 apt update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg git lsb-release apt-transport-https

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    log "2a/5 installing Docker engine"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
else
    log "2a/5 Docker already installed"
fi

# --- Tailscale --------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
    log "2b/5 installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
else
    log "2b/5 Tailscale already installed"
fi

# --- Repo + state dir -------------------------------------------------------
log "3/5 cloning repo to $INSTALL_DIR"
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    git -C "$INSTALL_DIR" fetch --all --quiet
    git -C "$INSTALL_DIR" pull --ff-only --quiet
fi

log "creating data root at $DATA_ROOT"
mkdir -p "$DATA_ROOT"/{scoreboard,db,logs,config,rendered,secrets}
chmod 700 "$DATA_ROOT/secrets"
touch "$DATA_ROOT/secrets/.env.m2"
chmod 600 "$DATA_ROOT/secrets/.env.m2"

# --- Build + run ------------------------------------------------------------
log "4/5 building Docker image (this takes a few minutes the first time)"
cd "$INSTALL_DIR"
docker compose -f deploy/docker-compose.yml build

log "5/5 starting UI container"
docker compose -f deploy/docker-compose.yml up -d

log "done."
cat <<EOF

==============================================================================
  NEXT STEPS
==============================================================================
1. Bring the VM onto your tailnet:
     sudo tailscale up
   Open the URL it prints in your laptop browser and approve.

2. (Optional) Reserve a stable tailnet hostname:
     sudo tailscale set --hostname=dipdiver

3. Edit secrets:
     sudo nano $DATA_ROOT/secrets/.env.m2
   Required keys: ALPACA_API_KEY, ALPACA_API_SECRET, DEEPSEEK_API_KEY (or OPENAI_API_KEY).
   Optional: DIPDIVER_UI_TELEGRAM_BOT_TOKEN.

4. Restart the container so it picks up the new secrets:
     sudo docker compose -f $INSTALL_DIR/deploy/docker-compose.yml restart

5. Open the UI from any tailnet device:
     https://dipdiver.<your-tailnet>.ts.net
   (Or use Tailscale Serve / Funnel for nicer URLs.)

For logs:    sudo docker compose -f $INSTALL_DIR/deploy/docker-compose.yml logs -f
For shell:   sudo docker exec -it dipdiver-ui bash
==============================================================================
EOF
