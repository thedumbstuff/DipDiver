# Deploy

Two ways to run the DipDiver Ops UI.

## Local dev (Windows / macOS / Linux)

```bash
# From repo root, with the project venv active
pip install -e ".[ui,m2,m3]"
dipdiver-ui serve
# → http://127.0.0.1:8765
```

That's it. The scheduler boots automatically (using `DIPDIVER_UI_DATA_ROOT` if
set, else the repo root). For dev with hot-reload:

```bash
dipdiver-ui serve --reload
```

## Home laptop (Linux + Docker, LAN access)

Target: any spare x86_64 laptop running a Linux distro with Docker + the
compose plugin already installed. Same container as the VM deploy, but bound
to the LAN instead of a tailnet.

```bash
# 1. Clone the repo
sudo mkdir -p /opt/dipdiver && sudo chown "$USER" /opt/dipdiver
git clone <this-repo> /opt/dipdiver/DipDiver
cd /opt/dipdiver/DipDiver

# 2. Create the persistent state dirs (survive container rebuilds)
sudo mkdir -p /var/lib/dipdiver/{scoreboard,db,logs,config,rendered,secrets,data}

# 3. Drop API keys
sudo cp .env.m2.example /var/lib/dipdiver/secrets/.env.m2
sudo nano /var/lib/dipdiver/secrets/.env.m2
# Required: ALPACA_API_KEY, ALPACA_API_SECRET, DEEPSEEK_API_KEY (or OPENAI_API_KEY)
# Optional: DIPDIVER_UI_TELEGRAM_BOT_TOKEN, DIPDIVER_UI_TELEGRAM_CHAT_ID

# 4. Build + start, bound to all interfaces so other LAN devices can reach it
DIPDIVER_UI_HOST=0.0.0.0 docker compose -f deploy/docker-compose.yml up -d --build

# 5. Open the UI from any device on your network
ip -4 addr show | grep inet        # find the laptop's LAN IP
# → http://<laptop-ip>:8765
```

> **Security note:** binding to `0.0.0.0` exposes the UI (and its kill-switch)
> to everyone on your LAN with no authentication. Fine on a trusted home
> network; do NOT port-forward it to the internet. If you want remote access,
> install Tailscale on the laptop and follow the VM section below instead
> (keep the default `127.0.0.1` bind).

Laptop-specific hardening — the box must stay awake and come back from power
cuts on its own:

```bash
# Don't suspend when the lid closes
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind

# Disable suspend/hibernate entirely
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Start Docker (and therefore the container, restart: unless-stopped) on boot
sudo systemctl enable docker
```

Optionally set "power on after power failure / restore AC power" in the BIOS
so the scheduler survives outages unattended. The nightly cron jobs fire in
UTC — the laptop's local timezone doesn't matter, but its clock must be right
(`timedatectl set-ntp true`).

Updating, state layout, and backups are identical to the VM deploy — see the
sections below (skip the Tailscale steps).

### Enabling more markets

The image ships with the `brain` extra (qlib + lightgbm + torch), so the
first build downloads several GB — be patient. In exchange, onboarding a new
market is one click: **/config → Add market** picks a universe + model, then
fetches data, trains and gate-checks the M1 baseline, exports signals, and
enables the nightly strategy — all as a background job with live progress.
Fetched data and signals persist under `/var/lib/dipdiver/data`.

## Production (self-hosted VM)

Target: Hetzner CX22 / DigitalOcean basic / any small Ubuntu 22.04+ VM.

```bash
# On the VM, as root:
curl -sSL https://raw.githubusercontent.com/thedumbstuff/DipDiver/main/scripts/ops/bootstrap_vm.sh | sudo bash
```

The script installs Docker + Tailscale, clones the repo to `/opt/dipdiver/DipDiver`,
builds the image, and starts the container. After it finishes:

```bash
# 1. Bring the VM onto your tailnet
sudo tailscale up    # follow the URL it prints

# 2. (Optional) name the node
sudo tailscale set --hostname=dipdiver

# 3. Drop API keys
sudo nano /var/lib/dipdiver/secrets/.env.m2
# Required: ALPACA_API_KEY, ALPACA_API_SECRET, DEEPSEEK_API_KEY (or OPENAI_API_KEY)
# Optional: DIPDIVER_UI_TELEGRAM_BOT_TOKEN

# 4. Restart so the new secrets land
sudo docker compose -f /opt/dipdiver/DipDiver/deploy/docker-compose.yml restart

# 5. Open the UI from any tailnet device
open https://dipdiver.<your-tailnet>.ts.net
# (Use `tailscale serve --bg --https=443 8765` first if you want clean HTTPS)
```

### Tailscale Serve (one-shot HTTPS on the tailnet)

```bash
sudo tailscale serve --bg --https=443 8765
# UI now at https://dipdiver.<tailnet>.ts.net (no Caddy needed)
```

### Caddy variant (only if you want compression/log/multi-domain)

See `deploy/Caddyfile`. Install Caddy on the VM (`apt install caddy`),
copy/edit the Caddyfile, and point the `reverse_proxy` at `127.0.0.1:8765`.
Tailscale Serve is simpler for most use cases.

## State on the VM

All persistent state lives under `/var/lib/dipdiver/`:

```
/var/lib/dipdiver/
├── scoreboard/scoreboard.jsonl   # the audit log (back this up)
├── db/ui.sqlite                  # job logs, kill-switch events
├── logs/                         # m3_live + m2_lite per-day records
├── config/ui_config.yaml         # strategy registry (managed via /config)
├── rendered/SCOREBOARD.md        # rendered scoreboard, served by /scoreboard
└── secrets/.env.m2               # mounted into the container
```

Override the root with `DIPDIVER_UI_DATA_ROOT` if you want a different layout
(e.g. `/srv/dipdiver`).

## Backups

The only file you can't regenerate is `scoreboard/scoreboard.jsonl`. Back it
up daily — any S3-compatible bucket works.

```bash
# Example: nightly Backblaze B2 sync
0 4 * * * rclone copy /var/lib/dipdiver/scoreboard b2:my-dipdiver/scoreboard
```

## Updating

```bash
cd /opt/dipdiver/DipDiver
sudo git pull
sudo docker compose -f deploy/docker-compose.yml build
sudo docker compose -f deploy/docker-compose.yml up -d
```

The container restarts; the persistent volume preserves all state.
