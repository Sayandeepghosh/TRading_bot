"""Holdings exit monitor.

Answers the second half of the question: you already own something, so when do
you get out?

You record what you bought, at what price, on what date, and the stop you set.
Each run re-evaluates the position against the same rules that generated it and
returns one of four verdicts:

  EXIT_NOW      a hard rule has triggered. Stop breached, or trend broken.
  TIGHTEN_STOP  the trade is working. Lock in progress.
  TIME_EXIT     the horizon elapsed without the setup delivering.
  HOLD          nothing has changed. Sit still.

The verdict is mechanical. That is the point: it removes the moment where you
talk yourself into holding a loser because it "has to come back".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

import pandas as pd

from .config import PROJECT_ROOT, AppConfig
from .engine import Analyser

HOLDINGS_PATH = PROJECT_ROOT / "config" / "holdings.json"


class Verdict(StrEnum):
    EXIT_NOW = "EXIT NOW"
    TIGHTEN_STOP = "TIGHTEN STOP"
    TIME_EXIT = "TIME EXIT"
    PARTIAL_PROFIT = "TAKE PARTIAL PROFIT"
    HOLD = "HOLD"


@dataclass(slots=True)
class Holding:
    symbol: str
    quantity: int
    entry_price: float
    entry_date: str          # ISO yyyy-mm-dd
    stop_price: float | None = None
    target_price: float | None = None
    notes: str = ""

    @property
    def entry_dt(self) -> date:
        try:
            return datetime.fromisoformat(self.entry_date).date()
        except ValueError:
            return date.today()


@dataclass(slots=True)
class HoldingReview:
    symbol: str
    company: str
    quantity: int
    entry_price: float
    entry_date: str
    last_price: float
    days_held: int

    pnl_abs: float
    pnl_pct: float
    r_multiple: float | None

    verdict: Verdict
    headline: str
    reasons: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    current_stop: float | None = None
    suggested_stop: float | None = None
    next_target: float | None = None

    indicators: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = str(self.verdict)
        return d


def load_holdings(path: Path | None = None) -> list[Holding]:
    p = path or HOLDINGS_PATH
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[Holding] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(
                Holding(
                    symbol=str(item["symbol"]).strip().upper(),
                    quantity=int(item["quantity"]),
                    entry_price=float(item["entry_price"]),
                    entry_date=str(item["entry_date"]),
                    stop_price=_opt_float(item.get("stop_price")),
                    target_price=_opt_float(item.get("target_price")),
                    notes=str(item.get("notes", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_holdings(holdings: list[Holding], path: Path | None = None) -> None:
    p = path or HOLDINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps([asdict(h) for h in holdings], indent=2),
        encoding="utf-8",
    )


class HoldingsMonitor:
    def __init__(self, cfg: AppConfig, analyser: Analyser) -> None:
        self.cfg = cfg
        self.analyser = analyser

    def review_all(self, holdings: list[Holding]) -> list[HoldingReview]:
        return [r for h in holdings if (r := self.review(h)) is not None]

    def review(self, h: Holding) -> HoldingReview | None:
        df = self.analyser.price_series(h.symbol, days=400)
        if df is None or df.empty:
            return None

        last = df.iloc[-1]
        close = float(last["close"])
        atr = _f(last.get("atr")) or close * 0.02
        sma_f = _f(last.get("sma_fast"))
        sma_s = _f(last.get("sma_slow"))
        rsi = _f(last.get("rsi"))
        macd_hist = _f(last.get("macd_hist"))
        adx = _f(last.get("adx"))

        days_held = (date.today() - h.entry_dt).days
        pnl_abs = (close - h.entry_price) * h.quantity
        pnl_pct = (close / h.entry_price - 1.0) * 100.0 if h.entry_price else 0.0

        # Risk unit, from the original stop if recorded, else a volatility proxy.
        if h.stop_price and h.stop_price < h.entry_price:
            risk_unit = h.entry_price - h.stop_price
        else:
            risk_unit = self.cfg.risk.atr_stop_multiple_trend * atr
        r_multiple = (close - h.entry_price) / risk_unit if risk_unit > 0 else None

        reasons: list[str] = []
        actions: list[str] = []
        verdict = Verdict.HOLD
        headline = "Nothing has changed. Hold and leave it alone."

        # ------------------------------------------------ hard exits, in order
        if h.stop_price and close <= h.stop_price:
            verdict = Verdict.EXIT_NOW
            headline = f"Stop breached. Close at Rs{close:,.2f} is at or below your stop of Rs{h.stop_price:,.2f}."
            reasons.append(
                "This is the level you chose in advance as proof the idea was wrong. "
                "Honour it. Moving a stop down is how a small loss becomes a large one."
            )
            actions.append(f"Sell all {h.quantity} shares.")

        elif sma_s is not None and close < sma_s and h.entry_price > sma_s:
            verdict = Verdict.EXIT_NOW
            headline = (
                f"Long-term trend broken. Price Rs{close:,.2f} has closed below the "
                f"200-day average at Rs{sma_s:,.2f}."
            )
            reasons.append(
                "You bought while the long-term uptrend was intact. That premise is gone, "
                "so the reason for owning it is gone."
            )
            actions.append(f"Exit all {h.quantity} shares.")

        elif sma_f is not None and close < sma_f and _closed_below_for(df, "sma_fast", 2):
            verdict = Verdict.EXIT_NOW
            headline = (
                f"Two consecutive closes below the 50-day average (Rs{sma_f:,.2f}). "
                "Medium-term trend has turned."
            )
            reasons.append(
                "One close below can be noise. Two in a row is a change of character."
            )
            actions.append(
                f"Exit, or reduce hard and hold the rest only above Rs{close - 1.5 * atr:,.2f}."
            )

        # ------------------------------------------------------- time-based exit
        elif days_held > 0 and _time_exhausted(days_held, r_multiple):
            verdict = Verdict.TIME_EXIT
            headline = (
                f"Held {days_held} days with the position still going nowhere "
                f"({pnl_pct:+.1f}%). The setup has not delivered."
            )
            reasons.append(
                "Capital sitting in a stock that is not moving is capital not available "
                "for one that is. A trade that has not worked in its expected window has failed, "
                "even if it has not lost much."
            )
            actions.append("Close it and redeploy, unless a fresh setup has formed.")

        # ------------------------------------------------------ working trades
        elif r_multiple is not None and r_multiple >= 2.0:
            verdict = Verdict.PARTIAL_PROFIT
            headline = f"Up {r_multiple:.1f}R ({pnl_pct:+.1f}%). Take money off the table."
            reasons.append(
                "The trade has delivered twice what you risked. Locking part of that in "
                "converts a paper gain into a real one and removes the chance of round-tripping."
            )
            actions.append(f"Sell about a third ({max(1, h.quantity // 3)} shares).")
            actions.append(
                f"Trail the stop on the remainder to Rs{close - 1.5 * atr:,.2f} "
                "(1.5x ATR below the current close)."
            )

        elif r_multiple is not None and r_multiple >= 1.0:
            verdict = Verdict.TIGHTEN_STOP
            headline = f"Up {r_multiple:.1f}R ({pnl_pct:+.1f}%). Move the stop to breakeven."
            reasons.append(
                "At +1R the priority shifts from making money to not giving it back. "
                "A breakeven stop means this trade can no longer cost you anything."
            )
            actions.append(f"Raise the stop to your entry price, Rs{h.entry_price:,.2f}.")

        # ------------------------------------------------------- warning signs
        else:
            warn: list[str] = []
            if rsi is not None and rsi > self.cfg.setups.rsi_overbought:
                warn.append(f"RSI {rsi:.0f} is overbought; near-term pullback risk is elevated.")
            if macd_hist is not None and macd_hist < 0 and close < h.entry_price:
                warn.append("MACD has turned negative while you are underwater.")
            if adx is not None and adx < 15:
                warn.append(f"ADX {adx:.0f}: the trend has gone flat, so progress may stall.")
            if h.stop_price:
                dist = (close - h.stop_price) / close * 100.0
                if dist < 3:
                    warn.append(f"Only {dist:.1f}% above your stop. Decision point is close.")
            if warn:
                headline = f"Holding, with cautions ({pnl_pct:+.1f}%)."
                reasons.extend(warn)
            else:
                reasons.append(
                    f"Above your stop, trend structure intact, {pnl_pct:+.1f}% on the position."
                )

        # Suggested stop: never below the one already in place.
        structural = _f(last.get("swing_low"))
        candidates = [c for c in (structural, close - self.cfg.risk.atr_stop_multiple_trend * atr) if c]
        suggested = max(candidates) if candidates else None
        if suggested is not None and h.stop_price:
            suggested = max(suggested, h.stop_price)
        if r_multiple is not None and r_multiple >= 1.0:
            suggested = max(suggested or h.entry_price, h.entry_price)

        next_target = h.target_price or (h.entry_price + 2 * risk_unit if risk_unit else None)

        if verdict is Verdict.HOLD and not actions:
            actions.append("No action. Do not tinker.")

        return HoldingReview(
            symbol=h.symbol,
            company=h.symbol,
            quantity=h.quantity,
            entry_price=round(h.entry_price, 2),
            entry_date=h.entry_date,
            last_price=round(close, 2),
            days_held=days_held,
            pnl_abs=round(pnl_abs, 2),
            pnl_pct=round(pnl_pct, 2),
            r_multiple=round(r_multiple, 2) if r_multiple is not None else None,
            verdict=verdict,
            headline=headline,
            reasons=reasons,
            actions=actions,
            current_stop=round(h.stop_price, 2) if h.stop_price else None,
            suggested_stop=round(suggested, 2) if suggested else None,
            next_target=round(next_target, 2) if next_target else None,
            indicators={
                k: round(float(last[k]), 2)
                for k in ("close", "sma_fast", "sma_slow", "rsi", "atr", "adx", "macd_hist")
                if k in last and pd.notna(last[k])
            },
        )


# ------------------------------------------------------------------- helpers


def _closed_below_for(df: pd.DataFrame, col: str, sessions: int) -> bool:
    """True when the last `sessions` closes are all below `col`."""
    if col not in df.columns or len(df) < sessions:
        return False
    tail = df.tail(sessions)
    if tail[col].isna().any():
        return False
    return bool((tail["close"] < tail[col]).all())


def _time_exhausted(days_held: int, r_multiple: float | None) -> bool:
    """A position past ~90 days that has not reached +0.5R has stalled."""
    if days_held < 90:
        return False
    return r_multiple is None or r_multiple < 0.5


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _opt_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
