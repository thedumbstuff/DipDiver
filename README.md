# DipDiver

> Agentic AI trading stack — assembled from validated open-source components.

**Status:** Pre-alpha · Architecture and documentation phase · No code yet · **Do not deploy capital.**

---

## What this is

DipDiver is an assembly of best-in-class open-source projects into a single agentic trading stack. It is **not** a from-scratch trading framework. The thesis: the strongest validated components already exist — the missing piece is a coherent integration with forward-only validation gating real-money use.

The stack pairs a peer-reviewed agentic research loop (Microsoft RD-Agent on Qlib) with an industrial execution engine (QuantConnect Lean), broker breadth borrowed from FinceptTerminal, a multi-agent risk-veto committee inspired by TradingAgents, and a forward-evaluation harness based on live-trade-bench.

## What this is **not**

- Not a get-rich tool. Not financial advice. See [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md).
- Not a yet-another LLM-wraps-an-API trading bot. The brain is RD-Agent's research+development loop, not a single LLM call per bar.
- Not validated on real money. Validation is forward-only on paper accounts. See [`docs/VALIDATION.md`](docs/VALIDATION.md).

## Stack at a glance

| Layer | Component | Source |
|---|---|---|
| Research brain (factor + model co-optimisation) | RD-Agent(Q) | [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) |
| Quant platform (data, features, backtester, RL env) | Qlib | [microsoft/qlib](https://github.com/microsoft/qlib) |
| Risk-veto committee (multi-agent debate, qualitative gate) | TradingAgents topology | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) |
| Execution chassis (order routing, backtest↔live parity) | Lean | [QuantConnect/Lean](https://github.com/QuantConnect/Lean) |
| Broker breadth (India + global + crypto) | FinceptTerminal adapters | [Fincept-Corporation/FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) |
| Forward-evaluation harness | live-trade-bench pattern | [ulab-uiuc/live-trade-bench](https://github.com/ulab-uiuc/live-trade-bench) |

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

Repo skeleton only — no trading code yet. These commands set up the dev environment and run the scaffolding tests.

```bash
git clone <this-repo>
cd DipDiver
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check .          # lint
black --check .       # format check
mypy                  # type check
pytest                # smoke tests
```

> **For M1 brain work (`pip install -e ".[brain]"`), use Python 3.12** — Qlib's wheels don't yet cover 3.13. The `dev` extra works on 3.11–3.13.

CI runs the same four checks on push/PR (`.github/workflows/ci.yml`) plus a gitleaks secret scan (`.github/workflows/secret-scan.yml`).

Next runnable milestone is M1 (Qlib baseline) — see [`docs/ROADMAP.md`](docs/ROADMAP.md).

## License

MIT — see [`LICENSE`](LICENSE). Compatible with the upstream components (Qlib MIT, RD-Agent MIT, Lean Apache-2.0). Component licenses are preserved per their terms.

## Acknowledgements

Built on the work of the Microsoft Research, QuantConnect, AI4Finance Foundation, Tauric Research, HKU Data Intelligence Lab, ULab UIUC, and Fincept Corporation teams. DipDiver claims no original research — it claims integration.
