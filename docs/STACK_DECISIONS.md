# Stack Decisions

ADR-style record of why each layer was chosen. Each entry: **decision · alternatives considered · why picked · why others rejected · validation evidence · open risks**.

---

## ADR-001 · Research brain: RD-Agent(Q) on Qlib

**Decision.** Use Microsoft RD-Agent(Q) as the agentic research loop, with Qlib as the underlying quant platform.

**Alternatives considered.**
- TauricResearch/TradingAgents as the brain (LLM-debate-as-trader)
- AI4Finance/FinRobot (multi-agent equity research)
- AI4Finance/FinRL (pure DRL)
- HKUDS/AI-Trader (collaborative agent platform)
- Build-our-own LLM agent loop

**Why picked.**
- **Peer-reviewed.** ICML 2026, NeurIPS 2025, ACL 2026 Findings. arXiv 2505.15155.
- **Independent benchmark win.** Leads MLE-bench (75 Kaggle competitions) at 30.22% with o3 + GPT-4.1.
- **Quant-specific result.** Claims ~2× annual return vs benchmark libraries with 70% fewer factors on real-market backtests.
- **The loop is the right shape.** Researcher proposes → developer implements → backtest → feedback. That's how human quants work; that's what should be automated.
- **Qlib pairing is native** (RD-Agent(Q) targets Qlib directly). Saves months of integration.

**Why others rejected.**
- *TradingAgents* and *FinRobot* are LLM-debate-as-decision-maker, which is post-hoc reasoning over data they didn't generate. They make plausible-sounding trades; they don't discover alpha. (TradingAgents earns a different role — see ADR-003.)
- *FinRL* is pure RL, not agentic; the authors themselves redirect production users to FinRL-X. RL alone doesn't write its own factors.
- *AI-Trader* is a collaboration platform, not an alpha-generation engine. Useful as a signal source, not a brain.
- *Roll our own* — no chance we out-engineer Microsoft Research on a 6-month budget.

**Validation evidence.**
- Three peer-reviewed venues, an independent leaderboard, and a quant-specific arXiv preprint with reproducible code.

**Open risks.**
- The 2× claim is Microsoft's number on Microsoft's chosen universe. **We will reproduce on our universe before this component is load-bearing.** If reproduction fails, fall back to Qlib's built-in models (LightGBM, LSTM, Transformer) and revisit.
- RD-Agent's published config uses o3 + GPT-4.1. LLM costs may dominate; we'll evaluate cheaper substitutes (Sonnet 4.6, Haiku 4.5, DeepSeek) in milestone 2.

---

## ADR-002 · Quant platform: Microsoft Qlib

**Decision.** Use Qlib as the data + features + backtester + RL-env layer beneath RD-Agent(Q).

**Alternatives considered.**
- Lumibot (Python, AI-agent-aware)
- Zipline / Zipline-reloaded
- Backtrader
- vectorbt
- Build on Lean directly (skip Qlib)

**Why picked.**
- Most mature open-source quant platform (43.7k stars, 13k+ commits in adjacent Microsoft repos).
- 25+ benchmarked models out of the box.
- RL execution environment included.
- RD-Agent(Q) speaks Qlib natively — choosing anything else means we lose ADR-001's value.

**Why others rejected.**
- *Lumibot* and *Backtrader* are execution-loop frameworks, not research platforms. They lack a feature store.
- *Zipline* is essentially abandoned upstream.
- *vectorbt* is a research tool, not a deployment platform.
- *Lean-only* would force us to rebuild Qlib's feature engine inside C# — unaffordable.

**Validation.** Used in published Microsoft Research papers; basis for RD-Agent(Q)'s real-market claims.

**Open risks.** Qlib's release cadence has slowed (v0.9.7 was August 2025). If it goes dormant, we may need to fork.

---

## ADR-003 · Risk-veto committee: TradingAgents topology (veto-only)

**Decision.** Adopt TradingAgents' multi-agent debate structure (analyst / researcher / trader / risk-manager) as a **veto-only** layer between the brain and execution. The committee can block or cap trades; it cannot create or enlarge them.

**Alternatives considered.**
- No committee — RD-Agent output flows straight to Lean.
- Use TradingAgents as the brain (rejected per ADR-001).
- Use FinRobot's perception-brain-action committee.

**Why picked.**
- LLM agents are strong at qualitative "this looks wrong" pattern matching (regime shifts, news that contradicts the proposal, position concentration that the optimiser missed).
- LLM agents are weak at sizing and execution. Don't give them what they're bad at.
- TradingAgents' topology is the most-cited reference design (80.7k stars, arXiv 2412.20138).
- Veto-only neutralises the "hallucinated trade" failure mode that has plagued LLM-trader projects.

**Why others rejected.**
- *No committee* — gives up free defence-in-depth.
- *FinRobot* — its committee is bundled with its execution layer; harder to isolate as a pure gate.

**Validation.** TradingAgents has academic publication; veto-only mode is our addition and will be validated by counting **how often the veto saves money** vs how often it blocks profitable trades — a metric the forward-eval harness tracks.

**Open risks.** A miscalibrated veto can starve a profitable strategy. The harness must track veto regret; if vetoes consistently lose money, the committee gets demoted to "annotation only".

---

## ADR-004 · Execution chassis: QuantConnect Lean

**Decision.** Use Lean as the only code path that reaches a broker.

**Alternatives considered.**
- Lumibot
- ccxt + custom event loop
- Alpaca-py + custom event loop
- Zipline live

**Why picked.**
- Most battle-tested OSS engine (19.4k stars, 13k+ commits, runs QuantConnect's hosted platform handling real money).
- Backtest↔live parity is a first-class design property, not bolted on.
- Native brokerage plugins for IBKR, Alpaca, Tradier, OANDA, Binance, and more.
- Event-driven model is correct for this domain (most OSS alternatives are vectorised, which lies about live behaviour).

**Why others rejected.**
- *Lumibot* — newer, smaller, less battle-tested in real money. Good fallback.
- *ccxt / Alpaca-py + custom loop* — recreates Lean badly.
- *Zipline live* — effectively unmaintained.

**Validation.** QuantConnect cloud has been running real strategies for years; Lean is the engine.

**Open risks.** Lean is C#-first. Most agentic code is Python. We will use Lean's Python wrappers; if performance bites, we revisit.

---

## ADR-005 · Broker breadth: FinceptTerminal adapters (ported into Lean)

**Decision.** Borrow FinceptTerminal's connectors for brokers Lean doesn't natively support — primarily Indian retail brokers (Zerodha, Angel One, Upstox, Fyers) — and port them into Lean's `IBrokerage` interface.

**Alternatives considered.**
- US-only scope (skip Indian brokers).
- Run two separate execution stacks (Lean for US, FinceptTerminal for IN).
- Build broker adapters from scratch against each broker's SDK.

**Why picked.**
- FinceptTerminal already speaks these brokers' APIs (16 live integrations, including Indian retail).
- A single execution chassis (Lean) means one parity guarantee, one risk system, one audit log.
- Porting is cheaper than greenfield broker integration.

**Why others rejected.**
- *US-only* — closes off a meaningful market for this project's likely users.
- *Two stacks* — doubles every subsequent integration and validation cost.

**Validation.** FinceptTerminal's adapters are in active use; conformance tests will be re-run against Lean's interface before any live key.

**Open risks.** Indian broker APIs change; FinceptTerminal may not always be up to date. Each ported adapter needs an owner.

---

## ADR-006 · Forward-eval harness: live-trade-bench pattern

**Decision.** Adopt live-trade-bench's nightly forward-evaluation pattern as **the** validation gate before any capital deployment.

**Alternatives considered.**
- Backtest-only gating.
- Quarterly walk-forward on historical data only.
- Trust RD-Agent(Q)'s own backtest reports.

**Why picked.**
- Backtests are systematically optimistic; the LLM-trading literature is full of repos with strong backtests and silent live records.
- Forward-only evaluation against real data on a paper account is the cheapest way to catch lookahead leakage and regime-change brittleness.
- live-trade-bench's design is published (arXiv) and explicitly targets the overfit problem.

**Why others rejected.**
- *Backtest-only* — exactly the failure mode this project exists to avoid.
- *Walk-forward only* — better than nothing, still doesn't catch real-time data anomalies.
- *Trust upstream reports* — no public agentic-trading project has ever earned that trust.

**Validation.** The harness is itself the validation mechanism for everything else. Its own correctness is checked by running known-bad strategies (e.g. coin-flip) and confirming the scoreboard exposes them as bad.

**Open risks.** Two months of green forward-test is a much weaker signal than a decade of live trading. We treat it as **necessary but not sufficient** — capital deployment gates are layered (see [`VALIDATION.md`](VALIDATION.md)).

---

## Rejected from the stack (and why)

| Repo | Why not |
|---|---|
| AI4Finance/FinRL | Not agentic. Superseded by RD-Agent(Q) + Qlib RL env for our purposes. |
| AI4Finance/FinRobot | Output is research reports, not trades. No live validation. |
| Open-Finance-Lab/AgenticTrading | Good engineering hygiene (Pydantic validators), but Lean enforces order schemas more rigorously. |
| Lumiwealth/Lumibot | Solid; demoted to fallback for ADR-004. Smaller and younger than Lean. |
| EthanAlgoX/LLM-TradeBot, TradingGoose, Vibe-Trading, QuantDinger | Feature-rich READMEs; no paper, no published forward record, marketing-heavy. |
| HKUDS/AI-Trader | Useful as an optional signal source via API; not load-bearing. |
| FinceptTerminal persona agents | Optional opinion-providers into ADR-003 committee; not load-bearing. |
