"""Entry triggers and exit rules.

This is the core of the tool. Everything else exists to feed this module.

Design stance: every level here is a mechanical consequence of price structure
and volatility. Nothing forecasts a price. The value is that entries and exits
are decided in advance, in writing, while you are calm, instead of improvised
while a position moves against you.

Entry  -> a price condition that must be TRUE before you buy.
Stop   -> the price at which the idea is disproven. Non-negotiable.
Targets-> R multiples, where R is the distance from entry to stop.
Time   -> if the setup has not worked inside its horizon, it has failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from .config import IndicatorConfig, RiskConfig, SetupConfig
from .models import SETUP_HORIZON_DAYS, Levels, PositionPlan, Setup


class EntryState(StrEnum):
    READY = "Buy zone active"
    WAIT = "Wait for trigger"
    MISSED = "Extended, wait for pullback"
    NO_ENTRY = "No entry"


@dataclass(slots=True)
class EntryPlan:
    """When to buy, stated as a condition rather than a hunch."""

    state: EntryState
    trigger_price: float | None
    zone_low: float
    zone_high: float
    condition: str
    invalidation: str


@dataclass(slots=True)
class ExitPlan:
    """When to stop. Every branch of getting out."""

    initial_stop: float
    stop_basis: str
    target_1r: float
    target_2r: float
    target_3r: float
    risk_per_share: float
    structural_target: float
    reward_risk: float
    target_basis: str
    trail_rule: str
    time_stop_days: int
    signal_exit_rule: str


# --------------------------------------------------------------------- setups


def classify_setup(row: pd.Series, sc: SetupConfig) -> Setup:
    """Label the character of the current chart. First match wins.

    Ordered most specific to least, so a breakout is not mislabelled as a
    generic uptrend.
    """
    close = _v(row, "close")
    sma_f = _v(row, "sma_fast")
    sma_s = _v(row, "sma_slow")
    rsi = _v(row, "rsi")
    adx = _v(row, "adx")
    vol_ratio = _v(row, "vol_ratio")
    from_high = _v(row, "pct_from_52w_high")
    plus_di = _v(row, "plus_di")
    minus_di = _v(row, "minus_di")

    if any(x is None for x in (close, sma_s, rsi)):
        return Setup.NO_SETUP

    # Downtrend: do not look for long ideas here.
    if sma_f is not None and close < sma_s and sma_f < sma_s:
        return Setup.AVOID
    if close < sma_s * 0.98:
        return Setup.AVOID

    above_fast = sma_f is not None and close > sma_f
    stacked = sma_f is not None and close > sma_f > sma_s
    trending = adx is not None and adx >= sc.adx_trending
    di_up = plus_di is not None and minus_di is not None and plus_di > minus_di

    # Breakout: at the highs, with participation.
    if (
        from_high is not None
        and from_high >= -sc.breakout_proximity_pct
        and vol_ratio is not None
        and vol_ratio >= sc.volume_surge_ratio
        and above_fast
        and rsi < sc.rsi_overbought
    ):
        return Setup.BREAKOUT

    # Established trend.
    if stacked and trending and di_up:
        return Setup.TREND_CONTINUATION

    # Pullback inside an intact uptrend.
    if (
        close > sma_s
        and sc.rsi_pullback_low <= rsi <= sc.rsi_pullback_high
        and sma_f is not None
        and close >= sma_f * 0.95
    ):
        return Setup.PULLBACK_IN_UPTREND

    # Deep but not broken.
    if close > sma_s and rsi <= sc.rsi_oversold:
        return Setup.OVERSOLD_BOUNCE

    # Trend forming but not yet confirmed by ADX.
    if stacked:
        return Setup.EARLY_TREND

    return Setup.NO_SETUP


# ---------------------------------------------------------------------- entry


def build_entry(row: pd.Series, prev: pd.Series, setup: Setup, sc: SetupConfig) -> EntryPlan:
    """Turn a setup into an explicit buy condition."""
    close = float(row["close"])
    high = float(row["high"])
    atr = _v(row, "atr") or close * 0.02
    sma_f = _v(row, "sma_fast")
    ema_s = _v(row, "ema_signal")
    high_52w = _v(row, "high_52w") or high
    rsi = _v(row, "rsi") or 50.0
    prev_high = float(prev["high"]) if prev is not None and not pd.isna(prev["high"]) else high

    if setup in (Setup.AVOID, Setup.NO_SETUP):
        return EntryPlan(
            state=EntryState.NO_ENTRY,
            trigger_price=None,
            zone_low=0.0,
            zone_high=0.0,
            condition="No entry. The setup does not qualify.",
            invalidation="n/a",
        )

    if setup is Setup.BREAKOUT:
        trigger = round(max(high_52w, prev_high) * 1.001, 2)
        ready = close >= trigger * 0.999
        return EntryPlan(
            state=EntryState.READY if ready else EntryState.WAIT,
            trigger_price=trigger,
            zone_low=round(trigger, 2),
            zone_high=round(trigger + 0.5 * atr, 2),
            condition=(
                f"Buy only on a daily CLOSE above Rs{trigger:,.2f} "
                f"with volume at least {sc.volume_surge_ratio:.1f}x the 20-day average. "
                "No close above the level means no trade."
            ),
            invalidation=(
                f"If it closes back below Rs{high_52w * 0.985:,.2f} the breakout has failed. "
                "Stand aside."
            ),
        )

    if setup in (Setup.TREND_CONTINUATION, Setup.EARLY_TREND):
        anchor = ema_s or sma_f or close
        zone_low = round(min(anchor, close - 0.5 * atr), 2)
        zone_high = round(close + 0.25 * atr, 2)
        extended = close > anchor + 2.5 * atr
        state = EntryState.MISSED if extended else EntryState.READY
        return EntryPlan(
            state=state,
            trigger_price=round(zone_high, 2),
            zone_low=zone_low,
            zone_high=zone_high,
            condition=(
                f"Accumulate between Rs{zone_low:,.2f} and Rs{zone_high:,.2f}. "
                f"The 21-day EMA at Rs{anchor:,.2f} is the reference; buying nearer "
                "that level tightens your stop and improves reward:risk."
                if state is EntryState.READY
                else (
                    f"Price is {((close - anchor) / atr):.1f} ATR above its "
                    f"{'21-day EMA' if ema_s else 'fast average'} at Rs{anchor:,.2f}. "
                    "Chasing here means a wide stop. Wait for a pullback into "
                    f"Rs{zone_low:,.2f}-Rs{anchor * 1.01:,.2f}."
                )
            ),
            invalidation=(
                f"A daily close below Rs{(sma_f or close * 0.95):,.2f} ends the trend premise."
            ),
        )

    if setup is Setup.PULLBACK_IN_UPTREND:
        trigger = round(prev_high * 1.001, 2)
        ready = close >= trigger
        return EntryPlan(
            state=EntryState.READY if ready else EntryState.WAIT,
            trigger_price=trigger,
            zone_low=round(close - 0.3 * atr, 2),
            zone_high=round(trigger + 0.3 * atr, 2),
            condition=(
                f"Wait for the pullback to stop falling. Buy on a close above "
                f"Rs{trigger:,.2f} (yesterday's high) with RSI turning back up from "
                f"{rsi:.0f}. Do not buy while it is still making lower daily lows."
            ),
            invalidation=(
                f"A close below the 200-day average at "
                f"Rs{(_v(row, 'sma_slow') or close * 0.9):,.2f} means the uptrend is gone."
            ),
        )

    # Oversold bounce: demand proof the fall has stopped.
    trigger = round(prev_high * 1.001, 2)
    ready = close >= trigger
    return EntryPlan(
        state=EntryState.READY if ready else EntryState.WAIT,
        trigger_price=trigger,
        zone_low=round(close - 0.3 * atr, 2),
        zone_high=round(trigger + 0.5 * atr, 2),
        condition=(
            f"Counter-trend and short leash. Buy only on a close above "
            f"Rs{trigger:,.2f}, confirming the decline has paused. "
            "Never average down on this setup."
        ),
        invalidation=(
            f"A close below Rs{(_v(row, 'low_52w') or close * 0.9) * 1.01:,.2f} "
            "or below the 200-day average voids it."
        ),
    )


# ----------------------------------------------------------------------- exit


def build_exit(
    row: pd.Series, setup: Setup, entry: EntryPlan, rc: RiskConfig, ic: IndicatorConfig
) -> ExitPlan | None:
    """Derive the full exit ladder from structure and volatility."""
    close = float(row["close"])
    atr = _v(row, "atr")
    if atr is None or atr <= 0:
        return None

    entry_px = entry.zone_high if entry.zone_high > 0 else close
    swing = _v(row, "swing_low") or close - 2 * atr
    sma_f = _v(row, "sma_fast")
    high_52w = _v(row, "high_52w") or entry_px

    mult = (
        rc.atr_stop_multiple_swing
        if setup in (Setup.OVERSOLD_BOUNCE, Setup.PULLBACK_IN_UPTREND, Setup.BREAKOUT)
        else rc.atr_stop_multiple_trend
    )

    structural_stop = swing * 0.995        # just under the recent swing low
    volatility_stop = entry_px - mult * atr

    # Take the further stop. Being shaken out by noise is the more common and
    # more expensive mistake; risk-based sizing shrinks the position to pay for
    # the wider stop, so total rupee risk is unchanged.
    if structural_stop <= volatility_stop:
        stop = structural_stop
        basis = f"just below the {ic.swing_lookback}-day swing low (Rs{swing:,.2f})"
    else:
        stop = volatility_stop
        basis = f"{mult:.1f}x ATR (Rs{atr:,.2f}) below entry"

    stop = round(min(stop, entry_px * 0.995), 2)
    risk_per_share = entry_px - stop
    if risk_per_share <= 0:
        return None

    t1 = round(entry_px + risk_per_share, 2)
    t2 = round(entry_px + 2 * risk_per_share, 2)
    t3 = round(entry_px + 3 * risk_per_share, 2)

    # Room to run. Two cases, because they are genuinely different:
    #   below the 52w high -> that high is real overhead supply, so measure to it
    #   at the 52w high    -> nothing overhead, so project a measured move equal
    #                         to the height of the base being broken out of
    if high_52w > entry_px * 1.02:
        structural_target = high_52w
        target_basis = "the 52-week high, the nearest overhead supply"
    else:
        low_52w = _v(row, "low_52w") or (entry_px - 6 * atr)
        base_height = max(entry_px - max(low_52w, entry_px - 12 * atr), 3 * atr)
        structural_target = entry_px + base_height
        target_basis = "a measured move equal to the height of the base"

    structural_target = round(structural_target, 2)
    reward_risk = round((structural_target - entry_px) / risk_per_share, 2)

    lo, hi = SETUP_HORIZON_DAYS.get(setup, (0, 0))

    return ExitPlan(
        initial_stop=stop,
        stop_basis=basis,
        target_1r=t1,
        target_2r=t2,
        target_3r=t3,
        risk_per_share=round(risk_per_share, 2),
        structural_target=structural_target,
        reward_risk=reward_risk,
        target_basis=target_basis,
        trail_rule=(
            f"At Rs{t1:,.2f} (+1R) sell a third and move the stop to breakeven "
            f"(Rs{entry_px:,.2f}) so the trade can no longer lose money. "
            f"At Rs{t2:,.2f} (+2R) sell another third and trail the rest "
            f"{1.5:.1f}x ATR below the highest close reached. "
            f"Let the last third run while it holds above the "
            f"{ic.sma_fast}-day average."
        ),
        time_stop_days=hi,
        signal_exit_rule=(
            f"Exit regardless of price if it closes below the {ic.sma_fast}-day "
            f"average (Rs{(sma_f or close):,.2f}) for two consecutive sessions, "
            "or if MACD histogram flips negative while price is below entry."
        ),
    )


# --------------------------------------------------------------------- sizing


def build_position_plan(entry_px: float, stop: float, rc: RiskConfig) -> PositionPlan:
    """Quantity from risk, not from hope.

    Loss if stopped = capital x risk_per_trade_pct. Position size falls out of
    that and the stop distance. A wider stop mechanically means fewer shares.
    """
    risk_per_share = max(entry_px - stop, 0.01)
    risk_budget = rc.capital * (rc.risk_per_trade_pct / 100.0)

    qty_by_risk = int(np.floor(risk_budget / risk_per_share))
    qty_cap = int(np.floor(rc.capital * (rc.max_position_pct / 100.0) / max(entry_px, 0.01)))
    qty = max(0, min(qty_by_risk, qty_cap))

    deployed = qty * entry_px
    max_loss = qty * risk_per_share

    if qty == 0:
        note = (
            "Position size rounds to zero: the stop is too wide for your capital "
            "and risk limit. Skip this one or raise capital."
        )
    elif qty_cap < qty_by_risk:
        note = (
            f"Capped by the {rc.max_position_pct:.0f}% max-position limit. "
            f"Risk-based size would have been {qty_by_risk} shares."
        )
    else:
        note = (
            f"Sized so a stop-out costs {rc.risk_per_trade_pct:.1f}% of capital "
            f"(Rs{max_loss:,.0f})."
        )

    return PositionPlan(
        quantity=qty,
        capital_deployed=round(deployed, 2),
        pct_of_capital=round(deployed / rc.capital * 100.0, 2) if rc.capital else 0.0,
        max_loss_at_stop=round(max_loss, 2),
        pct_capital_at_risk=round(max_loss / rc.capital * 100.0, 2) if rc.capital else 0.0,
        sizing_note=note,
    )


def to_levels(entry: EntryPlan, ex: ExitPlan) -> Levels:
    return Levels(
        entry_low=entry.zone_low,
        entry_high=entry.zone_high,
        stop=ex.initial_stop,
        target_1r=ex.target_1r,
        target_2r=ex.target_2r,
        target_3r=ex.target_3r,
        risk_per_share=ex.risk_per_share,
        reward_risk=ex.reward_risk,
    )


def _v(row: pd.Series, key: str) -> float | None:
    if key not in row:
        return None
    val = row[key]
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else f
