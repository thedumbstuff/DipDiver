#!/usr/bin/env bash
# Stage 4 / M11 — shell companion to the /health kill switch.
#
# When the FastAPI UI is down (boot failure, port conflict, etc.) and you
# still need to flatten positions + halt scheduled trading, run this.
#
# Behaviour:
#   1. Reads ALPACA_API_KEY / ALPACA_API_SECRET from env or .env.m2.
#   2. Cancels all open orders.
#   3. Closes all positions (with cancel_orders=True for safety).
#   4. Touches DIPDIVER_KILLED at the data root so the nightly script
#      refuses to run until the operator removes it manually.
#
# Usage:
#   bash scripts/kill_switch.sh "reason for the kill"
#
set -euo pipefail

REASON="${1:-no reason supplied}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Load .env.m2 if present and the env vars aren't already set.
if [ -f .env.m2 ]; then
  set +u
  source .env.m2
  set -u
fi

if [ -z "${ALPACA_API_KEY:-}" ] || [ -z "${ALPACA_API_SECRET:-}" ]; then
  echo "kill_switch: ALPACA_API_KEY / ALPACA_API_SECRET not set; cannot reach broker." >&2
  exit 2
fi

PY="${PYTHON:-python}"

"$PY" - <<'PYTHON'
import datetime as dt
import os
import pathlib

from dipdiver._paths import ui_data_root
from dipdiver.adapters.alpaca.client import AlpacaPaperClient

reason = os.environ.get("KILL_REASON", "shell kill_switch")
print(f"[kill_switch] connecting to Alpaca...")
client = AlpacaPaperClient()

print(f"[kill_switch] cancelling all open orders...")
try:
    client._trading.cancel_orders()  # private but stable
    print("  ok")
except Exception as e:
    print(f"  cancel_orders FAILED: {e}")

print(f"[kill_switch] closing all positions...")
try:
    client._trading.close_all_positions(cancel_orders=True)
    print("  ok")
except Exception as e:
    print(f"  close_all_positions FAILED: {e}")

flag = ui_data_root() / "DIPDIVER_KILLED"
flag.parent.mkdir(parents=True, exist_ok=True)
flag.write_text(
    f"killed_at={dt.datetime.now(dt.timezone.utc).isoformat()}\nreason={reason}\n",
    encoding="utf-8",
)
print(f"[kill_switch] wrote {flag}")
print("[kill_switch] DONE. Re-enable trading: rm DIPDIVER_KILLED + re-enable nightly_run via /schedule.")
PYTHON
