"""Technical indicators, implemented directly on pandas/numpy.

Written out rather than pulled from TA-Lib so the project installs with plain
pip and no system C library. These are the standard formulations; where a
convention varies (RSI smoothing, ADX smoothing) Wilder's method is used, which
is what charting platforms show.

Every function returns a full-length Series aligned to the input index, with
NaN during the warm-up period. Nothing here forward-fills or back-fills, because
that would leak future information into a historical reading.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing: an EMA with alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index, Wilder-smoothed. Range 0-100."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder(gain, period)
    avg_loss = wilder(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # Zero average loss means an unbroken run of gains: RSI is 100 by definition.
    return out.where(avg_loss != 0.0, 100.0).where(avg_gain.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range in price units. The basis for stops and sizing."""
    return wilder(true_range(high, low, close), period)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (adx, plus_di, minus_di). ADX measures trend strength, not direction.

    Above ~22 means a real trend is present; below means chop, where trend
    signals produce whipsaws.
    """
    up = high.diff()
    down = -low.diff()

    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    tr_smooth = wilder(true_range(high, low, close), period)
    plus_di = 100.0 * wilder(plus_dm, period) / tr_smooth.replace(0.0, np.nan)
    minus_di = 100.0 * wilder(minus_dm, period) / tr_smooth.replace(0.0, np.nan)

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return wilder(dx, period), plus_di, minus_di


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, mid, lower)."""
    mid = sma(close, period)
    sd = close.rolling(window=period, min_periods=period).std(ddof=0)
    return mid + num_std * sd, mid, mid - num_std * sd


def roc(close: pd.Series, period: int) -> pd.Series:
    """Rate of change in percent over `period` bars."""
    return close.pct_change(periods=period) * 100.0


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: cumulative volume signed by daily direction."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


def slope_pct(series: pd.Series, period: int) -> pd.Series:
    """Least-squares slope over a window, normalised to percent per bar.

    Used to ask 'is this line rising' without eyeballing a chart.
    """

    def _fit(window: np.ndarray) -> float:
        if np.isnan(window).any():
            return np.nan
        x = np.arange(len(window), dtype=float)
        slope = np.polyfit(x, window, 1)[0]
        mean = float(np.mean(window))
        return float(slope / mean * 100.0) if mean else np.nan

    return series.rolling(window=period, min_periods=period).apply(_fit, raw=True)


def rolling_max(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=1).max()


def rolling_min(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=1).min()


def swing_low(low: pd.Series, lookback: int = 10) -> pd.Series:
    """Lowest low over the lookback, for structural stop placement."""
    return low.rolling(window=lookback, min_periods=1).min()


def relative_strength(close: pd.Series, index_close: pd.Series, period: int = 60) -> float | None:
    """Stock return minus index return over `period` bars, in percentage points.

    Positive means the stock is outperforming Nifty. Weak stocks in strong
    markets are usually weak for a reason.
    """
    aligned = pd.concat([close, index_close], axis=1, join="inner").dropna()
    if len(aligned) < period + 1:
        return None
    stock = aligned.iloc[:, 0]
    idx = aligned.iloc[:, 1]
    s_ret = (stock.iloc[-1] / stock.iloc[-1 - period] - 1.0) * 100.0
    i_ret = (idx.iloc[-1] / idx.iloc[-1 - period] - 1.0) * 100.0
    if not np.isfinite(s_ret) or not np.isfinite(i_ret):
        return None
    return float(s_ret - i_ret)


def compute_all(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Attach every indicator to a copy of the OHLCV frame.

    `cfg` is an IndicatorConfig. Returns a new frame; the input is untouched.
    """
    out = df.copy()
    close, high, low, vol = out["close"], out["high"], out["low"], out["volume"]

    out["sma_fast"] = sma(close, cfg.sma_fast)
    out["sma_slow"] = sma(close, cfg.sma_slow)
    out["ema_signal"] = ema(close, cfg.ema_signal)
    out["rsi"] = rsi(close, cfg.rsi_period)
    out["atr"] = atr(high, low, close, cfg.atr_period)
    out["atr_pct"] = out["atr"] / close * 100.0

    adx_v, plus_di, minus_di = adx(high, low, close, cfg.adx_period)
    out["adx"] = adx_v
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    macd_line, macd_sig, macd_hist = macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    out["macd"] = macd_line
    out["macd_signal"] = macd_sig
    out["macd_hist"] = macd_hist

    bb_u, bb_m, bb_l = bollinger(close, cfg.bb_period, cfg.bb_std)
    out["bb_upper"] = bb_u
    out["bb_mid"] = bb_m
    out["bb_lower"] = bb_l

    out["roc_short"] = roc(close, cfg.roc_short)
    out["roc_long"] = roc(close, cfg.roc_long)

    out["vol_avg"] = sma(vol, cfg.volume_lookback)
    out["vol_ratio"] = vol / out["vol_avg"].replace(0.0, np.nan)
    out["obv"] = obv(close, vol)
    out["obv_slope"] = slope_pct(out["obv"].abs().replace(0.0, np.nan), cfg.volume_lookback)

    out["high_52w"] = rolling_max(high, 252)
    out["low_52w"] = rolling_min(low, 252)
    out["pct_from_52w_high"] = (close / out["high_52w"] - 1.0) * 100.0
    out["pct_above_52w_low"] = (close / out["low_52w"] - 1.0) * 100.0

    out["swing_low"] = swing_low(low, cfg.swing_lookback)
    out["turnover"] = close * vol  # rupee turnover, for the liquidity filter

    return out
