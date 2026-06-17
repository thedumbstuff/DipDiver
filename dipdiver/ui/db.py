"""SQLite cache for UI state. JSONL stays canonical for the scoreboard;
this DB holds: schedule entries, job execution logs, kill-switch state,
audit trail for config edits.

Schema is intentionally small — we never put trade or P&L data here; that's
the JSONL's job. Kill the DB and the UI still works (just loses the job
history).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from dipdiver._paths import ui_db_path


class Base(DeclarativeBase):
    pass


class ScheduleEntry(Base):
    """One scheduled job. APScheduler reads from this on boot to register
    jobs; edits via /schedule update the row + re-register the job."""

    __tablename__ = "schedule_entries"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    job_id: str = Column(String(64), unique=True, nullable=False)  # type: ignore[assignment]
    cron: str = Column(String(128), nullable=False)  # type: ignore[assignment]
    enabled: bool = Column(Boolean, default=True, nullable=False)  # type: ignore[assignment]
    description: str = Column(String(256), default="", nullable=False)  # type: ignore[assignment]
    last_modified_utc: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )  # type: ignore[assignment]


class JobLog(Base):
    """One row per job execution. Surfaced on /health and used for alerting."""

    __tablename__ = "job_logs"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    job_id: str = Column(String(64), nullable=False, index=True)  # type: ignore[assignment]
    started_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    finished_utc: datetime | None = Column(DateTime(timezone=True), nullable=True)  # type: ignore[assignment]
    status: str = Column(String(16), nullable=False)  # success / error / running
    exit_code: int | None = Column(Integer, nullable=True)  # type: ignore[assignment]
    summary: str = Column(Text, default="", nullable=False)  # human-readable result
    error: str | None = Column(Text, nullable=True)  # populated on failure
    triggered_by: str = Column(String(32), default="scheduler", nullable=False)
    # scheduler | manual | retry


class KillSwitchEvent(Base):
    """When the operator hits the kill-switch. Append-only audit."""

    __tablename__ = "kill_switch_events"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    triggered_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    reason: str = Column(Text, nullable=False)
    actor: str = Column(String(64), default="operator", nullable=False)
    actions_taken: str = Column(Text, default="", nullable=False)
    status: str = Column(String(16), default="initiated", nullable=False)
    # initiated | succeeded | partial | failed


class ConfigAudit(Base):
    """One row per /config save. Cheap audit; full history elsewhere."""

    __tablename__ = "config_audit"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    saved_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    actor: str = Column(String(64), default="operator", nullable=False)
    diff_summary: str = Column(Text, default="", nullable=False)


# ---------------------------------------------------------------------------
# QW11 — operator notes pinned to specific decisions
# ---------------------------------------------------------------------------


class DecisionNote(Base):
    """Persistent operator memory attached to a (date, universe, symbol) tuple."""

    __tablename__ = "decision_notes"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    date: str = Column(String(10), nullable=False, index=True)  # type: ignore[assignment]
    universe: str = Column(String(32), nullable=False)  # type: ignore[assignment]
    symbol: str = Column(String(16), nullable=False, index=True)  # type: ignore[assignment]
    note: str = Column(Text, nullable=False)
    created_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    actor: str = Column(String(64), default="operator", nullable=False)


# ---------------------------------------------------------------------------
# Stage 5 (M12) — feedback / override / execution loop
# ---------------------------------------------------------------------------


class UserFeedback(Base):
    """Thumbs-up/down on a symbol-day. Used by /picks rank penalty."""

    __tablename__ = "user_feedback"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    date: str = Column(String(10), nullable=False, index=True)  # type: ignore[assignment]
    universe: str = Column(String(32), nullable=False)  # type: ignore[assignment]
    symbol: str = Column(String(16), nullable=False, index=True)  # type: ignore[assignment]
    rating: int = Column(Integer, nullable=False)  # -1, 0, 1
    notes: str = Column(Text, default="", nullable=False)
    created_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    actor: str = Column(String(64), default="operator", nullable=False)


class OverrideDecision(Base):
    """Operator overrides a committee verdict. `reason` is required at the API layer."""

    __tablename__ = "override_decisions"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    date: str = Column(String(10), nullable=False, index=True)  # type: ignore[assignment]
    universe: str = Column(String(32), nullable=False)  # type: ignore[assignment]
    symbol: str = Column(String(16), nullable=False, index=True)  # type: ignore[assignment]
    original_decision: str = Column(String(16), nullable=False)  # approved/vetoed
    new_decision: str = Column(String(16), nullable=False)
    reason: str = Column(Text, nullable=False)
    created_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    actor: str = Column(String(64), default="operator", nullable=False)


class ExecutionRecord(Base):
    """Did the operator actually take the trade, skip, or partial?"""

    __tablename__ = "execution_records"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    date: str = Column(String(10), nullable=False, index=True)  # type: ignore[assignment]
    universe: str = Column(String(32), nullable=False)  # type: ignore[assignment]
    symbol: str = Column(String(16), nullable=False, index=True)  # type: ignore[assignment]
    decision: str = Column(String(16), nullable=False)  # approved/vetoed
    user_action: str = Column(String(16), nullable=False)  # taken/skipped/partial
    fraction_taken: float | None = Column(Float, nullable=True)  # type: ignore[assignment]
    notes: str = Column(Text, default="", nullable=False)
    created_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    actor: str = Column(String(64), default="operator", nullable=False)


class WatchlistEntry(Base):
    """Symbols the operator wants surfaced on /picks even outside top-N."""

    __tablename__ = "watchlist"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    symbol: str = Column(String(16), nullable=False, index=True)  # type: ignore[assignment]
    universe: str = Column(String(32), nullable=False)  # type: ignore[assignment]
    notes: str = Column(Text, default="", nullable=False)
    added_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    actor: str = Column(String(64), default="operator", nullable=False)


# ---------------------------------------------------------------------------
# Stage 3 (M10) — model versioning
# ---------------------------------------------------------------------------


class ModelVersion(Base):
    """One row per M1 retrain. Latest `locked` per config drives current picks."""

    __tablename__ = "model_versions"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    config_name: str = Column(String(128), nullable=False, index=True)  # type: ignore[assignment]
    config_hash: str = Column(String(64), nullable=False)  # type: ignore[assignment]
    locked_on_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    train_start: str = Column(String(10), nullable=False)  # type: ignore[assignment]
    train_end: str = Column(String(10), nullable=False)  # type: ignore[assignment]
    test_start: str = Column(String(10), nullable=False)  # type: ignore[assignment]
    test_end: str = Column(String(10), nullable=False)  # type: ignore[assignment]
    sharpe: float = Column(Float, default=0.0)
    max_dd: float = Column(Float, default=0.0)
    hit_rate: float = Column(Float, default=0.0)
    psr: float = Column(Float, default=0.0)  # Probabilistic Sharpe Ratio (confidence Sharpe>0)
    status: str = Column(String(16), default="candidate", nullable=False)
    # candidate | locked | superseded | rejected
    notes: str = Column(Text, default="", nullable=False)


# ---------------------------------------------------------------------------
# Stage 4 (M11) — live-gate audit
# ---------------------------------------------------------------------------


class LiveGateAudit(Base):
    """One row every time the live-trading gate is evaluated."""

    __tablename__ = "live_gate_audit"

    id: int = Column(Integer, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    evaluated_utc: datetime = Column(DateTime(timezone=True), nullable=False)  # type: ignore[assignment]
    strategy_id: str = Column(String(64), nullable=False, index=True)  # type: ignore[assignment]
    passed: bool = Column(Boolean, nullable=False)  # type: ignore[assignment]
    criteria_json: str = Column(Text, default="[]", nullable=False)
    invoked_by: str = Column(String(32), default="cli", nullable=False)


# ---------------------------------------------------------------------------
# Engine + session helpers
# ---------------------------------------------------------------------------


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(path: Path | None = None):
    global _engine, _SessionLocal
    if _engine is None:
        target = path or ui_db_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{target}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(_engine)
        _migrate_schema(_engine)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


# Columns added after a DB may already exist on disk. sqlite's create_all never
# ALTERs an existing table, so new (nullable / defaulted) columns are added by
# hand here. Idempotent: each ADD COLUMN runs only when the column is missing.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "model_versions": [("psr", "FLOAT DEFAULT 0.0")],
}


def _migrate_schema(engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            if table not in tables:
                continue  # create_all already made it with every current column
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db(path: Path | None = None) -> None:
    """Create tables if they don't exist. Idempotent."""
    get_engine(path)


@contextmanager
def session() -> Iterator[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
