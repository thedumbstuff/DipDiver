# DipDiver LLM Wiki

This wiki is the fast-path reference for the DipDiver codebase. It is written
*for* LLM consumption in future sessions — concrete file paths, function names,
data flows, and gotchas, not generic prose.

**Read order** depends on what you need to do:

| If you need to…                                        | Start here                                            |
| ------------------------------------------------------ | ----------------------------------------------------- |
| Understand the project end-to-end                      | [scaffold.md](scaffold.md) → this table top-to-bottom |
| Debug a signal / re-train a model                      | [m1_baselines.md](m1_baselines.md)                    |
| Add or audit an LLM-proposed factor                    | [m2_lite.md](m2_lite.md)                              |
| Trace a backtest order or a live Alpaca trade          | [m3_execution.md](m3_execution.md)                    |
| Tune the risk-veto committee or add a persona          | [m5_committee.md](m5_committee.md)                    |
| Inspect Qlib bins / Lean ZIPs / signals.csv            | [data_and_lean.md](data_and_lean.md)                  |
| Know what counts as evidence / overfit / a gate        | [validation.md](validation.md)                        |
| Check what is and isn't tested                         | [tests.md](tests.md)                                  |

## Subsystem index

- **[M1 — Qlib Baselines (Brain)](m1_baselines.md)** — Reproducible LightGBM/LSTM baselines per (universe, model). Locked results serve as comparators for all downstream improvements.
- **[M2-lite — LLM Factor Proposer (Brain)](m2_lite.md)** — Lightweight 800-LOC LLM-driven factor discovery; iterates propose→backtest→record in-process. Replaced a failed RD-Agent integration.
- **[M3 — Lean Backtest + Alpaca Paper Trading (Adapters)](m3_execution.md)** — Bridges M1/M2 signals to Lean backtest and Alpaca paper execution via shared TopkDropoutStrategy logic; parity 70.5% with documented cascade-drift.
- **[M5 — Risk-Veto LLM Committee](m5_committee.md)** — Four-persona panel (fundamental, technical, risk, value) reviewing proposed trades between M1/M2 and M3. Risk persona has single-vote veto; others need ≥2 to block. Fail-open. Wired via `--with-committee` flag on `m3_live_alpaca.py`.
- **[Scaffold — Project Setup, Packaging, CI, License](scaffold.md)** — Package layout, extras_require groups, `repo_root()` helper, CI workflows, contributor rules.
- **[Data Layout — Qlib Bins, Lean Data Format, Signals CSV](data_and_lean.md)** — Three-layer pipeline: Qlib binary OHLCV → signals.csv → Lean ZIP equity format.
- **[Validation — Forward-Eval, Gates, and Anti-Overfit Rules](validation.md)** — Tier-of-evidence ladder, the six anti-overfit rules, capital deployment gates. M6 forward-eval harness specced but not built.
- **[Test Infrastructure and Coverage](tests.md)** — What is and isn't tested; how to run pytest.

## Milestone status (as of 2026-06-04)

| Milestone | Status      | Wiki page                              |
| --------- | ----------- | -------------------------------------- |
| M0        | done        | [scaffold.md](scaffold.md)             |
| M1        | done        | [m1_baselines.md](m1_baselines.md)     |
| M2-lite   | done        | [m2_lite.md](m2_lite.md)               |
| M3        | done        | [m3_execution.md](m3_execution.md)     |
| M4        | deferred    | (Indian-broker breadth — not started)  |
| M5        | done        | [m5_committee.md](m5_committee.md)     |
| M6        | not started | (forward-eval harness)                 |

## How to refresh this wiki

The pages are written by a documentation workflow (one agent per subsystem,
run in parallel). To regenerate after substantial code changes:

```
# In Claude Code, ask:
"Rebuild llm_wiki from the current code — same fan-out as last time."
```

Or manually invoke the workflow at
`workflows/scripts/dipdiver-wiki-build-*.js` (it's saved with `meta.name: dipdiver-wiki-build`).

If a subsystem changes a lot, edit just that one page rather than regenerating
everything — cheaper and avoids drift in unrelated pages.

## Conventions

- Every page opens with **Purpose** and **Entry Points**.
- Code references use `path/to/file.py:line` format.
- Every page ends with a **Cross-references** section linking sibling pages.
- Code excerpts are 5–15 lines max — show the load-bearing logic, not the
  whole function.
- "Gotchas" sections are the high-value part — preserve them carefully when
  editing.
