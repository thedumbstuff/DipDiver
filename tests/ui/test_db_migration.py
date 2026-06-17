"""Schema migration: an existing ui.sqlite predating the `psr` column must gain
it via ALTER TABLE on init (create_all never ALTERs an existing table), without
losing rows, and idempotently.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import dipdiver.ui.db as db


def _make_old_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE model_versions (
          id INTEGER PRIMARY KEY, config_name TEXT, config_hash TEXT, locked_on_utc TEXT,
          train_start TEXT, train_end TEXT, test_start TEXT, test_end TEXT,
          sharpe REAL, max_dd REAL, hit_rate REAL, status TEXT, notes TEXT)"""
    )
    con.execute(
        "INSERT INTO model_versions (config_name, status, sharpe) VALUES ('old_x','locked',1.1)"
    )
    con.commit()
    con.close()


def _cols(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(model_versions)")}
    finally:
        con.close()


def test_psr_column_added_to_existing_db(tmp_path, monkeypatch):
    dbp = tmp_path / "ui.sqlite"
    _make_old_db(dbp)
    assert "psr" not in _cols(dbp)

    # Reset module-level engine so get_engine re-initialises against our file.
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_SessionLocal", None)
    db.get_engine(dbp)

    assert "psr" in _cols(dbp)
    with db.session() as s:
        row = s.query(db.ModelVersion).filter_by(config_name="old_x").one()
        assert row.sharpe == 1.1 and row.status == "locked"
        assert row.psr == 0.0  # defaulted for the pre-existing row

    # Idempotent: a second init must not raise.
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_SessionLocal", None)
    db.get_engine(dbp)
    assert "psr" in _cols(dbp)
