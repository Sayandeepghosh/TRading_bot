"""Data source contract.

Every source implements this. The registry then composes them with fallback, so
adding Angel One SmartAPI later (free, real live ticks) means writing one class
and touching nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(slots=True)
class UniverseMember:
    symbol: str          # NSE symbol, e.g. "RELIANCE"
    company: str
    sector: str


@dataclass(slots=True)
class SourceStatus:
    name: str
    ok: bool
    detail: str


@runtime_checkable
class PriceSource(Protocol):
    """Supplies daily OHLCV history."""

    name: str

    def fetch_history(self, symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
        """Return {symbol: DataFrame(index=date, cols=open/high/low/close/volume)}.

        Partial success is expected and fine: return what you got. Missing
        symbols are handled by the registry falling through to the next source.
        """
        ...


@runtime_checkable
class UniverseSource(Protocol):
    """Supplies the list of stocks to scan, with sector labels."""

    name: str

    def fetch_universe(self, index: str) -> list[UniverseMember]: ...


@runtime_checkable
class FundamentalSource(Protocol):
    """Supplies per-company fundamentals. Slow, so used sparingly."""

    name: str

    def fetch_fundamentals(self, symbols: list[str]) -> dict[str, dict]: ...


@runtime_checkable
class MarketSource(Protocol):
    """Supplies index-level context: level, breadth, open/closed."""

    name: str

    def fetch_market_snapshot(self) -> dict: ...


def to_yahoo(symbol: str) -> str:
    """NSE symbol -> Yahoo ticker. RELIANCE -> RELIANCE.NS"""
    s = symbol.strip().upper()
    return s if s.endswith((".NS", ".BO")) else f"{s}.NS"


def from_yahoo(ticker: str) -> str:
    """Yahoo ticker -> NSE symbol. RELIANCE.NS -> RELIANCE"""
    t = ticker.strip().upper()
    for suf in (".NS", ".BO"):
        if t.endswith(suf):
            return t[: -len(suf)]
    return t
