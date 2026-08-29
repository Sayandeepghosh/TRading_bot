"""NSE public endpoints.

Used for two things yfinance does not do well:
  1. Authoritative index constituents with official sector labels
     (nsearchives CSVs - stable, official, no auth).
  2. Index level and market open/closed status.

NSE requires a browser-like User-Agent and a priming request to set cookies.
Their JSON API paths shift without notice, so every call is best-effort and the
caller must tolerate failure. The archives CSVs have proven far more stable than
the /api/ JSON routes.
"""

from __future__ import annotations

import csv
import io
import logging
import threading
import time

import requests

from .base import SourceStatus, UniverseMember

log = logging.getLogger(__name__)

_INDEX_CSV = {
    "NIFTY50": "ind_nifty50list.csv",
    "NIFTY100": "ind_nifty100list.csv",
    "NIFTY200": "ind_nifty200list.csv",
    "NIFTY500": "ind_nifty500list.csv",
}

_ARCHIVE_BASE = "https://nsearchives.nseindia.com/content/indices/"
_HOME = "https://www.nseindia.com/"
_ALL_INDICES = "https://www.nseindia.com/api/allIndices"
_MARKET_STATUS = "https://www.nseindia.com/api/marketStatus"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


class NseSource:
    """Best-effort NSE client. Never raises to the caller."""

    name = "NSE"

    def __init__(self, timeout: float = 15.0, min_interval: float = 0.4) -> None:
        self.timeout = timeout
        self.min_interval = min_interval  # self-throttle; NSE blocks bursts
        self._session: requests.Session | None = None
        self._primed_at = 0.0
        self._last_call = 0.0
        self._lock = threading.Lock()
        self.last_status = SourceStatus(self.name, True, "not yet called")

    # ------------------------------------------------------------- plumbing

    def _get_session(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.headers.update(_HEADERS)
            self._session = s
        # Re-prime cookies every 5 minutes.
        if time.time() - self._primed_at > 300:
            try:
                self._session.get(_HOME, timeout=self.timeout)
                self._primed_at = time.time()
            except requests.RequestException as exc:
                log.debug("NSE priming failed: %s", exc)
        return self._session

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()

    def _get(self, url: str) -> requests.Response | None:
        with self._lock:
            session = self._get_session()
            self._throttle()
            try:
                resp = session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                log.debug("NSE GET %s failed: %s", url, exc)
                return None
        if resp.status_code != 200:
            log.debug("NSE GET %s -> HTTP %s", url, resp.status_code)
            return None
        return resp

    # -------------------------------------------------------------- universe

    def fetch_universe(self, index: str) -> list[UniverseMember]:
        """Official constituent list with sector labels. Empty list on failure."""
        filename = _INDEX_CSV.get(index.upper())
        if not filename:
            self.last_status = SourceStatus(self.name, False, f"unknown index {index}")
            return []

        resp = self._get(_ARCHIVE_BASE + filename)
        if resp is None:
            self.last_status = SourceStatus(self.name, False, "constituent CSV unreachable")
            return []

        members: list[UniverseMember] = []
        try:
            reader = csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                symbol = (row.get("Symbol") or "").strip().upper()
                series = (row.get("Series") or "EQ").strip().upper()
                if not symbol or series not in {"EQ", "BE"}:
                    continue
                members.append(
                    UniverseMember(
                        symbol=symbol,
                        company=(row.get("Company Name") or symbol).strip(),
                        sector=(row.get("Industry") or "Unknown").strip(),
                    )
                )
        except (csv.Error, UnicodeDecodeError) as exc:
            self.last_status = SourceStatus(self.name, False, f"CSV parse error: {exc}")
            return []

        if members:
            self.last_status = SourceStatus(self.name, True, f"{len(members)} constituents")
        else:
            self.last_status = SourceStatus(self.name, False, "CSV parsed but empty")
        return members

    # ---------------------------------------------------------------- market

    def fetch_market_snapshot(self) -> dict:
        """Index level, breadth and open/closed. Keys absent when unavailable."""
        out: dict = {}

        resp = self._get(_ALL_INDICES)
        if resp is not None:
            try:
                data = resp.json().get("data", [])
                for row in data:
                    if str(row.get("index", "")).upper() == "NIFTY 50":
                        out["nifty_last"] = _num(row.get("last"))
                        out["nifty_change_pct"] = _num(row.get("percentChange"))
                        out["nifty_prev_close"] = _num(row.get("previousClose"))
                        out["nifty_year_high"] = _num(row.get("yearHigh"))
                        out["nifty_year_low"] = _num(row.get("yearLow"))
                        adv = _num(row.get("advances"))
                        dec = _num(row.get("declines"))
                        if adv is not None and dec is not None:
                            out["advances"] = int(adv)
                            out["declines"] = int(dec)
                        break
            except (ValueError, AttributeError, TypeError) as exc:
                log.debug("allIndices parse failed: %s", exc)

        resp = self._get(_MARKET_STATUS)
        if resp is not None:
            try:
                for row in resp.json().get("marketState", []):
                    if str(row.get("market", "")).lower().startswith("capital"):
                        out["market_status"] = str(row.get("marketStatus", "Unknown"))
                        out["trade_date"] = str(row.get("tradeDate", ""))
                        break
            except (ValueError, AttributeError, TypeError) as exc:
                log.debug("marketStatus parse failed: %s", exc)

        self.last_status = SourceStatus(
            self.name, bool(out), f"{len(out)} fields" if out else "no market data"
        )
        return out


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None
