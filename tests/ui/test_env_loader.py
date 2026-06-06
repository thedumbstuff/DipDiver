"""Tests for the .env.m2 / .env.m2.example auto-loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dipdiver.ui import env_loader


def _write_env(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture(autouse=True)
def isolate_environ(monkeypatch):
    """Each test gets a clean view of the keys we touch."""
    for k in ("ALPACA_API_KEY", "ALPACA_API_SECRET",
              "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    env_loader.reset_for_test()
    yield


def test_loads_from_env_m2_when_present(tmp_path: Path):
    m2 = tmp_path / ".env.m2"
    _write_env(m2, "ALPACA_API_KEY=PKreal\nALPACA_API_SECRET=secret_real\n")
    report = env_loader.load_env_files(candidates=(m2,))
    assert os.environ["ALPACA_API_KEY"] == "PKreal"
    assert os.environ["ALPACA_API_SECRET"] == "secret_real"
    assert "ALPACA_API_KEY" in report.vars_set
    assert str(m2) in report.files_loaded


def test_falls_back_to_example_when_m2_missing(tmp_path: Path):
    """User's stated workflow: only .env.m2.example has the real keys locally."""
    example = tmp_path / ".env.m2.example"
    _write_env(example, "ALPACA_API_KEY=PKkeyFromExample\nALPACA_API_SECRET=secretFromExample\n")
    m2 = tmp_path / ".env.m2"  # does not exist
    report = env_loader.load_env_files(candidates=(m2, example))
    assert os.environ["ALPACA_API_KEY"] == "PKkeyFromExample"
    assert "ALPACA_API_KEY" in report.vars_set
    assert str(m2) not in report.files_loaded
    assert str(example) in report.files_loaded


def test_real_m2_wins_over_example(tmp_path: Path):
    """When both exist, .env.m2 is authoritative — example never overrides it."""
    m2 = tmp_path / ".env.m2"
    example = tmp_path / ".env.m2.example"
    _write_env(m2, "ALPACA_API_KEY=PK_real\n")
    _write_env(example, "ALPACA_API_KEY=PK_example_shouldnt_win\n")
    env_loader.load_env_files(candidates=(m2, example))
    assert os.environ["ALPACA_API_KEY"] == "PK_real"


def test_existing_environ_is_never_overwritten(tmp_path: Path, monkeypatch):
    """Production deploy: env var set in systemd unit wins over .env files."""
    monkeypatch.setenv("ALPACA_API_KEY", "PK_from_systemd")
    m2 = tmp_path / ".env.m2"
    _write_env(m2, "ALPACA_API_KEY=PK_from_file_shouldnt_win\n")
    report = env_loader.load_env_files(candidates=(m2,))
    assert os.environ["ALPACA_API_KEY"] == "PK_from_systemd"
    assert "ALPACA_API_KEY" in report.vars_skipped_already_set


def test_placeholder_values_are_skipped(tmp_path: Path):
    """The committed .env.m2.example has PK_REPLACE_ME etc. — must not load them."""
    example = tmp_path / ".env.m2.example"
    _write_env(example, "ALPACA_API_KEY=PK_REPLACE_ME\nALPACA_API_SECRET=REPLACE_ME\n")
    report = env_loader.load_env_files(candidates=(example,))
    assert "ALPACA_API_KEY" not in os.environ
    assert "ALPACA_API_KEY" in report.vars_skipped_placeholder
    assert "ALPACA_API_SECRET" in report.vars_skipped_placeholder


def test_missing_files_are_silently_ok(tmp_path: Path):
    """No .env files anywhere → no crash, just an empty report."""
    nonexistent = tmp_path / "nope.env"
    report = env_loader.load_env_files(candidates=(nonexistent,))
    assert report.files_loaded == ()
    assert report.vars_set == ()


def test_quoted_and_commented_lines_handled(tmp_path: Path):
    m2 = tmp_path / ".env.m2"
    _write_env(m2, """
# a comment
ALPACA_API_KEY="PKquoted"
ALPACA_API_SECRET='single_quoted'

DEEPSEEK_API_KEY=plain
""")
    env_loader.load_env_files(candidates=(m2,))
    assert os.environ["ALPACA_API_KEY"] == "PKquoted"
    assert os.environ["ALPACA_API_SECRET"] == "single_quoted"
    assert os.environ["DEEPSEEK_API_KEY"] == "plain"


def test_default_candidates_check_both_files(tmp_path: Path, monkeypatch):
    """Calling load_env_files() with no args defaults to .env.m2 + .env.m2.example under repo_root()."""
    monkeypatch.setattr(env_loader, "repo_root", lambda: tmp_path)
    _write_env(tmp_path / ".env.m2.example",
               "ALPACA_API_KEY=fallback_value\n")
    report = env_loader.load_env_files()
    assert os.environ["ALPACA_API_KEY"] == "fallback_value"
    assert any(".env.m2.example" in f for f in report.files_loaded)


def test_health_route_surfaces_env_info(client, data_root, monkeypatch, tmp_path):
    """When /health renders, the operator can see which file was loaded.

    NOTE: app.py's module-level env_loader.load_env_files() ran BEFORE us with
    the real repo_root and the developer's actual .env.m2, populating
    os.environ. We re-clear those vars here and then re-run the loader against
    a synthesised tmp_path .env.m2.example so the assertion about "loaded from
    example" is meaningful.
    """
    for k in ("ALPACA_API_KEY", "ALPACA_API_SECRET",
              "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(env_loader, "repo_root", lambda: tmp_path)
    _write_env(tmp_path / ".env.m2.example",
               "ALPACA_API_KEY=PKfromExample\nALPACA_API_SECRET=secretFromExample\n")
    env_loader.reset_for_test()
    report = env_loader.load_env_files()
    assert ".env.m2.example" in "".join(report.files_loaded)
    assert "ALPACA_API_KEY" in report.vars_set

    r = client.get("/health")
    assert r.status_code == 200
    body = r.text
    assert "Env loader checked" in body or ".env.m2.example" in body
