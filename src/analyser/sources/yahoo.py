"""Yahoo Finance source via yfinance.

Free, no account, no key. Supplies daily OHLCV and company fundamentals.

Known limitations, handled here rather than pretended away:
  - Intraday quotes are delayed roughly 15 minutes. Fine for daily-timeframe
    analysis, useless for scalping.
  - The trailing row is sometimes partial: a volume figure with NaN prices.
    Those rows are dropped before anything downstream sees them.
  - Fundamentals require one HTTP call per symbol, so the caller restricts this
    to the top-ranked shortlist.
"""

from __future__ import annotations

import logging
import warnings
from typing import Callable

import pandas as pd

from .base import SourceStatus, from_yahoo, to_yahoo

log = logging.getLogger(__name__)

warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")
warnings.filterwarnings("ignore", message=".*auto_adjust.*")

_OHLCV = ["open", "high", "low", "close", "volume"]


class YahooSource:
    name = "Yahoo Finance"

    def __init__(self, batch_size: int = 50) -> None:
        self.batch_size = batch_size
        self.last_status = SourceStatus(self.name, True, "not yet called")

    # -------------------------------------------------------------- history

    def fetch_history(
        self,
        symbols: list[str],
        days: int,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Batched daily OHLCV. Returns only symbols that produced usable data.

        `progress(done, total)` fires after each batch. This is the slowest stage
        of a scan, so it is the one worth reporting on.
        """
        import yfinance as yf

        if not symbols:
            return {}

        period = f"{max(days, 250)}d"
        out: dict[str, pd.DataFrame] = {}
        failed: list[str] = []
        done = 0

        for chunk in _chunks(symbols, self.batch_size):
            tickers = [to_yahoo(s) for s in chunk]
            try:
                raw = yf.download(
                    tickers=" ".join(tickers),
                    period=period,
                    interval="1d",
                    auto_adjust=True,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                )
            except Exception as exc:  # yfinance raises a wide variety
                log.warning("Yahoo batch of %d failed: %s", len(chunk), exc)
                failed.extend(chunk)
                done += len(chunk)
                if progress:
                    progress(done, len(symbols))
                continue

            if raw is None or raw.empty:
                failed.extend(chunk)
                done += len(chunk)
                if progress:
                    progress(done, len(symbols))
                continue

            for sym, tkr in zip(chunk, tickers, strict=True):
                df = _extract(raw, tkr, single=len(tickers) == 1)
                if df is None or df.empty:
                    failed.append(sym)
                    continue
                out[sym] = df

            done += len(chunk)
            if progress:
                progress(done, len(symbols))

        ok = len(out)
        self.last_status = SourceStatus(
            self.name,
            ok > 0,
            f"{ok}/{len(symbols)} symbols" + (f", {len(failed)} failed" if failed else ""),
        )
        if failed:
            log.info("Yahoo history missing for %d symbols: %s", len(failed), failed[:10])
        return out

    # ---------------------------------------------------------- fundamentals

    def fetch_fundamentals(
        self, symbols: list[str], progress: Callable[[int, int], None] | None = None
    ) -> dict[str, dict]:
        """One call per symbol. Missing fields come back as None, not zero."""
        import yfinance as yf

        out: dict[str, dict] = {}
        for n, sym in enumerate(symbols, start=1):
            if progress:
                progress(n, len(symbols))
            try:
                info = yf.Ticker(to_yahoo(sym)).info or {}
            except Exception as exc:
                log.debug("fundamentals failed for %s: %s", sym, exc)
                continue
            if not info:
                continue
            out[sym] = {
                "name": info.get("longName") or info.get("shortName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "market_cap": _num(info.get("marketCap")),
                "trailing_pe": _num(info.get("trailingPE")),
                "forward_pe": _num(info.get("forwardPE")),
                "price_to_book": _num(info.get("priceToBook")),
                "roe": _pct(info.get("returnOnEquity")),
                "debt_to_equity": _num(info.get("debtToEquity")),
                "revenue_growth": _pct(info.get("revenueGrowth")),
                "earnings_growth": _pct(
                    info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
                ),
                "profit_margin": _pct(info.get("profitMargins")),
                "dividend_yield": _num(info.get("dividendYield")),
            }
        self.last_status = SourceStatus(
            self.name, bool(out), f"fundamentals for {len(out)}/{len(symbols)}"
        )
        return out

    # ---------------------------------------------------------------- index

    def fetch_index_history(self, days: int = 400) -> pd.DataFrame | None:
        """Nifty 50 history, for regime detection and relative strength."""
        import yfinance as yf

        try:
            raw = yf.download(
                tickers="^NSEI",
                period=f"{max(days, 250)}d",
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
        except Exception as exc:
            log.warning("Nifty index history failed: %s", exc)
            return None
        return _extract(raw, "^NSEI", single=True)


# --------------------------------------------------------------------- utils


def _extract(raw: pd.DataFrame, ticker: str, single: bool) -> pd.DataFrame | None:
    """Pull one ticker's OHLCV out of a yfinance frame.

    yfinance's column layout varies with ticker count and version, so probe
    rather than assume.
    """
    if raw is None or raw.empty:
        return None

    df: pd.DataFrame | None = None
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = set(raw.columns.get_level_values(0))
        lvl1 = set(raw.columns.get_level_values(1))
        if ticker in lvl0:
            df = raw[ticker]
        elif ticker in lvl1:
            df = raw.xs(ticker, axis=1, level=1)
        elif single:
            df = raw.droplevel(1, axis=1) if len(lvl1) == 1 else None
    else:
        df = raw

    if df is None or df.empty:
        return None

    df = df.copy()
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    if "adj_close" in df.columns and "close" not in df.columns:
        df["close"] = df["adj_close"]
    if not all(c in df.columns for c in _OHLCV):
        return None

    df = df[_OHLCV]
    # The critical cleanup: kill partial rows that would poison indicators.
    df = df.dropna(subset=["close", "high", "low"])
    if df.empty:
        return None

    df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).tz_localize(None)
    return df[~df.index.duplicated(keep="last")].sort_index()


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _pct(v: object) -> float | None:
    """Yahoo returns ratios (0.18); convert to percent (18.0)."""
    f = _num(v)
    return None if f is None else f * 100.0
