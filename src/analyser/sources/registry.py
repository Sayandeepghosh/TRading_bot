"""Multi-source data registry.

Composes every available free source with fallback and caching:

  universe      NSE constituent CSV  -> bundled static list
  price history cache (fresh)        -> Yahoo  -> cache (stale, flagged)
  fundamentals  Yahoo
  market ctx    NSE snapshot         -> Yahoo index history

No single source is trusted to be up. Each request degrades rather than fails,
and the dashboard reports which sources answered so you can see when you are
looking at stale numbers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Callable

import pandas as pd

from ..cache import Cache
from ..config import AppConfig
from .base import SourceStatus, UniverseMember
from .nse import NseSource
from .yahoo import YahooSource

log = logging.getLogger(__name__)

# Fallback universe so the tool still runs if NSE is unreachable.
# Large, liquid names only. Sector labels are approximate here; the NSE CSV
# supplies authoritative ones when reachable.
_FALLBACK_NIFTY50: list[tuple[str, str, str]] = [
    ("RELIANCE", "Reliance Industries Ltd.", "Oil Gas & Consumable Fuels"),
    ("HDFCBANK", "HDFC Bank Ltd.", "Financial Services"),
    ("ICICIBANK", "ICICI Bank Ltd.", "Financial Services"),
    ("INFY", "Infosys Ltd.", "Information Technology"),
    ("TCS", "Tata Consultancy Services Ltd.", "Information Technology"),
    ("BHARTIARTL", "Bharti Airtel Ltd.", "Telecommunication"),
    ("ITC", "ITC Ltd.", "Fast Moving Consumer Goods"),
    ("LT", "Larsen & Toubro Ltd.", "Construction"),
    ("SBIN", "State Bank of India", "Financial Services"),
    ("AXISBANK", "Axis Bank Ltd.", "Financial Services"),
    ("KOTAKBANK", "Kotak Mahindra Bank Ltd.", "Financial Services"),
    ("HINDUNILVR", "Hindustan Unilever Ltd.", "Fast Moving Consumer Goods"),
    ("MARUTI", "Maruti Suzuki India Ltd.", "Automobile"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Ltd.", "Healthcare"),
    ("TATAMOTORS", "Tata Motors Ltd.", "Automobile"),
    ("M&M", "Mahindra & Mahindra Ltd.", "Automobile"),
    ("NTPC", "NTPC Ltd.", "Power"),
    ("POWERGRID", "Power Grid Corporation of India Ltd.", "Power"),
    ("ULTRACEMCO", "UltraTech Cement Ltd.", "Construction Materials"),
    ("ASIANPAINT", "Asian Paints Ltd.", "Consumer Durables"),
    ("TITAN", "Titan Company Ltd.", "Consumer Durables"),
    ("BAJFINANCE", "Bajaj Finance Ltd.", "Financial Services"),
    ("WIPRO", "Wipro Ltd.", "Information Technology"),
    ("HCLTECH", "HCL Technologies Ltd.", "Information Technology"),
    ("TATASTEEL", "Tata Steel Ltd.", "Metals & Mining"),
    ("JSWSTEEL", "JSW Steel Ltd.", "Metals & Mining"),
    ("COALINDIA", "Coal India Ltd.", "Oil Gas & Consumable Fuels"),
    ("ONGC", "Oil & Natural Gas Corporation Ltd.", "Oil Gas & Consumable Fuels"),
    ("NESTLEIND", "Nestle India Ltd.", "Fast Moving Consumer Goods"),
    ("GRASIM", "Grasim Industries Ltd.", "Construction Materials"),
    ("CIPLA", "Cipla Ltd.", "Healthcare"),
    ("DRREDDY", "Dr. Reddy's Laboratories Ltd.", "Healthcare"),
    ("EICHERMOT", "Eicher Motors Ltd.", "Automobile"),
    ("TECHM", "Tech Mahindra Ltd.", "Information Technology"),
    ("ADANIENT", "Adani Enterprises Ltd.", "Metals & Mining"),
    ("ADANIPORTS", "Adani Ports and Special Economic Zone Ltd.", "Services"),
    ("BAJAJFINSV", "Bajaj Finserv Ltd.", "Financial Services"),
    ("HINDALCO", "Hindalco Industries Ltd.", "Metals & Mining"),
    ("INDUSINDBK", "IndusInd Bank Ltd.", "Financial Services"),
    ("TATACONSUM", "Tata Consumer Products Ltd.", "Fast Moving Consumer Goods"),
    ("BRITANNIA", "Britannia Industries Ltd.", "Fast Moving Consumer Goods"),
    ("APOLLOHOSP", "Apollo Hospitals Enterprise Ltd.", "Healthcare"),
    ("DIVISLAB", "Divi's Laboratories Ltd.", "Healthcare"),
    ("HEROMOTOCO", "Hero MotoCorp Ltd.", "Automobile"),
    ("BAJAJ-AUTO", "Bajaj Auto Ltd.", "Automobile"),
    ("SBILIFE", "SBI Life Insurance Company Ltd.", "Financial Services"),
    ("HDFCLIFE", "HDFC Life Insurance Company Ltd.", "Financial Services"),
    ("SHRIRAMFIN", "Shriram Finance Ltd.", "Financial Services"),
    ("TRENT", "Trent Ltd.", "Consumer Services"),
    ("JIOFIN", "Jio Financial Services Ltd.", "Financial Services"),
]


class DataRegistry:
    """Single entry point for all market data."""

    def __init__(self, cfg: AppConfig, cache: Cache | None = None) -> None:
        self.cfg = cfg
        self.cache = cache or Cache()
        self.nse = NseSource()
        self.yahoo = YahooSource(batch_size=cfg.data.batch_size)
        self._status: dict[str, SourceStatus] = {}

    # ------------------------------------------------------------- universe

    def universe(self, force_refresh: bool = False) -> list[UniverseMember]:
        index = self.cfg.universe.index
        key = f"universe:{index}"

        if not force_refresh:
            cached = self.cache.get_meta(key, ttl_minutes=60 * 24 * 7)
            if cached:
                self._status["universe"] = SourceStatus(
                    "NSE (cached)", True, f"{len(cached)} names"
                )
                return self._filter([UniverseMember(**m) for m in cached])

        members = self.nse.fetch_universe(index)
        self._status["universe"] = self.nse.last_status

        if not members:
            log.warning("NSE universe unavailable; using bundled fallback list")
            members = [
                UniverseMember(symbol=s, company=c, sector=sec)
                for s, c, sec in _FALLBACK_NIFTY50
            ]
            self._status["universe"] = SourceStatus(
                "Bundled fallback", True, f"{len(members)} names (NSE unreachable)"
            )
        else:
            self.cache.put_meta(
                key,
                [{"symbol": m.symbol, "company": m.company, "sector": m.sector} for m in members],
            )

        return self._filter(members)

    def _filter(self, members: list[UniverseMember]) -> list[UniverseMember]:
        excl = set(self.cfg.universe.exclude)
        seen: set[str] = set()
        out: list[UniverseMember] = []
        for m in members:
            if m.symbol in excl or m.symbol in seen:
                continue
            seen.add(m.symbol)
            out.append(m)
        return out

    # -------------------------------------------------------------- history

    def history(
        self,
        symbols: list[str],
        force_refresh: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[dict[str, pd.DataFrame], list[str]]:
        """Daily OHLCV per symbol, plus the list that could not be resolved."""
        days = self.cfg.data.history_days
        min_rows = max(60, self.cfg.indicators.sma_slow // 2)

        if force_refresh:
            stale = list(symbols)
        else:
            # Weekends and holidays mean "yesterday" is often the newest bar
            # that exists, so allow up to 4 days before calling data stale.
            stale = self.cache.symbols_needing_refresh(symbols, stale_after_days=4)

        if stale:
            log.info("Fetching history for %d/%d symbols", len(stale), len(symbols))
            fetched = self.yahoo.fetch_history(stale, days, progress=progress)
            self._status["history"] = self.yahoo.last_status
            for sym, df in fetched.items():
                self.cache.put_ohlcv(sym, df)
        else:
            self._status["history"] = SourceStatus("Cache", True, "all symbols fresh")
            if progress:
                progress(len(symbols), len(symbols))

        out: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for sym in symbols:
            df = self.cache.get_ohlcv(sym, min_rows=min_rows)
            if df is None or df.empty:
                missing.append(sym)
            else:
                out[sym] = df
        return out, missing

    # ---------------------------------------------------------- fundamentals

    def fundamentals(
        self, symbols: list[str], progress: Callable[[int, int], None] | None = None
    ) -> dict[str, dict]:
        """Cached per symbol for a day, since these barely move."""
        out: dict[str, dict] = {}
        to_fetch: list[str] = []
        for sym in symbols:
            cached = self.cache.get_meta(f"fund:{sym}", ttl_minutes=60 * 24)
            if cached:
                out[sym] = cached
            else:
                to_fetch.append(sym)

        if to_fetch:
            log.info("Fetching fundamentals for %d symbols", len(to_fetch))
            fetched = self.yahoo.fetch_fundamentals(to_fetch, progress=progress)
            for sym, data in fetched.items():
                self.cache.put_meta(f"fund:{sym}", data)
                out[sym] = data
            self._status["fundamentals"] = self.yahoo.last_status
        else:
            self._status["fundamentals"] = SourceStatus("Cache", True, "all cached")
        return out

    # --------------------------------------------------------------- market

    def index_history(self, force_refresh: bool = False) -> pd.DataFrame | None:
        key = "^NSEI"
        if not force_refresh:
            df = self.cache.get_ohlcv(key, min_rows=200)
            last = self.cache.last_ohlcv_date(key)
            if df is not None and last:
                age = (datetime.now(UTC).date() - datetime.fromisoformat(last).date()).days
                if age <= 4:
                    return df
        df = self.yahoo.fetch_index_history(self.cfg.data.history_days)
        if df is not None and not df.empty:
            self.cache.put_ohlcv(key, df)
            return df
        return self.cache.get_ohlcv(key, min_rows=200)

    def market_snapshot(self) -> dict:
        snap = self.cache.get_meta("market:snapshot", ttl_minutes=15)
        if snap:
            self._status["market"] = SourceStatus("NSE (cached)", True, "snapshot < 15m old")
            return snap
        snap = self.nse.fetch_market_snapshot()
        self._status["market"] = self.nse.last_status
        if snap:
            self.cache.put_meta("market:snapshot", snap)
        return snap

    # --------------------------------------------------------------- health

    def source_health(self) -> dict[str, str]:
        return {
            k: f"{'ok' if v.ok else 'FAILED'} - {v.name}: {v.detail}"
            for k, v in self._status.items()
        }
