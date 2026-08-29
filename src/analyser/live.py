"""Live market data: session state, intraday polling, and fan-out to browsers.

Honest scope
------------
"Live" here means the browser updates without a reload, driven by a server that
keeps re-fetching. It does not mean tick-by-tick. The ceiling is the data source:
Yahoo Finance intraday bars run roughly 15 minutes behind the exchange, and the
1-minute series only advances once a minute. Polling faster than that just burns
requests for no new information.

If you want genuine real-time, the source has to change, not this module. A free
Angel One SmartAPI account gives real WebSocket ticks; implement it as another
DataSource and point `LiveQuotes._fetch` at it. Everything downstream, including
the browser protocol, stays as-is.

Design
------
* One background task polls only the symbols someone is actually looking at,
  reference-counted, so an idle dashboard costs nothing.
* Polling stops entirely when the market is closed. There is no point asking
  Yahoo for new bars at midnight.
* Fan-out is per-connection asyncio queues with a bounded size. A slow or dead
  browser gets its stale messages dropped rather than blocking the poller.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# NSE equity cash market, all times IST.
PRE_OPEN_START = dtime(9, 0)
SESSION_START = dtime(9, 15)
SESSION_END = dtime(15, 30)

MAX_SUBSCRIPTIONS = 40      # cap the fan-out; a browser cannot watch more usefully
POLL_OPEN_SECONDS = 25      # 1-minute bars, so faster gains nothing
POLL_CLOSED_SECONDS = 300   # just enough to notice the market opening
QUEUE_MAX = 8


# --------------------------------------------------------------------- session


@dataclass(slots=True)
class SessionState:
    status: str            # "pre-open" | "open" | "closed"
    is_open: bool
    label: str
    now_ist: str
    next_change: str | None = None
    source: str = "clock"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "is_open": self.is_open,
            "label": self.label,
            "now_ist": self.now_ist,
            "next_change": self.next_change,
            "source": self.source,
        }


class MarketSession:
    """Is the NSE cash market open right now.

    Two sources, deliberately. The clock is always available but cannot know
    about trading holidays. NSE's marketStatus endpoint does know, so it wins
    when reachable, and the clock is the fallback. Believing the clock alone
    would have the dashboard claim "open" on Republic Day.
    """

    def __init__(self, registry=None, cache_seconds: int = 60) -> None:
        self.registry = registry
        self.cache_seconds = cache_seconds
        self._cached: SessionState | None = None
        self._cached_at: datetime | None = None

    def state(self) -> SessionState:
        now = datetime.now(UTC)
        if (
            self._cached is not None
            and self._cached_at is not None
            and (now - self._cached_at).total_seconds() < self.cache_seconds
        ):
            return self._cached

        state = self._compute()
        self._cached = state
        self._cached_at = now
        return state

    def is_open(self) -> bool:
        return self.state().is_open

    # ------------------------------------------------------------- internals

    def _compute(self) -> SessionState:
        ist = datetime.now(IST)
        clock = self._from_clock(ist)

        # Ask NSE. It knows about holidays; the clock does not.
        exchange = self._from_exchange()
        if exchange is not None:
            if exchange and not clock.is_open:
                # Exchange says open, clock says shut. Trust the clock on hours
                # (NSE sometimes reports stale state outside hours) but say so.
                return clock
            if not exchange and clock.is_open:
                return SessionState(
                    status="closed",
                    is_open=False,
                    label="Closed (exchange holiday)",
                    now_ist=ist.strftime("%d %b %Y, %H:%M:%S IST"),
                    source="NSE",
                )
            clock.source = "NSE + clock"
        return clock

    def _from_clock(self, ist: datetime) -> SessionState:
        stamp = ist.strftime("%d %b %Y, %H:%M:%S IST")
        t = ist.time()
        weekday = ist.weekday() < 5

        if not weekday:
            return SessionState(
                status="closed", is_open=False, label="Closed (weekend)",
                now_ist=stamp, next_change=self._next_open_label(ist),
            )
        if PRE_OPEN_START <= t < SESSION_START:
            return SessionState(
                status="pre-open", is_open=False, label="Pre-open session",
                now_ist=stamp, next_change="Opens 09:15",
            )
        if SESSION_START <= t <= SESSION_END:
            return SessionState(
                status="open", is_open=True, label="Market open",
                now_ist=stamp, next_change="Closes 15:30",
            )
        return SessionState(
            status="closed", is_open=False, label="Closed",
            now_ist=stamp, next_change=self._next_open_label(ist),
        )

    def _from_exchange(self) -> bool | None:
        """True/False from NSE, or None when unavailable."""
        if self.registry is None:
            return None
        try:
            snap = self.registry.market_snapshot() or {}
        except Exception:
            return None
        status = str(snap.get("market_status", "")).strip().lower()
        if not status:
            return None
        return status == "open"

    @staticmethod
    def _next_open_label(ist: datetime) -> str:
        nxt = ist
        # Past today's close, or the weekend: roll to the next weekday.
        if ist.time() > SESSION_END or ist.weekday() >= 5:
            nxt = ist + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            return f"Opens {nxt.strftime('%a')} 09:15"
        return "Opens 09:15"


# ---------------------------------------------------------------- live quotes


@dataclass
class Quote:
    symbol: str
    price: float
    prev_close: float | None
    change: float | None
    change_pct: float | None
    volume: float | None
    bar_time: str | None
    day_high: float | None = None
    day_low: float | None = None
    day_open: float | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "prev_close": self.prev_close,
            "change": self.change,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "bar_time": self.bar_time,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "day_open": self.day_open,
        }


@dataclass
class LiveQuotes:
    """Polls intraday bars for watched symbols and pushes updates to clients."""

    registry: object
    session: MarketSession
    poll_open: int = POLL_OPEN_SECONDS
    poll_closed: int = POLL_CLOSED_SECONDS

    _counts: dict[str, int] = field(default_factory=dict)
    _latest: dict[str, Quote] = field(default_factory=dict)
    _bars: dict[str, list[dict]] = field(default_factory=dict)
    _clients: set[asyncio.Queue] = field(default_factory=set)
    _task: asyncio.Task | None = None
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _stop: bool = False
    last_poll: str | None = None
    last_error: str | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = False
            self._task = asyncio.create_task(self._loop(), name="live-quotes")

    async def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # --------------------------------------------------------------- clients

    def add_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._clients.add(q)
        return q

    def remove_client(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    # ---------------------------------------------------------- subscriptions

    def subscribe(self, symbols: list[str]) -> list[str]:
        """Reference-counted. Returns the symbols actually accepted."""
        taken: list[str] = []
        for s in symbols:
            s = s.strip().upper()
            if not s:
                continue
            if s not in self._counts and len(self._counts) >= MAX_SUBSCRIPTIONS:
                log.debug("subscription cap reached, ignoring %s", s)
                continue
            self._counts[s] = self._counts.get(s, 0) + 1
            taken.append(s)
        if taken:
            self._wake.set()   # fetch immediately rather than waiting out the sleep
        return taken

    def unsubscribe(self, symbols: list[str]) -> None:
        for s in symbols:
            s = s.strip().upper()
            n = self._counts.get(s)
            if n is None:
                continue
            if n <= 1:
                self._counts.pop(s, None)
                self._latest.pop(s, None)
                self._bars.pop(s, None)
            else:
                self._counts[s] = n - 1

    @property
    def watched(self) -> list[str]:
        return sorted(self._counts)

    async def prime(self, symbols: list[str]) -> list[str]:
        """Fetch quotes for symbols we have nothing cached for, once.

        Deliberately ignores whether the market is open. Continuous polling is
        gated on session state, but a first look should still show the last
        traded price rather than an empty box on a weekend.

        Does not take a reference, so callers do not have to release one.
        """
        missing = [
            s for s in (x.strip().upper() for x in symbols) if s and s not in self._latest
        ]
        if not missing:
            return []

        frames = await asyncio.to_thread(self._fetch, missing)
        filled: list[str] = []
        for sym, df in frames.items():
            quote, bars = self._to_quote(sym, df)
            if quote is None:
                continue
            self._latest[sym] = quote
            self._bars[sym] = bars
            filled.append(sym)

        if filled:
            self.last_poll = datetime.now(IST).strftime("%H:%M:%S IST")
            await self._broadcast(
                {
                    "type": "quotes",
                    "session": self.session.state().to_dict(),
                    "quotes": {s: self._latest[s].to_dict() for s in filled},
                    "last_poll": self.last_poll,
                }
            )
        return filled

    def snapshot(self, symbols: list[str] | None = None) -> dict:
        want = [s.strip().upper() for s in symbols] if symbols else self.watched
        return {
            "type": "quotes",
            "session": self.session.state().to_dict(),
            "quotes": {s: self._latest[s].to_dict() for s in want if s in self._latest},
            "last_poll": self.last_poll,
            "error": self.last_error,
        }

    def bars(self, symbol: str) -> list[dict]:
        return self._bars.get(symbol.strip().upper(), [])

    # ------------------------------------------------------------------ loop

    async def _loop(self) -> None:
        log.info("live quote poller started")
        while not self._stop:
            open_now = self.session.is_open()
            try:
                if open_now and self._counts:
                    await self._poll()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("live poll failed: %s", exc)

            delay = self.poll_open if open_now else self.poll_closed
            self._wake.clear()
            try:
                # Wake early when someone subscribes to something new.
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except (asyncio.TimeoutError, TimeoutError):
                pass

    async def _poll(self) -> None:
        symbols = self.watched
        if not symbols:
            return
        frames = await asyncio.to_thread(self._fetch, symbols)
        if not frames:
            return

        updated: dict[str, dict] = {}
        for sym, df in frames.items():
            q, bars = self._to_quote(sym, df)
            if q is None:
                continue
            prev = self._latest.get(sym)
            self._latest[sym] = q
            self._bars[sym] = bars
            # Only push symbols whose price or bar actually moved.
            if prev is None or prev.price != q.price or prev.bar_time != q.bar_time:
                updated[sym] = q.to_dict()

        self.last_poll = datetime.now(IST).strftime("%H:%M:%S IST")
        self.last_error = None
        if updated:
            await self._broadcast(
                {
                    "type": "quotes",
                    "session": self.session.state().to_dict(),
                    "quotes": updated,
                    "last_poll": self.last_poll,
                }
            )

    def _fetch(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """Blocking. Two days of 1-minute bars, so yesterday's close is available
        for a real change figure rather than a session-only delta."""
        import yfinance as yf

        from .sources.base import to_yahoo

        tickers = [to_yahoo(s) for s in symbols]
        try:
            raw = yf.download(
                tickers=" ".join(tickers),
                period="2d",
                interval="1m",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            log.warning("intraday download failed: %s", exc)
            return {}
        if raw is None or raw.empty:
            return {}

        out: dict[str, pd.DataFrame] = {}
        multi = isinstance(raw.columns, pd.MultiIndex)
        for sym, tkr in zip(symbols, tickers, strict=True):
            try:
                if multi:
                    lvl0 = set(raw.columns.get_level_values(0))
                    df = raw[tkr] if tkr in lvl0 else raw.xs(tkr, axis=1, level=1)
                else:
                    df = raw
            except (KeyError, IndexError):
                continue
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
            if "close" not in df.columns:
                continue
            df = df.dropna(subset=["close"])
            if not df.empty:
                out[sym] = df
        return out

    @staticmethod
    def _to_quote(symbol: str, df: pd.DataFrame) -> tuple[Quote | None, list[dict]]:
        if df is None or df.empty:
            return None, []

        idx = df.index
        if getattr(idx, "tz", None) is not None:
            local = idx.tz_convert(IST)
        else:
            local = pd.DatetimeIndex(idx).tz_localize("UTC").tz_convert(IST)

        dates = local.normalize()
        sessions = sorted(set(dates))
        today = sessions[-1]

        today_mask = dates == today
        today_df = df.loc[today_mask]
        if today_df.empty:
            return None, []

        prev_close = None
        if len(sessions) > 1:
            prev_df = df.loc[dates == sessions[-2]]
            if not prev_df.empty:
                prev_close = float(prev_df["close"].iloc[-1])

        price = float(today_df["close"].iloc[-1])
        change = (price - prev_close) if prev_close else None
        change_pct = (change / prev_close * 100.0) if (change is not None and prev_close) else None

        bars = [
            {
                "t": ts.strftime("%Y-%m-%d %H:%M"),
                "o": _r(row.get("open")),
                "h": _r(row.get("high")),
                "l": _r(row.get("low")),
                "c": _r(row.get("close")),
                "v": _r(row.get("volume"), 0),
            }
            for ts, row in zip(local[today_mask], today_df.to_dict("records"), strict=True)
        ]

        quote = Quote(
            symbol=symbol,
            price=round(price, 2),
            prev_close=round(prev_close, 2) if prev_close else None,
            change=round(change, 2) if change is not None else None,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            volume=_r(today_df["volume"].sum(), 0) if "volume" in today_df else None,
            bar_time=local[today_mask][-1].strftime("%Y-%m-%d %H:%M"),
            day_high=_r(today_df["high"].max()) if "high" in today_df else None,
            day_low=_r(today_df["low"].min()) if "low" in today_df else None,
            day_open=_r(today_df["open"].iloc[0]) if "open" in today_df else None,
        )
        return quote, bars

    async def _broadcast(self, message: dict) -> None:
        dead = []
        for q in list(self._clients):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # Client is not keeping up. Drop its oldest and retry once; if it
                # is genuinely gone the socket handler will clean it up.
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except Exception:
                    dead.append(q)
            except Exception:
                dead.append(q)
        for q in dead:
            self._clients.discard(q)


def _r(v, digits: int = 2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, digits) if digits else int(f)
