"""Local SQLite cache for price history and slow metadata.

Rationale: free data sources are rate limited and flaky. Caching means a rescan
costs one incremental fetch instead of hammering the provider, and the dashboard
still works if a source goes down mid-session.

Stored as real columns rather than pickled blobs so the cache stays inspectable
with any SQLite client and is not a deserialisation risk.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from .config import CACHE_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol ON ohlcv(symbol);

CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Cache:
    """Thread-safe SQLite wrapper. One connection per thread."""

    def __init__(self, path: Path | None = None) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or (CACHE_DIR / "analyser.db")
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ---------------------------------------------------------------- OHLCV

    def put_ohlcv(self, symbol: str, df: pd.DataFrame) -> int:
        """Upsert a symbol's daily bars. Expects columns open/high/low/close/volume."""
        if df is None or df.empty:
            return 0
        frame = df.copy()
        frame.columns = [str(c).lower() for c in frame.columns]
        needed = ["open", "high", "low", "close", "volume"]
        for col in needed:
            if col not in frame.columns:
                return 0
        frame = frame[needed]

        # Drop rows with no usable close. yfinance sometimes emits a partial
        # trailing row with volume but NaN prices; that row would corrupt every
        # indicator downstream.
        frame = frame.dropna(subset=["close"])
        if frame.empty:
            return 0

        idx = pd.to_datetime(frame.index, errors="coerce")
        frame = frame.loc[~idx.isna()]
        if frame.empty:
            return 0
        dates = pd.DatetimeIndex(pd.to_datetime(frame.index)).tz_localize(None).strftime("%Y-%m-%d")

        rows = [
            (
                symbol,
                d,
                _f(r.open),
                _f(r.high),
                _f(r.low),
                _f(r.close),
                _f(r.volume),
            )
            for d, r in zip(dates, frame.itertuples(index=False), strict=True)
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO ohlcv (symbol,date,open,high,low,close,volume) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol,date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, "
                "close=excluded.close, volume=excluded.volume",
                rows,
            )
        return len(rows)

    def get_ohlcv(self, symbol: str, min_rows: int = 0) -> pd.DataFrame | None:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT date,open,high,low,close,volume FROM ohlcv "
                "WHERE symbol=? ORDER BY date ASC",
                (symbol,),
            )
            rows = cur.fetchall()
        if not rows or len(rows) < min_rows:
            return None
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        return df.dropna(subset=["close"])

    def last_ohlcv_date(self, symbol: str) -> str | None:
        with self._conn() as conn:
            cur = conn.execute("SELECT MAX(date) FROM ohlcv WHERE symbol=?", (symbol,))
            row = cur.fetchone()
        return row[0] if row and row[0] else None

    def symbols_needing_refresh(self, symbols: list[str], stale_after_days: int = 1) -> list[str]:
        """Symbols with no data or data older than the cutoff."""
        if not symbols:
            return []
        placeholders = ",".join("?" * len(symbols))
        with self._conn() as conn:
            cur = conn.execute(
                f"SELECT symbol, MAX(date) FROM ohlcv WHERE symbol IN ({placeholders}) "
                "GROUP BY symbol",
                symbols,
            )
            latest = dict(cur.fetchall())
        cutoff = (datetime.now(UTC).date() - timedelta(days=stale_after_days)).isoformat()
        return [s for s in symbols if latest.get(s) is None or latest[s] < cutoff]

    # ----------------------------------------------------------------- meta

    def put_meta(self, key: str, value: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta (key,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value, default=str), datetime.now(UTC).isoformat()),
            )

    def get_meta(self, key: str, ttl_minutes: int | None = None) -> Any | None:
        with self._conn() as conn:
            cur = conn.execute("SELECT value, updated_at FROM meta WHERE key=?", (key,))
            row = cur.fetchone()
        if not row:
            return None
        value, updated_at = row
        if ttl_minutes is not None:
            try:
                ts = datetime.fromisoformat(updated_at)
            except ValueError:
                return None
            if datetime.now(UTC) - ts > timedelta(minutes=ttl_minutes):
                return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def meta_age_minutes(self, key: str) -> float | None:
        with self._conn() as conn:
            cur = conn.execute("SELECT updated_at FROM meta WHERE key=?", (key,))
            row = cur.fetchone()
        if not row:
            return None
        try:
            ts = datetime.fromisoformat(row[0])
        except ValueError:
            return None
        return (datetime.now(UTC) - ts).total_seconds() / 60.0

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            bars = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
            syms = conn.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv").fetchone()[0]
        return {"bars": bars, "symbols": syms}


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f
