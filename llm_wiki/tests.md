# Test Infrastructure and Coverage

## Purpose & Entry Points

The DipDiver test suite validates scaffolding—package structure, config/schema validation, and data pipeline logic—without requiring external dependencies like Qlib or live brokers. Tests run in CI on Python 3.11–3.12 with `pytest --cov=dipdiver --cov-report=term-missing`.

**Test root:** `/tests/`
**Invocation:** `pytest` (runs all tests)

---

## Test Organization

### Top-Level: Smoke Tests

**File:** `tests/test_smoke.py`

Two import-only tests ensuring the package and all subpackages load without errors:

```python
def test_package_imports() -> None:
    import dipdiver
    assert dipdiver.__version__ == "0.0.0"

def test_subpackages_import() -> None:
    for name in (
        "dipdiver.brain",
        "dipdiver.committee",
        "dipdiver.adapters",
        "dipdiver.adapters.lean",
        "dipdiver.harness",
        "dipdiver.brokers",
    ):
        importlib.import_module(name)
```

These catch import-time errors (circular imports, syntax, missing dependencies).

---

### M1 Baselines: Config, Data, and Results

**Directory:** `tests/brain/`
**Files:**
- `test_baselines.py` (230 lines, 27 tests)
- `test_data.py` (87 lines, 7 tests)

#### Coverage: `test_baselines.py`

**Universes** (4 tests):
- `test_dow30_has_30_instruments()` — Validates cardinality.
- `test_nifty50_has_50_instruments()` — Validates cardinality.
- `test_crypto_basket_has_3_instruments()` — Validates cardinality.
- `test_universes_have_unique_tickers()` — Ensures no duplicate instruments within a universe.

Source: `dipdiver.brain.baselines.universes` (lines 8–46 in test_baselines.py)

**Config validation** (5 tests):
- `test_config_hash_is_stable()` — Same inputs produce same hash.
- `test_config_hash_changes_with_seed()` — Hash changes when seed changes.
- `test_config_rejects_unknown_model()` — Rejects `model != {"lightgbm", "lstm"}`.
- `test_config_rejects_overlapping_windows()` — Enforces `train_end ≤ valid_start` and `valid_end ≤ test_start` (time-fence rule).
- `test_config_rejects_reversed_range()` — Rejects `start >= end` within any window.

Source: `dipdiver.brain.baselines.config` lines 41–54 (BaselineConfig.__post_init__)

**YAML config shipping** (6 parametrized tests):
- Loads all 6 shipped configs (dow30/nifty50/crypto × lightgbm/lstm) and ensures they have a non-empty hash.

Config files: `dipdiver/brain/baselines/configs/*.yaml`

**Runner (mocked Qlib)** (2 tests):
- `test_runner_assembles_result_from_metrics()` — Mocks `_run_qlib_workflow` to return synthetic metrics; verifies `run_baseline()` assembles result correctly and calculates `excess_return = annualised_return - benchmark_annualised_return`.
- `test_runner_calls_into_qlib_workflow()` — Without qlib installed, `run_baseline()` should raise ImportError; this confirms the lazy import in `_run_qlib_workflow()` is not stubbed.

Source: `dipdiver.brain.baselines.runner` (lines 59–85 in the module)

**Results: lock & compare** (5 tests):
- `test_compare_within_tolerance()` — Results within ±5% tolerance pass (fractional drift).
- `test_compare_outside_tolerance()` — Results >5% away fail.
- `test_compare_rejects_hash_mismatch()` — Comparing two different configs raises ValueError.
- `test_save_locked_round_trip()` — Save → read → JSON structure intact.
- `test_save_locked_refuses_overwrite()` — Second write to same path raises FileExistsError.

Source: `dipdiver.brain.baselines.results` (lines 47–89 in the module)

#### Coverage: `test_data.py`

Tests the M1 data pipeline without network or Qlib. Uses synthetic DataFrames to verify binary serialization and store verification.

**Dump & verify** (4 tests):
- `test_dump_writes_calendar_and_instruments()` — Writes calendar/day.txt, instruments/*.txt, and per-field .day.bin files for each instrument in the universe.
- `test_bin_file_has_uint32_header_and_float32_payload()` — Validates binary format: uint32 start index (little-endian) followed by float32 payload.
- `test_verify_reports_complete_store()` — Full dataset: `VerifyReport.ok=True`, instrument count matches, no missing instruments.
- `test_verify_flags_missing_instrument()` — Partial dataset: `VerifyReport.ok=False`, missing instruments listed.

Source: `dipdiver.brain.baselines.data` lines 172–251 (dump_to_qlib, verify_store)

**Error handling** (1 test):
- `test_verify_raises_when_store_absent()` — Missing directory raises FileNotFoundError.

---

### Untested Subsystems: Coverage Gaps

#### Zero Coverage

**`dipdiver.brain.m2.lite`** (factor proposer, LLM execution loop)
- `executor.py` — Converts LLM proposals into Qlib operations.
- `handler.py` — LLM response routing.
- `loop.py` — Main agent loop.
- `prompts.py` — Factor generation and veto prompts.
- `proposer.py` — LLM client and structured output.
- `schema.py` — Request/response dataclasses.

Reason: Requires OpenAI SDK, live LLM calls (or extensive mocking), and Qlib.

**`dipdiver.brain.m5.committee`** (risk-veto committee)
- `committee.py` — Agent orchestration, veto logic.
- `personas.py` — Individual agent implementations.
- `schema.py` — Committee domain models.

Reason: Requires running agents with live state transitions. Tested downstream in integration tests only.

**`dipdiver.brain.baselines._qlib`** (Qlib task builder, metrics extraction)
- `task.py` — Builds Qlib workflow config.
- `metrics.py` — Extracts metrics from Qlib recorder.
- `__init__.py` — Empty.

Reason: Requires Qlib installed and initialized. No unit tests; validation comes from `test_runner_calls_into_qlib_workflow()` (ImportError check) and real runs.

**`dipdiver.adapters.lean.signals`** (Lean signal adapter)
- Signal schema and portfolio proposal conversion.

Reason: Requires Lean execution engine (Docker). No unit tests.

**`dipdiver.adapters.alpaca`** (Alpaca broker adapter)
- `client.py` — Alpaca API wrapper.
- `strategy.py` — Order execution and state tracking.

Reason: Requires live or paper Alpaca account. No unit tests.

**`dipdiver.brain.m2.__init__`** (M2 barrel)
- Empty (only re-exports).

**`dipdiver.committee.__init__`**, **`dipdiver.harness.__init__`**, **`dipdiver.brokers.__init__`**
- All empty placeholder modules. No content to test.

**`dipdiver._paths.py`**
- Path resolution utilities.

Reason: Heavy filesystem dependency; not tested directly (tested implicitly via config loading paths).

---

## Test Execution and CI

### Running Tests Locally

```bash
# All tests
pytest

# Specific test file
pytest tests/brain/test_baselines.py

# With coverage report
pytest --cov=dipdiver --cov-report=term-missing

# Specific test by name
pytest tests/brain/test_baselines.py::test_config_hash_is_stable

# Skip slow/integration tests (if markers are set)
pytest -m "not slow"
```

### CI Pipeline

**File:** `.github/workflows/ci.yml`

Runs on push and PR to `main`:
1. **Lint** (ruff + black) — Python 3.11
2. **Typecheck** (mypy strict) — Python 3.11
3. **Test** (pytest + coverage) — Python 3.11 and 3.12 in parallel

The test job runs:
```bash
pip install -e ".[dev]"
pytest --cov=dipdiver --cov-report=term-missing
```

No coverage threshold is enforced in CI; coverage is reported per-run.

---

## Test Configuration

**pytest config:** `pyproject.toml` [tool.pytest.ini_options]
```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: requires external services (broker, LLM, data)",
]
```

**Coverage config:** `pyproject.toml` [tool.coverage.run]
```toml
[tool.coverage.run]
branch = true
source = ["dipdiver"]
omit = ["tests/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
]
```

No tests are currently marked `@pytest.mark.slow` or `@pytest.mark.integration`; they will be added as M2 and M3 tests are implemented.

---

## Test Dependencies

**Required** (via `.[dev]`):
- `pytest>=8.0` — Test runner.
- `pytest-cov>=5.0` — Coverage reporting.
- `pyyaml>=6` — Config loading in tests.

**Optional** (imported with `pytest.importorskip()`):
- `pandas` — Used in `test_data.py`; tests skip gracefully if absent.
- `numpy` — Used in `test_data.py`; tests skip gracefully if absent.

**Not required**:
- `pyqlib` — Tests work without it (checked via ImportError in `test_runner_calls_into_qlib_workflow()`).
- `openai` — M2 tests do not exist yet.
- `alpaca-py`, `lean` — M3 tests do not exist yet.

---

## Known Limitations & Future Work

1. **No M2 (factor proposer) tests:** Requires OpenAI SDK and mock LLM responses. Planned after M2 API stabilizes.

2. **No M3 (Lean execution) tests:** Requires Docker-based Lean engine or Alpaca paper account. Tests will use `@pytest.mark.integration` and be skipped in CI by default.

3. **No M5 (committee) tests:** Requires full agent orchestration. Tests will be integration-only, validating veto logic on synthetic trades.

4. **No parity test (Qlib↔Lean):** Validates that a Qlib backtest and Lean backtest on the same config produce the same P&L. Deferred to M3.

5. **Qlib metrics extraction untested:** `dipdiver.brain.baselines._qlib.metrics.extract_metrics()` is only validated by live Qlib runs. No unit test exists; consider adding a synthetic recorder fixture if Qlib's internals stabilize.

6. **Config loading path untested:** `resolve_provider_uri()` in `_paths.py` is only indirectly tested via `load_config()` + runner. No direct unit test.

---

## Coverage Summary

| Module | Tests | Status |
|--------|-------|--------|
| `dipdiver.brain.baselines.universes` | 4 | ✓ Covered |
| `dipdiver.brain.baselines.config` | 5 + 6 parametrized | ✓ Covered |
| `dipdiver.brain.baselines.runner` | 2 (mocked qlib) | ✓ Covered (qlib-free) |
| `dipdiver.brain.baselines.results` | 5 | ✓ Covered |
| `dipdiver.brain.baselines.data` | 5 | ✓ Covered (pandas-optional) |
| `dipdiver.brain.baselines._qlib` | 0 | ✗ No unit tests (qlib required) |
| `dipdiver.brain.m2.lite.*` | 0 | ✗ No tests (LLM integration) |
| `dipdiver.brain.m5.*` | 0 | ✗ No tests (agent orchestration) |
| `dipdiver.adapters.lean` | 0 | ✗ No tests (Lean integration) |
| `dipdiver.adapters.alpaca` | 0 | ✗ No tests (broker integration) |
| `dipdiver.committee.*` | 0 | ✗ No tests (placeholder) |
| `dipdiver.harness.*` | 0 | ✗ No tests (placeholder) |
| `dipdiver.brokers.*` | 0 | ✗ No tests (placeholder) |

---

## How to Contribute Tests

Per `CONTRIBUTING.md`:
- **All behaviour changes require tests.** Bug fixes, config additions, universe changes must include a test.
- **Type hints required** (mypy strict on new code).
- **Separate test from refactor:** A PR that refactors M1 core code and adds M2 tests will be split.

When adding tests:
1. Use `pytest.importorskip()` for optional dependencies (pandas, qlib, openai).
2. Mock external calls (qlib, LLM, brokers). See `test_runner_assembles_result_from_metrics()` for the pattern.
3. Use fixtures sparingly; prefer parameterization for config variants.
4. Keep test names descriptive: `test_<function>_<condition>_<expected>`.
5. Document non-obvious setup in docstrings.

---

## Cross-references

- [M1 Baselines](m1_baselines.md) — What the baseline tests validate.
- [M2-lite](m2_lite.md) — Future test surface for the LLM factor proposer.
- [M3 execution](m3_execution.md) — Future integration tests for order execution.
- [Architecture](../docs/ARCHITECTURE.md) — Subsystem boundaries where tests live.
- [Validation](validation.md) — Forward-eval harness and acceptance gates.
