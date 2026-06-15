# DipDiver

> Agentic AI trading stack — assembled from validated open-source components.

**Status:** Alpha · M0–M3, M5, M6, M8–M14 shipped (M4 deferred, M2 pivoted to M2-lite per ADR-001) · Paper trading on Alpaca, accumulating forward-eval evidence (validation tier 0→2) · Live capital (M7) remains code-gated by `LiveTradingGate` · **Do not deploy capital.**

---

## What this is

DipDiver is an assembly of best-in-class open-source projects into a single agentic trading stack. It is **not** a from-scratch trading framework. The thesis: the strongest validated components already exist — the missing piece is a coherent integration with forward-only validation gating real-money use.

The stack pairs Qlib baselines (LightGBM/LSTM on Alpha158) with an LLM factor proposer (M2-lite, which replaced the original RD-Agent plan — see ADR-001), a multi-agent risk-veto committee inspired by TradingAgents, Alpaca paper execution with Lean backtest parity, a forward-evaluation harness based on live-trade-bench, and a FastAPI operator console (dashboards, picks board, schedules, kill-switch) that automates the nightly pipeline.

What runs today, end to end: M1 models generate signals per universe → the 4-persona committee approves or vetoes each buy → approved orders go to an Alpaca paper account → every decision, order, and P&L lands in an append-only JSONL scoreboard, including counterfactual tracking of vetoed trades (veto regret).

## What this is **not**

- Not a get-rich tool. Not financial advice. See [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md).
- Not a yet-another LLM-wraps-an-API trading bot. Signals come from locked, walk-forward-validated ML baselines; LLMs propose factors (M2-lite) and vote on risk (M5 committee) — they never place an order directly.
- Not validated on real money. Validation is forward-only on paper accounts; every strategy is at tier 0–2 of the evidence ladder. See [`docs/VALIDATION.md`](docs/VALIDATION.md).

## Stack at a glance

| Layer | Component | Source |
|---|---|---|
| Research brain (LLM factor proposer; replaced RD-Agent per ADR-001) | M2-lite (DeepSeek/OpenAI on Qlib expressions) | `dipdiver/brain/m2/` |
| Quant platform (data, features, backtester, baselines) | Qlib | [microsoft/qlib](https://github.com/microsoft/qlib) |
| Risk-veto committee (multi-agent debate, qualitative gate) | TradingAgents topology | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) |
| Execution (Alpaca paper live runner; Lean backtest parity) | alpaca-py + Lean | [QuantConnect/Lean](https://github.com/QuantConnect/Lean) |
| Broker breadth (India + global + crypto) | FinceptTerminal adapters — **deferred (M4)** | [Fincept-Corporation/FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) |
| Forward-evaluation harness | live-trade-bench pattern | [ulab-uiuc/live-trade-bench](https://github.com/ulab-uiuc/live-trade-bench) |
| Ops console (dashboards, picks, schedules, kill-switch) | FastAPI + Jinja2 + HTMX + APScheduler | `dipdiver/ui/` |

Full rationale, alternatives considered, and validation evidence: [`docs/STACK_DECISIONS.md`](docs/STACK_DECISIONS.md).

## Documentation

| Document | Purpose |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layered architecture, data flow, integration boundaries |
| [`docs/STACK_DECISIONS.md`](docs/STACK_DECISIONS.md) | ADR-style: per-layer choice + alternatives + evidence |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 10-week build sequence with milestone gates |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | Forward-eval methodology, kill-switches, capital gates |
| [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md) | Legal, risk, regulatory notices — read before anything else |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to contribute, scope, PR rules |

## Dev quickstart

```bash
git clone <this-repo>
cd DipDiver
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check .          # lint
black --check .       # format check
mypy                  # type check
pytest                # full test suite
```

> **For M1 brain work (`pip install -e ".[brain]"`), use Python 3.12** — Qlib's wheels don't yet cover 3.13. The `dev` extra works on 3.11–3.13.

CI runs the same four checks on push/PR (`.github/workflows/ci.yml`) plus a gitleaks secret scan (`.github/workflows/secret-scan.yml`).

### Run the Ops UI

```bash
pip install -e ".[ui,m2,m3]"
cp .env.m2.example .env.m2    # then fill in ALPACA_API_KEY, ALPACA_API_SECRET, DEEPSEEK_API_KEY
dipdiver-ui serve
# → http://127.0.0.1:8765
```

The scheduler boots with the app and runs the nightly pipeline (signals → committee → Alpaca paper orders → scoreboard) on cron. See [`deploy/README.md`](deploy/README.md) for Docker / self-hosted deployment.

Current milestone status lives in [`docs/ROADMAP.md`](docs/ROADMAP.md); what each shipped milestone does is in [`docs/milestones/`](docs/milestones/).

## License

MIT — see [`LICENSE`](LICENSE). Compatible with the upstream components (Qlib MIT, RD-Agent MIT, Lean Apache-2.0). Component licenses are preserved per their terms.

## Acknowledgements

Built on the work of the Microsoft Research, QuantConnect, AI4Finance Foundation, Tauric Research, HKU Data Intelligence Lab, ULab UIUC, and Fincept Corporation teams. DipDiver claims no original research — it claims integration.
