"""Factor scoring and historical base rates.

Scores are 0-100 and exist only to rank and to explain. They are not
probabilities. A score of 80 does not mean an 80% chance of profit, and the
dashboard never presents it that way.

The base rate function is the honesty check: it replays the same setup
condition through the stock's own history and reports how often the target was
reached before the stop. Small samples are labelled unreliable rather than
quietly rounded into a confident-looking number.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import IndicatorConfig, SetupConfig
from .models import (
    SETUP_HORIZON_DAYS,
    BaseRate,
    Confidence,
    FactorScores,
    Fundamentals,
    Regime,
    Setup,
    Signal,
)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _v(row: pd.Series, key: str) -> float | None:
    if key not in row:
        return None
    try:
        f = float(row[key])
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else f


# --------------------------------------------------------------------- factors


def score_trend(row: pd.Series, sc: SetupConfig) -> tuple[float, list[Signal]]:
    """Is there a durable uptrend, and is price on the right side of it."""
    pts = 0.0
    signals: list[Signal] = []

    close = _v(row, "close")
    sma_f = _v(row, "sma_fast")
    sma_s = _v(row, "sma_slow")
    adx = _v(row, "adx")
    plus_di = _v(row, "plus_di")
    minus_di = _v(row, "minus_di")
    from_high = _v(row, "pct_from_52w_high")

    if close is None:
        return 0.0, signals

    if sma_s is not None:
        if close > sma_s:
            pts += 25
            signals.append(
                Signal(
                    "Above 200-day average",
                    f"Rs{close:,.2f} vs Rs{sma_s:,.2f}. The long-term trend is up.",
                    "bullish",
                )
            )
        else:
            signals.append(
                Signal(
                    "Below 200-day average",
                    f"Rs{close:,.2f} vs Rs{sma_s:,.2f}. Long-term trend is down.",
                    "bearish",
                )
            )

    if sma_f is not None:
        if close > sma_f:
            pts += 18
            signals.append(
                Signal(
                    "Above 50-day average",
                    f"Rs{close:,.2f} vs Rs{sma_f:,.2f}. Medium-term trend is up.",
                    "bullish",
                )
            )
        else:
            signals.append(
                Signal(
                    "Below 50-day average",
                    f"Rs{close:,.2f} vs Rs{sma_f:,.2f}. Medium-term trend is down.",
                    "bearish",
                )
            )

    if sma_f is not None and sma_s is not None:
        if sma_f > sma_s:
            pts += 17
            signals.append(
                Signal(
                    "Averages stacked bullishly",
                    "50-day is above the 200-day, the classic uptrend structure.",
                    "bullish",
                )
            )
        else:
            signals.append(
                Signal(
                    "Averages stacked bearishly",
                    "50-day is below the 200-day. Rallies tend to fail here.",
                    "bearish",
                )
            )

    if adx is not None:
        if adx >= sc.adx_trending:
            pts += 22
            signals.append(
                Signal(
                    "Trend is strong",
                    f"ADX {adx:.1f} (above {sc.adx_trending:.0f}). A real trend, not chop. "
                    "Trend-following works in this environment.",
                    "bullish",
                )
            )
        elif adx >= sc.adx_trending * 0.75:
            pts += 11
            signals.append(
                Signal(
                    "Trend is developing",
                    f"ADX {adx:.1f}, approaching trend threshold.",
                    "neutral",
                )
            )
        else:
            signals.append(
                Signal(
                    "Choppy, no trend",
                    f"ADX {adx:.1f} is low. Price is ranging; breakouts often fail.",
                    "neutral",
                )
            )

    if plus_di is not None and minus_di is not None and plus_di > minus_di:
        pts += 8

    if from_high is not None and from_high > -5:
        pts += 10
        signals.append(
            Signal(
                "Near 52-week high",
                f"{abs(from_high):.1f}% below its 1-year high. Strength tends to persist.",
                "bullish",
            )
        )
    elif from_high is not None and from_high < -25:
        signals.append(
            Signal(
                "Far below 52-week high",
                f"{abs(from_high):.1f}% below its 1-year high. In a drawdown.",
                "bearish",
            )
        )

    return _clamp(pts), signals


def score_momentum(
    row: pd.Series, rel_strength: float | None, sc: SetupConfig
) -> tuple[float, list[Signal]]:
    pts = 0.0
    signals: list[Signal] = []

    rsi = _v(row, "rsi")
    macd_hist = _v(row, "macd_hist")
    macd_line = _v(row, "macd")
    macd_sig = _v(row, "macd_signal")
    roc_s = _v(row, "roc_short")
    roc_l = _v(row, "roc_long")

    if rsi is not None:
        if 52 <= rsi <= 68:
            pts += 28
            signals.append(
                Signal("RSI in the strong zone", f"RSI {rsi:.0f}. Healthy momentum, not stretched.", "bullish")
            )
        elif 45 <= rsi < 52:
            pts += 18
            signals.append(Signal("RSI neutral", f"RSI {rsi:.0f}. Momentum is balanced.", "neutral"))
        elif 68 < rsi <= sc.rsi_overbought:
            pts += 16
            signals.append(
                Signal("RSI elevated", f"RSI {rsi:.0f}. Strong, but a pullback would give a better entry.", "neutral")
            )
        elif rsi > sc.rsi_overbought:
            pts += 4
            signals.append(
                Signal("RSI overbought", f"RSI {rsi:.0f}. Stretched. Poor risk on a fresh entry.", "bearish")
            )
        elif rsi <= sc.rsi_oversold:
            pts += 10
            signals.append(
                Signal("RSI oversold", f"RSI {rsi:.0f}. Washed out; a bounce is possible but risky.", "neutral")
            )
        else:
            pts += 8

    if macd_hist is not None and macd_line is not None and macd_sig is not None:
        if macd_line > macd_sig and macd_hist > 0:
            pts += 24
            signals.append(
                Signal("MACD positive", "MACD is above its signal line: momentum is building.", "bullish")
            )
        elif macd_hist > 0:
            pts += 12
        else:
            signals.append(
                Signal("MACD negative", "MACD is below its signal line: momentum is fading.", "bearish")
            )

    if roc_s is not None:
        pts += _clamp(14 + roc_s * 1.2, 0, 22)
    if roc_l is not None:
        pts += _clamp(10 + roc_l * 0.5, 0, 16)
        if roc_l > 15:
            signals.append(
                Signal("Strong 3-month gain", f"Up {roc_l:.1f}% over roughly 3 months.", "bullish")
            )
        elif roc_l < -15:
            signals.append(
                Signal("Weak 3-month performance", f"Down {abs(roc_l):.1f}% over roughly 3 months.", "bearish")
            )

    if rel_strength is not None:
        pts += _clamp(10 + rel_strength * 0.8, 0, 20)
        if rel_strength > 5:
            signals.append(
                Signal(
                    "Outperforming Nifty",
                    f"Beating the index by {rel_strength:.1f} percentage points over 3 months. "
                    "Leaders tend to keep leading.",
                    "bullish",
                )
            )
        elif rel_strength < -5:
            signals.append(
                Signal(
                    "Underperforming Nifty",
                    f"Lagging the index by {abs(rel_strength):.1f} percentage points over 3 months.",
                    "bearish",
                )
            )

    return _clamp(pts), signals


def score_volume(row: pd.Series, sc: SetupConfig) -> tuple[float, list[Signal]]:
    pts = 40.0  # neutral baseline; volume is confirmation, not a thesis
    signals: list[Signal] = []

    vol_ratio = _v(row, "vol_ratio")
    obv_slope = _v(row, "obv_slope")
    turnover = _v(row, "turnover")

    if vol_ratio is not None:
        if vol_ratio >= sc.volume_surge_ratio:
            pts += 35
            signals.append(
                Signal(
                    "Volume surge",
                    f"{vol_ratio:.1f}x the 20-day average volume. Real participation behind the move.",
                    "bullish",
                )
            )
        elif vol_ratio >= 1.0:
            pts += 18
        elif vol_ratio < 0.6:
            pts -= 15
            signals.append(
                Signal(
                    "Thin volume",
                    f"Only {vol_ratio:.1f}x average volume. Moves on light volume are less trustworthy.",
                    "neutral",
                )
            )

    if obv_slope is not None and np.isfinite(obv_slope):
        pts += _clamp(12 + obv_slope * 2.0, 0, 25)

    if turnover is not None:
        cr = turnover / 1e7  # rupee crore
        if cr < 2:
            pts -= 20
            signals.append(
                Signal(
                    "Low liquidity",
                    f"About Rs{cr:.1f} crore traded. Exits may be difficult at your size.",
                    "bearish",
                )
            )

    return _clamp(pts), signals


def score_volatility(row: pd.Series) -> tuple[float, list[Signal]]:
    """Rewards tradeable volatility. Too little means no movement; too much
    means the stop must sit so far away that position size collapses."""
    signals: list[Signal] = []
    atr_pct = _v(row, "atr_pct")
    if atr_pct is None:
        return 50.0, signals

    if 1.2 <= atr_pct <= 3.0:
        score = 85.0
    elif 0.8 <= atr_pct < 1.2:
        score = 65.0
    elif 3.0 < atr_pct <= 4.5:
        score = 55.0
        signals.append(
            Signal(
                "Elevated volatility",
                f"Daily range about {atr_pct:.1f}% of price. Stops must be wide, so size down.",
                "neutral",
            )
        )
    elif atr_pct > 4.5:
        score = 25.0
        signals.append(
            Signal(
                "Very high volatility",
                f"Daily range about {atr_pct:.1f}% of price. Hard to manage risk.",
                "bearish",
            )
        )
    else:
        score = 40.0
        signals.append(
            Signal("Very quiet", f"Daily range only {atr_pct:.1f}%. Little movement to capture.", "neutral")
        )
    return score, signals


def score_fundamental(f: Fundamentals | None) -> tuple[float, bool, list[Signal]]:
    """Returns (score, had_data, signals). Missing data scores a neutral 50 and
    is flagged rather than penalised, so absent data cannot look like bad data."""
    signals: list[Signal] = []
    if f is None:
        return 50.0, False, signals

    pts: list[float] = []

    if f.trailing_pe is not None:
        pe = f.trailing_pe
        if pe <= 0:
            pts.append(25.0)
            signals.append(Signal("Loss making", "Negative trailing earnings.", "bearish"))
        elif pe < 15:
            pts.append(85.0)
            signals.append(Signal("Low P/E", f"Trailing P/E {pe:.1f}. Undemanding valuation.", "bullish"))
        elif pe < 30:
            pts.append(65.0)
        elif pe < 60:
            pts.append(40.0)
        else:
            pts.append(20.0)
            signals.append(Signal("Expensive", f"Trailing P/E {pe:.1f}. Priced for perfection.", "bearish"))

    if f.roe is not None:
        roe = f.roe
        if roe >= 20:
            pts.append(90.0)
            signals.append(Signal("High return on equity", f"ROE {roe:.1f}%. Efficient use of capital.", "bullish"))
        elif roe >= 13:
            pts.append(70.0)
        elif roe >= 6:
            pts.append(45.0)
        else:
            pts.append(20.0)
            signals.append(Signal("Weak return on equity", f"ROE {roe:.1f}%.", "bearish"))

    if f.debt_to_equity is not None:
        de = f.debt_to_equity  # Yahoo reports this as a percentage
        if de < 30:
            pts.append(85.0)
            signals.append(Signal("Low debt", f"Debt/equity {de:.0f}%. Strong balance sheet.", "bullish"))
        elif de < 100:
            pts.append(60.0)
        elif de < 200:
            pts.append(35.0)
        else:
            pts.append(15.0)
            signals.append(Signal("High debt", f"Debt/equity {de:.0f}%. Leveraged.", "bearish"))

    if f.earnings_growth is not None:
        g = f.earnings_growth
        if g >= 20:
            pts.append(88.0)
            signals.append(Signal("Strong earnings growth", f"Earnings up {g:.1f}%.", "bullish"))
        elif g >= 5:
            pts.append(65.0)
        elif g >= -5:
            pts.append(45.0)
        else:
            pts.append(20.0)
            signals.append(Signal("Earnings declining", f"Earnings down {abs(g):.1f}%.", "bearish"))

    if f.revenue_growth is not None:
        g = f.revenue_growth
        pts.append(_clamp(50 + g * 1.6, 10, 90))

    if f.profit_margin is not None:
        pts.append(_clamp(35 + f.profit_margin * 2.2, 10, 90))

    if not pts:
        return 50.0, False, signals
    return _clamp(float(np.mean(pts))), True, signals


def composite(factors: FactorScores, weights: dict[str, float]) -> float:
    """Weighted blend. If fundamentals are missing, their weight is
    redistributed across the technical factors rather than scoring a silent 50."""
    w = dict(weights)
    if not factors.fundamental_available:
        fw = w.pop("fundamental", 0.0)
        total = sum(w.values())
        if total > 0:
            for k in w:
                w[k] += fw * (w[k] / total)
    scores = factors.as_dict()
    return round(sum(scores[k] * w.get(k, 0.0) for k in w), 1)


# ------------------------------------------------------------------ base rates


def setup_mask(df: pd.DataFrame, setup: Setup, sc: SetupConfig) -> pd.Series:
    """Vectorised recreation of the setup condition across all history.

    Used to find prior occurrences. Deliberately mirrors classify_setup.
    """
    close, sma_f, sma_s = df["close"], df["sma_fast"], df["sma_slow"]
    rsi, adx, vol_ratio = df["rsi"], df["adx"], df["vol_ratio"]
    from_high = df["pct_from_52w_high"]
    plus_di, minus_di = df["plus_di"], df["minus_di"]

    if setup is Setup.BREAKOUT:
        return (
            (from_high >= -sc.breakout_proximity_pct)
            & (vol_ratio >= sc.volume_surge_ratio)
            & (close > sma_f)
            & (rsi < sc.rsi_overbought)
        )
    if setup is Setup.TREND_CONTINUATION:
        return (close > sma_f) & (sma_f > sma_s) & (adx >= sc.adx_trending) & (plus_di > minus_di)
    if setup is Setup.PULLBACK_IN_UPTREND:
        return (
            (close > sma_s)
            & (rsi >= sc.rsi_pullback_low)
            & (rsi <= sc.rsi_pullback_high)
            & (close >= sma_f * 0.95)
        )
    if setup is Setup.OVERSOLD_BOUNCE:
        return (close > sma_s) & (rsi <= sc.rsi_oversold)
    if setup is Setup.EARLY_TREND:
        return (close > sma_f) & (sma_f > sma_s) & (adx < sc.adx_trending)
    return pd.Series(False, index=df.index)


def base_rate(
    df: pd.DataFrame,
    setup: Setup,
    sc: SetupConfig,
    ic: IndicatorConfig,
    atr_stop_mult: float,
    min_samples: int,
) -> BaseRate | None:
    """Replay this setup through the stock's own history.

    For each past occurrence, walk forward bar by bar and record whether the
    +1R target or the stop was reached first inside the horizon. This is a
    path-dependent test, so it does not flatter the strategy the way a simple
    'return after N days' calculation would.
    """
    lo, horizon = SETUP_HORIZON_DAYS.get(setup, (0, 0))
    if horizon <= 0 or len(df) < 120:
        return None

    mask = setup_mask(df, setup, sc).fillna(False).to_numpy()
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    atr_arr = df["atr"].to_numpy(dtype=float)
    n = len(close)

    wins = 0
    losses = 0
    timeouts = 0
    returns: list[float] = []
    last_signal = -10_000

    for i in range(60, n - 1):
        if not mask[i]:
            continue
        # Avoid counting the same episode many times over consecutive bars.
        if i - last_signal < max(5, horizon // 4):
            continue
        atr_i = atr_arr[i]
        if not np.isfinite(atr_i) or atr_i <= 0:
            continue

        entry = close[i]
        stop = entry - atr_stop_mult * atr_i
        target = entry + (entry - stop)  # +1R
        last_signal = i

        end = min(i + horizon, n - 1)
        outcome = None
        for j in range(i + 1, end + 1):
            hit_stop = low[j] <= stop
            hit_target = high[j] >= target
            if hit_stop and hit_target:
                outcome = "loss"  # assume the worse fill when both touch intrabar
                break
            if hit_stop:
                outcome = "loss"
                break
            if hit_target:
                outcome = "win"
                break
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        else:
            timeouts += 1
        returns.append((close[end] / entry - 1.0) * 100.0)

    total = wins + losses + timeouts
    if total == 0:
        return None

    decided = wins + losses
    win_rate = (wins / decided * 100.0) if decided else 0.0
    reliable = total >= min_samples

    if not reliable:
        note = (
            f"Only {total} historical occurrences. Too few to trust; treat this "
            "as anecdote, not evidence."
        )
    elif timeouts:
        note = (
            f"{wins} hit +1R first, {losses} hit the stop first, {timeouts} timed out "
            f"within {horizon} days across {total} occurrences on this stock."
        )
    else:
        note = f"{wins} wins, {losses} losses across {total} occurrences on this stock."

    return BaseRate(
        win_rate_pct=round(win_rate, 1),
        samples=total,
        avg_return_pct=round(float(np.mean(returns)), 2) if returns else 0.0,
        horizon_days=horizon,
        reliable=reliable,
        note=note,
    )


# ------------------------------------------------------------------ confidence


def assess_confidence(
    factors: FactorScores,
    setup: Setup,
    regime: Regime,
    br: BaseRate | None,
    signals: list[Signal],
) -> tuple[Confidence, list[str]]:
    """Confidence reflects agreement and evidence, never predicted profit."""
    reasons: list[str] = []
    points = 0

    bulls = sum(1 for s in signals if s.direction == "bullish")
    bears = sum(1 for s in signals if s.direction == "bearish")
    if bulls >= bears * 2 and bulls >= 4:
        points += 2
        reasons.append(f"{bulls} bullish signals against {bears} bearish: the evidence agrees.")
    elif bears > bulls:
        points -= 2
        reasons.append(f"{bears} bearish signals against {bulls} bullish: mixed evidence.")
    else:
        reasons.append(f"{bulls} bullish, {bears} bearish signals: partial agreement.")

    strong = sum(1 for v in factors.as_dict().values() if v >= 65)
    if strong >= 3:
        points += 1
        reasons.append(f"{strong} of 5 factors score 65 or above.")
    elif strong <= 1:
        points -= 1
        reasons.append("Only one factor is strong; the rest are middling.")

    if regime is Regime.BULLISH:
        points += 1
        reasons.append("Nifty is in an uptrend, which favours long positions.")
    elif regime is Regime.BEARISH:
        points -= 2
        reasons.append(
            "Nifty is in a downtrend. Long setups fail more often in this regime "
            "regardless of how good the individual chart looks."
        )

    if br is not None:
        if not br.reliable:
            points -= 1
            reasons.append(f"Historical sample is thin ({br.samples} occurrences).")
        elif br.win_rate_pct >= 55:
            points += 1
            reasons.append(
                f"This setup reached +1R before its stop {br.win_rate_pct:.0f}% of the time "
                f"historically on this stock ({br.samples} occurrences)."
            )
        elif br.win_rate_pct < 40:
            points -= 1
            reasons.append(
                f"This setup historically worked only {br.win_rate_pct:.0f}% of the time "
                f"on this stock ({br.samples} occurrences)."
            )
    else:
        reasons.append("No historical base rate available for this setup.")

    if not factors.fundamental_available:
        reasons.append("Fundamentals unavailable, so this is a technical read only.")

    if setup in (Setup.OVERSOLD_BOUNCE,):
        points -= 1
        reasons.append("Counter-trend setups are lower probability by nature.")

    if points >= 3:
        return Confidence.HIGH, reasons
    if points >= 1:
        return Confidence.MEDIUM, reasons
    return Confidence.LOW, reasons
