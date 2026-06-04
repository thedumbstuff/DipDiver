# Scaffold — Project Setup, Packaging, CI, License

## Purpose

The scaffold layer defines how DipDiver is packaged, distributed, tested, and governed. It enforces hard rules about secrets, performance claims, execution boundaries, and validation gates — the "law of the land" for everyone building on this codebase.

**Entry point for learning:**
1. Start with `README.md` to understand the vision (stack composition, what DipDiver is not).
2. Read `CONTRIBUTING.md` for the five hard rules and contribution scope.
3. Understand the package layout via `dipdiver/__init__.py` and `dipdiver/_paths.py`.
4. See `.github/workflows/` for CI gates that enforce these rules.

---

## Package Layout

**Root:** `C:/Shwetank/Work/Workspace/Python/thedumbstuff/DipDiver/`

```
dipdiver/
  __init__.py              # Package root, version "0.0.0"
  _paths.py                # repo_root() and data_root() helpers
  adapters/                # Lean + Alpaca integrations
    __init__.py
    lean/
    alpaca/
  brain/                   # Research brain: RD-Agent(Q) + Qlib baselines
    baselines/             # M1: Qlib baseline backtest harness
    m2/
      lite/                # M2: LLM factor proposer
  brokers/                 # (Placeholder for future broker adapters)
  committee/               # (Placeholder for M5: risk-veto committee agents)
  harness/                 # (Placeholder for forward-eval harness)

tests/                     # Unit + integration tests
notebooks/                 # Jupyter exploratory work
```

No entry points (console scripts) are defined yet; all milestones define their own `__main__.py` files. Example: `dipdiver.brain.baselines.__main__` runs M1 Qlib backtests via `python -m dipdiver.brain.baselines`.

---

## Dependencies & Extras

**File:** `pyproject.toml` (lines 24–66)

### Core (empty during scaffolding)
```toml
dependencies = [
    # Kept empty to avoid stale pins during development.
    # Brain/Execution/Committee pinned per milestone.
]
```

### `[dev]` extra
```toml
dev = [
    "ruff>=0.5",           # Linter + formatter orchestrator
    "black>=24.0",         # Code formatter
    "mypy>=1.10",          # Static type checker (strict mode)
    "pytest>=8.0",         # Test runner
    "pytest-cov>=5.0",     # Coverage reporting
    "pyyaml>=6",           # Config files
    "types-PyYAML",        # Type stubs for PyYAML
]
```
Install with: `pip install -e ".[dev]"`

### `[brain]` extra — M1 & M2 Qlib + LLM
```toml
brain = [
    "pyqlib>=0.9.7; python_version < '3.13'",  # Requires Python ≤3.12 (no 3.13 wheels)
    "pyyaml>=6",
    "lightgbm>=4",
    "torch>=2.2",
    "numpy>=1.26",
    "pandas>=2.2",
    "yfinance>=0.2.40",
]
```
**Note:** Use Python 3.12 venv for M1 brain work; pyqlib does not ship 3.13 wheels yet.

### `[m2]` extra — LLM factor proposer
```toml
m2 = [
    "openai>=1.40",        # OpenAI SDK (also works for DeepSeek via base_url override)
    "pydantic>=2.5",       # Validates LLM output before it touches Qlib
]
```

### `[m3]` extra — Lean backtest + Alpaca live paper trading
```toml
m3 = [
    "lean>=1.0",           # Lean CLI; engine runs in Docker (Docker Desktop required)
    "alpaca-py>=0.30",     # Alpaca paper-trading integration (free signup)
]
```

---

## Path Helpers

**File:** `dipdiver/_paths.py` (lines 13–42)

### `repo_root() -> Path`
Walks up from `_paths.py` until it finds `pyproject.toml`. Used by YAML configs and scripts to stay portable.

```python
def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("could not find repo root (no pyproject.toml above this file)")
```

Raises `RuntimeError` if `pyproject.toml` is not found above the dipdiver package. **Never hard-code absolute paths in configs.**

### `data_root() -> Path`
Returns `<repo>/data/qlib` by default. Overridable via `DIPDIVER_DATA_ROOT` environment variable.

```python
def data_root() -> Path:
    override = os.environ.get("DIPDIVER_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "data" / "qlib"
```

### `resolve_provider_uri(raw: str | Path) -> Path`
Resolves Qlib `provider_uri` strings portably (lines 29–42):
- Absolute paths → returned as-is.
- Paths starting with `~` → expanded.
- Relative paths → joined to `repo_root()` so configs stay portable.

**Example:**
```python
resolve_provider_uri("data/qlib")  # → /repo/data/qlib
resolve_provider_uri("~/.qlib")    # → /home/user/.qlib
resolve_provider_uri("/abs/path")  # → /abs/path
```

---

## Tooling Configuration

**File:** `pyproject.toml` (lines 80–141)

### Ruff (linter, lines 80–96)
```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "C4", "RUF"]
ignore = ["E501"]  # line length handled by black
```
Checks: pycodestyle (E/W), pyflakes (F), isort (I), bugbear (B), pyupgrade (UP), simplify (SIM), comprehensions (C4), ruff-specific (RUF).

### Black (formatter, lines 101–103)
```toml
[tool.black]
line-length = 100
target-version = ["py311"]
```

### Mypy (type checker, lines 105–119)
**Strict mode on all new code** (lines 105–119):
```toml
[tool.mypy]
python_version = "3.11"
strict = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
```
Tests can be non-strict (line 119). Type hints are mandatory.

### Pytest (tests, lines 121–128)
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: requires external services (broker, LLM, data)",
]
```
Run with: `pytest --cov=dipdiver --cov-report=term-missing` (coverage included).

---

## CI/CD Workflows

**Location:** `.github/workflows/`

### `ci.yml` — Lint, type-check, test (lines 1–67)
Runs on every push to `main` and every PR to `main` (concurrency: cancel in-progress).

**Jobs:**
1. **lint** (ubuntu-latest, Python 3.11)
   - `ruff check .`
   - `black --check .`

2. **typecheck** (ubuntu-latest, Python 3.11)
   - `mypy` (strict, all files under `dipdiver/`)

3. **test** (ubuntu-latest, Python 3.11 + 3.12 matrix)
   - `pytest --cov=dipdiver --cov-report=term-missing`

All three jobs must pass before merge.

### `secret-scan.yml` — Gitleaks (lines 1–27)
Runs on every push/PR to `main` + weekly Monday 7am UTC.

```yaml
- uses: gitleaks/gitleaks-action@v2
```

**Enforce:** No `.env`, `.env.*` (except `.env.m2.example`), `credentials*.json`, `*.key`, `*.pem` are committed. Gitleaks scans full history.

---

## Secrets & Environment

**File:** `.env.m2.example` (lines 1–15)

Template for local `.env.m2` (never committed):
```bash
DEEPSEEK_API_KEY=sk-REPLACE_ME      # M2 factor proposer (default provider)
OPENAI_API_KEY=sk-REPLACE_ME         # M2 factor proposer (alt)
ALPACA_API_KEY=PK_REPLACE_ME         # M3 Alpaca paper trading
ALPACA_API_SECRET=REPLACE_ME         # M3 Alpaca paper trading
```

Scripts auto-load `.env.m2` from repo root; no shell sourcing needed.

**Gitignore rule** (`.gitignore` lines 41–49):
```
.env
.env.*
!.env.example
!.env.m2.example
secrets/
*.key
*.pem
credentials*.json
```

---

## Contributing & Governance

**File:** `CONTRIBUTING.md` (lines 1–101)

### Five Hard Rules (non-negotiable; PR-closing violations)

1. **No live-trading performance claims** without scoreboard rows (lines 22–23).
   - README/docs/code/commits must not say "works", "profitable", "outperforms" unless backed by public forward-eval results.

2. **No direct LLM-to-broker path** (lines 23).
   - All execution goes through Lean. LLMs propose and veto; they do not place orders.

3. **Anti-overfit rules enforced** (lines 24–25, see `docs/VALIDATION.md`).
   - Time fence, no retroactive re-training, no metric shopping, no survivorship, costs in, timestamp care.

4. **No vendored secrets** (lines 25).
   - API keys, broker tokens, LLM keys never committed. Gitleaks scans every PR; bypass → permanent ban.

5. **No bundled binary models** (lines 26).
   - Models trained from code + data reproducibly. Checkpoints pulled by hash, stored elsewhere.

### Contribution scope (lines 28–79)

**Welcome:**
- Bug fixes & small improvements
- New broker adapters (recipe in lines 39–46; highest-value contribution)
- New risk-veto agents (lines 48–53; must demonstrate non-negative veto-regret over ≥30 days)
- New strategies (lines 55–61; backtest + parity test required)
- Documentation

**Not welcome:**
- "Yet another LLM trading bot"
- Reimplementing Lean in Python
- Features that pull toward kitchen-sink trading frameworks

### Code style (lines 67–72)

- **Python:** ruff + black + mypy strict on new code.
- **Type hints:** Mandatory on all new code.
- **Comments:** Only if the *why* is non-obvious.
- **No emojis** in code or commit messages.

### Commit & PR conventions (lines 75–79)

- **Commits:** Imperative mood ("Add Zerodha adapter"), ≤72 chars, body explains *why*.
- **PRs:** One sentence title; description includes scope, tested/not tested, screenshots if user-visible.
- **Review:** Diffs >400 lines are split. Refactors ≠ features in same PR.
- **Reviewer time first:** Hard-to-review PRs sit until fixed.

### Special approval (lines 79)

PRs touching broker adapters, LLM-to-Lean boundary, or forward-eval harness require **two reviewers**.

### Security issues (lines 89–96)

Email maintainers directly, do not open public issues. Include:
- Anything that leaks broker credentials / API keys
- Anything letting LLM-injected strings reach the order path
- Anything bypassing validation gates
- Supply-chain concerns

---

## License

**File:** `LICENSE` (MIT, lines 1–22)

Copyright (c) 2026 DipDiver contributors.

**Clause:** Full MIT — free to use, modify, distribute; no warranty.

**Compatibility:** MIT compatible with upstream components:
- Qlib (MIT)
- RD-Agent (MIT)
- Lean (Apache-2.0)
- All others (see `STACK_DECISIONS.md` line 32 for rationale + evidence)

Component licenses preserved per their terms.

---

## Docs Reference

One-line pointers (read the full docs, don't copy from here):

- **[ARCHITECTURE.md](../docs/ARCHITECTURE.md)** — Six-layer stack diagram, design principles, data flow, parity requirements.
- **[ROADMAP.md](../docs/ROADMAP.md)** — Ten-week build sequence (M0–M5); acceptance criteria for each milestone.
- **[STACK_DECISIONS.md](../docs/STACK_DECISIONS.md)** — ADR-style per-layer choices, alternatives, validation evidence, open risks.
- **[VALIDATION.md](../docs/VALIDATION.md)** — Tiers of evidence (backtest → paper → live), anti-overfit rules, forward-eval harness spec.
- **[DISCLAIMER.md](../docs/DISCLAIMER.md)** — Legal / risk / regulatory (read before anything else).

---

## Dev Quickstart

**Installation:**
```bash
git clone https://github.com/thedumbstuff/DipDiver
cd DipDiver
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

**Checks:**
```bash
ruff check .          # lint
black --check .       # format check
mypy                  # type check (strict)
pytest                # tests + coverage
```

**For M1 brain work:**
```bash
python -m venv .venv_312 --python=python3.12
source .venv_312/bin/activate
pip install -e ".[brain,dev]"
python -m dipdiver.brain.baselines  # M1 baseline runner
```

**CI:** Same four checks run on every push/PR. Gitleaks runs weekly + on every PR.

---

## Cross-references

- [M1 Qlib Baseline](m1_baselines.md)
- [M2 LLM Factor Proposer](m2_lite.md)
- [M3 Execution](m3_execution.md)
- [M5 Risk Committee](m5_committee.md)
