# Contributing

Thanks for considering a contribution. DipDiver is small and opinionated. Reading this page first will save you and the maintainers time.

## What DipDiver is — and isn't

DipDiver is an **integration** project. It assembles existing best-in-class components (RD-Agent, Qlib, Lean, TradingAgents topology, FinceptTerminal broker adapters, live-trade-bench pattern) into one stack with disciplined forward-validation.

DipDiver is **not** trying to:

- Invent a new trading framework from scratch
- Be the most feature-rich agentic trader on GitHub
- Provide trading signals or financial advice
- Be a SaaS or hosted product

Contributions that pull the project toward "yet another LLM trading bot" or "let's reimplement Lean in Python" will be declined. Contributions that improve integration, validation, or honesty about results are welcome.

## Hard rules

These are non-negotiable. PRs that violate them are closed without review.

1. **No live-trading performance claims.** The README, docs, code comments, and commit messages must not claim DipDiver "works", "is profitable", "outperforms", or anything similar — unless backed by scoreboard rows from the public forward-eval harness, linked from the claim.
2. **No path from an LLM token to a broker order.** All execution goes through Lean. LLMs propose and veto; they do not place orders. PRs that bypass this are rejected.
3. **No anti-overfit-rule violations.** The rules in [`docs/VALIDATION.md`](docs/VALIDATION.md) — time fence, no re-training on test windows, costs in, no survivorship — apply to every backtest in the repo.
4. **No vendored secrets.** API keys, broker tokens, LLM keys never enter the repo. The repo's CI scans for them; bypassing the scan is a permanent ban.
5. **No bundled binary models.** Models are trained from code + data, reproducibly. Pre-trained checkpoints live elsewhere and are pulled by hash.

## How to contribute

### Bug fixes & small improvements

- Open an issue describing the bug or improvement first if it isn't obvious. For typos and one-line fixes, skip straight to the PR.
- Branch from `main`, name `fix/<short-slug>` or `improve/<short-slug>`.
- One logical change per PR.
- Tests required for any behaviour change.

### New broker adapter

This is one of the highest-value contributions. Recipe:

1. Pick a broker not already supported by Lean natively or by DipDiver.
2. If FinceptTerminal already has an adapter for it, port that. Otherwise, start from the broker's official SDK.
3. Implement Lean's `IBrokerage` interface.
4. Conformance suite green on the broker's paper environment (place / cancel / replace / query order / query positions / query balances).
5. Document the broker's quirks (order types not supported, market-hours edge cases, lot sizes, settlement) in `docs/brokers/<broker>.md`.
6. Add yourself as the adapter's owner — broker APIs change, owners maintain.

### New agent (for the risk-veto committee)

1. Decide what failure mode this agent catches. If you can't write it down, the agent isn't worth adding.
2. Implement against the committee's agent interface.
3. The agent must obey veto-only semantics — it can block or cap, never enlarge.
4. Demonstrate on the forward-eval harness over ≥30 days that the agent's vetoes have net non-negative P&L. (Negative-veto-regret agents are removed.)

### New strategy

1. Strategies live under `dipdiver/strategies/<name>/`.
2. Must be expressible as a Qlib pipeline producing a `PortfolioProposal`.
3. Backtest using Qlib and Lean; the parity test must pass.
4. Strategy ships at validation tier 0 ([`VALIDATION.md`](docs/VALIDATION.md)). No tier upgrades from a PR — only from scoreboard runs.

### Documentation

- Doc PRs are welcome and reviewed quickly.
- One rule: don't make claims about performance, profitability, or live use that aren't backed by the scoreboard. If you find such claims already in the repo, flag them.

## Code style

- Python: ruff + black + mypy strict on new code. Existing code is migrated incrementally.
- C# (Lean side): follow Lean's existing style.
- Type-hint everything. Prefer dataclasses or Pydantic over loose dicts for inter-layer contracts.
- Comments: don't write them unless the *why* is non-obvious. Don't reference issue numbers in code.
- No emojis in code or commit messages.

## Commit & PR conventions

- Commits: imperative mood ("Add Zerodha adapter conformance test"), under 72 chars, body explains *why* not *what*.
- PRs: title is one sentence; description includes scope, what's tested, what's deliberately not tested, and a screenshot/log if there's any user-visible change.
- PRs that touch broker adapters, the LLM-to-Lean boundary, or the forward-eval harness require two reviewers.

## Reviews

We optimise for reviewer time, not contributor time. A PR that is hard to review will sit until the contributor restructures it. Concretely:

- Diffs over ~400 lines are split.
- Refactors and feature changes don't share a PR.
- Generated code (lockfiles, schema dumps) goes in its own commit so it can be skipped in review.

## Reporting security issues

Do **not** open a public issue for security problems. Email the maintainers directly (address in the repo root after maintainership is set). This includes:

- Anything that could leak broker credentials or API keys
- Anything that lets an LLM-injected string reach the order path
- Anything that bypasses the validation gates
- Supply-chain concerns about a dependency

## Code of conduct

Be precise, be honest, be kind. Disagree with ideas, not people. The default assumption when someone disagrees with you is that they have information you don't — ask for it before arguing.
