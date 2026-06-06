# M8 · Operator UI + always-on automation

> **Goal.** A self-hosted ops console + scheduler that removes every "I have to remember to run this" step. The operator opens a browser, sees what happened, and clicks to act. Background jobs run on a VM so the laptop doesn't matter.
>
> **Scope.** Full ops console (dashboard + triggers + config + schedule editor), self-hosted VM from day one. This is the operator's control surface — not a customer-facing product, not a SaaS.

## Why this exists

Today, eight CLI scripts (`m1_setup`, `m1_run`, `m2_lite_run`, `m3_export_signals`, `m3_live_alpaca`, `m5_review_signals`, `m6_nightly`, `m6_render_scoreboard`) need to be invoked manually in the right order at the right time. The operator (you) has to:

1. Remember to run `m6_nightly` after market open every weekday.
2. Tail logs to know what happened.
3. Read JSON files to see committee decisions.
4. Re-run scripts when something failed.
5. Edit YAML config files for any change.

M6 made the data layer (JSONL scoreboard + run records) machine-readable. M8 builds the ops surface on top: web UI + always-on scheduler. After M8 lands, the operator runs **zero commands** for the daily flow — they only intervene when something is wrong or they want to change strategy.

This is explicitly **not** the "we won't build a UI" the original ROADMAP referenced. That meant no customer product. An operator console for your own workflow is a different artifact.

---

## Tech stack (decided)

| Layer | Choice | Rationale |
|---|---|---|
| Backend | **FastAPI** | Async-capable, native Pydantic (already in use), type-safe routes, OpenAPI for free. |
| Frontend | **Jinja2 + HTMX + Tailwind** | Server-rendered HTML, light client JS (HTMX swaps fragments). Zero Node toolchain. Operator-grade, not glossy. |
| Job scheduler | **APScheduler** w/ SQLite job store | In-process; survives restarts via the SQLite store. Cron-like syntax. |
| Cache / index DB | **SQLite** (`dipdiver/ui/db.sqlite`) | Run index, config, schedule, user prefs. JSONL stays canonical for the scoreboard. |
| Realtime | **Server-Sent Events** | One-way log streaming during a run. No WebSocket complexity. |
| Reverse proxy + TLS | **Caddy** | Automatic Let's Encrypt; one-line config. |
| Auth | **Tailscale** | Mesh VPN; UI binds to tailnet IP only. No public ingress. Locked 2026-06-04 — HTTP Basic fallback dropped. |
| Container | **Docker Compose** | Two services: `dipdiver-ui` + `caddy`. One-command deploy. |
| Hosting | **Hetzner CX22** or **DigitalOcean basic droplet** | ~$5/mo, always-on, EU/US region close to Alpaca. |
| Python package | New `dipdiver-ui` extra in `pyproject.toml` | Optional install for users who only want the CLI. |

**Module layout:**

```
dipdiver/ui/
├── __init__.py
├── app.py             — FastAPI app factory
├── deps.py            — DB session, config loader, auth dep
├── routes/
│   ├── dashboard.py
│   ├── strategies.py
│   ├── runs.py
│   ├── decisions.py
│   ├── triggers.py
│   ├── config.py
│   ├── schedule.py
│   ├── health.py
│   ├── positions.py
│   └── logs.py
├── jobs/
│   ├── scheduler.py   — APScheduler boot + job registry
│   ├── nightly.py     — wraps scripts/m6_nightly.main
│   ├── pnl_settle.py  — fetches Alpaca portfolio_history → PnlSettledEvent (M6.2)
│   ├── m2_lite.py     — wraps scripts/m2_lite_run.main
│   ├── veto_backfill.py — VetoOutcomeEvent writer (M6.3)
│   └── signal_refresh.py — wraps scripts/m3_export_signals.main
├── db/
│   ├── models.py      — SQLAlchemy / SQLModel: Run, Config, ScheduleEntry, JobLog
│   ├── migrate.py     — schema bootstrap
│   └── projections.py — pull from JSONL → SQLite cache
├── templates/         — Jinja2: base.html + one per page
├── static/            — Tailwind output + small JS
└── cli.py             — `dipdiver-ui serve` entry point
```

---

## Sitemap

```
/                            Dashboard
/strategies                  Per-strategy A/B totals + sparkline
/strategies/<sid>            Single-strategy deep dive
/runs                        All daily runs (filterable)
/runs/<date>                 Single-day deep dive
/decisions/<date>/<symbol>   Full committee transcript (all 4 personas)
/positions                   Live Alpaca paper positions
/scoreboard                  Live-rendered scoreboard table
/triggers                    Manual run buttons + live SSE log
/config                      Edit config.yaml (form-driven)
/schedule                    View + edit job cron expressions
/health                      Alpaca conn, last run, last error
/logs                        Tail logs/m3_live, logs/m2_lite, app log
/auth/login                  Login (if HTTP Basic chosen — otherwise rendered upstream by Caddy/Tailscale)
```

### Dashboard layout (rough)

```
+--------------------------------------------------------------+
| DipDiver Ops                          [Status: 🟢 healthy]   |
+--------------------------------------------------------------+
| Today              | Last 7 days        | Account            |
| 3 buys approved    | +$420 unrealised   | Equity:  $99,820   |
| 0 vetoed           | -1 veto-regret hit | Cash:    $0        |
| Next run: 09:35 ET | Sharpe: 1.31       | Status:  ACTIVE    |
+--------------------------------------------------------------+
| Scoreboard (latest 5 days)                  [ See all → ]    |
+-----------+--------+-------+-------+---------+--------+------+
| Date      | Strat  | Buys  | Veto% | P&L     | Equity | ⋯    |
| 2026-06-04| _comm  | 3     |  0%   | +$120   | 99,820 |      |
| 2026-06-03| base   | 10    |  —    | +$60    | 99,700 |      |
| ...                                                          |
+--------------------------------------------------------------+
| Quick actions                                                |
| [ Run nightly now ] [ Render scoreboard ] [ Settle yesterday's P&L ] |
+--------------------------------------------------------------+
```

### Day-run drill-down

```
/runs/2026-06-04
+--------------------------------------------------------------+
| 2026-06-04 · dow30 · dow30_lightgbm_committee                |
+--------------------------------------------------------------+
| Proposal              | Committee            | Orders        |
| target: 10 names      | 3 buys reviewed      | 3 closes      |
| adds: CVX, TRV, V     | 3 approved (100%)    | 3 opens       |
| removes: AMGN,CAT,MCD | cost: $0.003         | all FILLED    |
+--------------------------------------------------------------+
| Per-symbol decisions                                         |
| CVX  buy  ✅ 3/4   fundamental(+) technical(~) risk(+) value(+)  [drill] |
| TRV  buy  ✅ 3/4   value(+)        technical(~) risk(+) value(+) [drill] |
| V    buy  ✅ 4/4   unanimous                                   [drill] |
+--------------------------------------------------------------+
| Source: logs/m3_live/dow30/2026-06-04.json    [Raw JSON ↗]   |
+--------------------------------------------------------------+
```

---

## Scheduled jobs

| Job | Default cron (UTC) | What it does |
|---|---|---|
| `nightly_run` | `35 14 * * 1-5` (09:35 ET) | Wraps `scripts/m6_nightly.main` — m3_live_alpaca + scoreboard backfill |
| `pnl_settle` | `30 9 * * 2-6` (04:30 ET, T+1) | Fetches yesterday's Alpaca portfolio_history → writes `PnlSettledEvent` |
| `veto_backfill` | `0 6 * * 1-5` | Walks all `VetoOutcomeEvent` candidates T+5 old, looks up close prices from Qlib, writes events |
| `m2_lite_weekly` | `0 3 * * 0` (Sunday) | Runs M2-lite factor discovery on each universe with `cost_cap = $0.50` |
| `signal_refresh` | _on M1 retrain trigger only_ | Regenerates `data/signals/*.csv` from refreshed M1 models |
| `scoreboard_render` | `0 15 * * 1-5` (10:00 ET) | Writes rendered scoreboard to `{data_root}/rendered/SCOREBOARD.md`. Served by UI at `/scoreboard`. Not committed to repo (decision 3 — VM-only). |

Each job is editable in `/schedule`. The page validates cron expressions before saving. Jobs that fail emit a `JobLog` row visible on `/health`.

---

## Phased implementation

Each phase is a 2-4 session chunk. After each one, the operator can deploy and use the partial UI.

### M8.1 — Skeleton + scheduler + dashboard (MVP)
*Goal: stop running `m6_nightly` manually.*

- FastAPI app, Jinja2 templates, base.html, Tailwind build pipeline.
- SQLite + projection from `scoreboard.jsonl` (`Run` table).
- Dashboard page: today's status, 5 latest scoreboard rows, account snapshot, next scheduled run.
- APScheduler with `nightly_run` job. Cron read from config.
- `/health` page: Alpaca conn check, last run timestamp.
- `dipdiver-ui serve` CLI entry point.
- Local dev recipe (no auth, localhost only).

**After this:** start the server, leave it running, daily runs happen automatically. Browse to localhost:8000 to see status.

### M8.2 — History + drill-downs + transcripts
*Goal: see what happened without grepping JSON.*

- `/runs` list page (filter by date / strategy / outcome).
- `/runs/<date>` deep dive (proposal, committee summary, orders, fills if available).
- `/decisions/<date>/<symbol>` transcript viewer (per-persona rationale, full text).
- `/positions` Alpaca paper positions table (live read).
- Equity sparkline on dashboard (needs P&L events — depends on M6.2 also landing).

### M8.3 — Strategies + scoreboard + charts
*Goal: A/B comparison surfaces.*

- `/strategies` list with running totals (same data as `m6_render_scoreboard`).
- `/strategies/<sid>` single-strategy deep dive: equity curve, hit-rate, drawdown, veto rate over time, committee cost cumulative.
- `/scoreboard` live-rendered table (same renderer, web view).
- Plotly charts for equity curve + drawdown (CDN, no build).

### M8.4 — Triggers + config + schedule editor
*Goal: control everything from the UI.*

- `/triggers` page: buttons for nightly-now, m2-lite-now, pnl-settle-now, scoreboard-render-now, signal-refresh. Each triggers an APScheduler one-shot job and streams stdout via SSE.
- `/config` page: form-driven YAML editor for universe / model / committee on-off / providers / cost caps. Diffs shown before save. Old versions kept in `config_history/`.
- `/schedule` page: cron expressions per job. Validate before save. Show "next 5 fire times" preview.
- Kill-switch button on `/health`: cancel all open Alpaca orders, flatten positions, disable nightly job. Confirmation prompt + reason field.

### M8.5 — VM deploy + auth + TLS
*Goal: production hosting.*

- Dockerfile (multi-stage build, small image).
- `docker-compose.yml`: `dipdiver-ui` + `caddy` + named volume for SQLite + bind mount for `scoreboard/` and `logs/`.
- Caddyfile binding to the Tailscale IP only (no `:80`/`:443` public listeners).
- Tailscale install on VM as part of `bootstrap_vm.sh`; admin runs `tailscale up` interactively to authorise the node into the tailnet.
- TLS via Tailscale MagicDNS HTTPS certs (`tailscale cert`) — auto-issued, auto-renewed, no Let's Encrypt rate limits.
- UI reachable at `https://dipdiver.<your-tailnet>.ts.net` from any device on the same tailnet (laptop, phone, etc).
- `.env.ui.example` with the deploy-time variables.
- Hetzner provisioning recipe: 1-page bash script that does `apt`, Docker install, repo clone, compose up.
- Backup recipe: `scoreboard/scoreboard.jsonl` to S3-compatible bucket nightly (lightweight; Backblaze B2 ~$0.005/GB/mo).

### M8.6 — Polish + alerting + observability
*Goal: know when something broke without checking.*

- Telegram alert (via `dipdiver-ops-bot`) when:
  - Nightly job fails (≥2 consecutive misses).
  - Committee veto rate drifts outside calibration band (5-25%).
  - Daily realised P&L drops more than 1× ATR.
  - Alpaca account status changes (suspended, restricted).
  - VM disk usage > 80% or scoreboard.jsonl growth rate anomalous.
- `/logs` page: tail recent app + m3_live + m2_lite logs with filtering.
- Job retry: exponential backoff, max 3 attempts, then alert.
- `prometheus_client` `/metrics` endpoint (cheap; useful if you ever wire Grafana).

---

## Deployment recipe (M8.5 preview)

```
1. Provision VM (Hetzner CX22, Ubuntu 24.04, ~$5/mo).
2. SSH in. Run the bootstrap script:
   curl -sSL https://raw.githubusercontent.com/<user>/DipDiver/main/scripts/ops/bootstrap_vm.sh | sudo bash
   # Installs Docker, clones repo, builds image, starts compose.
3. Run `tailscale up` interactively, paste the auth URL into a browser, authorise the node.
   Auto-issue cert: `tailscale cert dipdiver.<your-tailnet>.ts.net`.
4. Drop secrets into `${DIPDIVER_DATA_ROOT}/secrets/.env.m2`.
5. Browse to `https://dipdiver.<your-tailnet>.ts.net/health` from any tailnet device → confirm all green.
```

After step 6, the operator's daily routine is: open dashboard, glance, close tab.

---

## What's NOT in M8

- **Real money.** Paper trading only. Live-capital authorization still goes through M7 + VALIDATION.md gates.
- **Customer-facing product.** This is the operator's console. No multi-tenant, no signup, no payments.
- **Auto-strategy-tuning.** The UI exposes config; the changes are still operator decisions, not automated.
- **Mobile apps.** Web responsive enough is fine. No native mobile.
- **Historical replay UI.** You can backtest from CLI; M8 doesn't add a backtest runner in the browser.
- **Multi-account.** One Alpaca paper account per deploy. Two strategies on the same account is fine; two accounts isn't.

---

## Resolved decisions (locked 2026-06-04)

1. **Auth: Tailscale.** UI bound to the Tailscale IP only; no public ingress. Caddy still terminates TLS on the tailnet hostname (auto-issued via Tailscale's `--https` MagicDNS cert flow). HTTP Basic dropped as a fallback path — keep one auth model, not two.
2. **VM paths: configurable.** All filesystem locations come from a single `DIPDIVER_DATA_ROOT` env var (default `/var/lib/dipdiver`) with sub-paths derived:
   - `{root}/scoreboard/scoreboard.jsonl`
   - `{root}/db/ui.sqlite`
   - `{root}/logs/`
   - `{root}/config/config.yaml`
   - `{root}/secrets/.env.m2` (or mounted from outside)

   Container mounts a single named volume to `{root}`; users who want different layouts override the env var. Code path: extend `dipdiver/_paths.py` with `data_root()`, `scoreboard_path()`, `ui_db_path()` helpers reading the env var.
3. **Public scoreboard: NO — VM-only.** Rendered scoreboard is served by the UI at `/scoreboard`, visible only to operators on the tailnet. **Implication for VALIDATION.md:** the "scoreboard is append-only and **public**" claim becomes "append-only and **operator-visible**." Public auditability isn't a goal of v1. If we ever want to publish (e.g. for capital partners), revisit and add a `scoreboard-sync` job at that point.
4. **Alerts: Telegram.** A `dipdiver-ops-bot` Telegram bot fires on job failures, veto-rate drift, and Alpaca status changes. Bot token in `.env.m2`. Chat ID stored in `config.yaml`. No email for v1.
5. **Kill-switch lands in M8.4.** Same code path serves paper recovery (stuck orders, runaway rebalance) and the eventual M7 live-capital requirement. Lives on `/health` page with confirmation prompt + reason field, logged to `JobLog`.

### Implication to flag

Decision 3 changes the tier-1+ evidence story. VALIDATION.md needs a footnote: "The append-only invariant is preserved (no edits, no deletes); the *public* part of the original spec is deferred. Capital deployment gates 1–4 are still queryable — they just require operator access to the VM."

---

## Acceptance criteria

When M8 is done:

- [ ] You don't run any CLI for the daily flow.
- [ ] Dashboard loads in <500ms with current day's status visible.
- [ ] Every committee decision is one click away from "I read the rationale."
- [ ] Schedule changes take effect without restarting the server.
- [ ] Killing the VM and restarting it loses ≤1 day of data (scoreboard.jsonl + SQLite are durable).
- [ ] An outage of >1 nightly run alerts you within 24h.
- [ ] Adding a new strategy_id is a config-file change, not a code change.
- [ ] Zero secrets in the repo. All in `.env.m2` mounted at runtime.

---

## Cross-references

- [Validation methodology](../VALIDATION.md) — the UI surfaces validation tiers but doesn't relax them.
- [M6 forward-eval](M6_forward_eval.md) — the data layer this UI sits on top of.
- [M7 live capital](../ROADMAP.md#milestone-7--live-capital-post-week-10--gated) — the kill-switch in M8.4 helps M7 land.
- Tech precedent: APScheduler ([docs](https://apscheduler.readthedocs.io/)), FastAPI ([docs](https://fastapi.tiangolo.com/)), HTMX ([docs](https://htmx.org/)), Tailscale ([docs](https://tailscale.com/kb)).
