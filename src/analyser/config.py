"""Typed configuration loading and validation.

Config is loaded once at startup and validated hard. A bad config should fail
immediately and loudly, not silently produce nonsense recommendations.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
CACHE_DIR = PROJECT_ROOT / ".cache"


class UniverseConfig(BaseModel):
    index: str = "NIFTY100"
    min_avg_turnover_cr: float = 5.0
    exclude: list[str] = Field(default_factory=list)

    @field_validator("index")
    @classmethod
    def _known_index(cls, v: str) -> str:
        allowed = {"NIFTY50", "NIFTY100", "NIFTY200", "NIFTY500"}
        v = v.upper().replace(" ", "")
        if v not in allowed:
            raise ValueError(f"universe.index must be one of {sorted(allowed)}, got {v!r}")
        return v

    @field_validator("exclude")
    @classmethod
    def _upper_exclude(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]


class DataConfig(BaseModel):
    history_days: int = Field(750, ge=250)
    cache_ttl_minutes: int = Field(60, ge=0)
    batch_size: int = Field(50, ge=1, le=200)
    fundamentals_top_n: int = Field(40, ge=0)


class Weights(BaseModel):
    trend: float = 0.30
    momentum: float = 0.25
    volume: float = 0.15
    volatility: float = 0.10
    fundamental: float = 0.20

    @model_validator(mode="after")
    def _sums_to_one(self) -> "Weights":
        total = self.trend + self.momentum + self.volume + self.volatility + self.fundamental
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total:.4f}")
        return self

    def as_dict(self) -> dict[str, float]:
        return {
            "trend": self.trend,
            "momentum": self.momentum,
            "volume": self.volume,
            "volatility": self.volatility,
            "fundamental": self.fundamental,
        }


class IndicatorConfig(BaseModel):
    sma_fast: int = 50
    sma_slow: int = 200
    ema_signal: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    volume_lookback: int = 20
    roc_short: int = 20
    roc_long: int = 60
    swing_lookback: int = 10
    bb_period: int = 20
    bb_std: float = 2.0


class RiskConfig(BaseModel):
    capital: float = Field(100000.0, gt=0)
    risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    max_position_pct: float = Field(15.0, gt=0, le=100)
    atr_stop_multiple_trend: float = Field(2.5, gt=0)
    atr_stop_multiple_swing: float = Field(1.8, gt=0)
    min_reward_risk: float = Field(1.5, gt=0)


class SetupConfig(BaseModel):
    adx_trending: float = 22.0
    rsi_overbought: float = 78.0
    rsi_oversold: float = 32.0
    rsi_pullback_low: float = 38.0
    rsi_pullback_high: float = 55.0
    breakout_proximity_pct: float = 3.0
    volume_surge_ratio: float = 1.5


class BaseRateConfig(BaseModel):
    enabled: bool = True
    min_samples: int = Field(8, ge=1)
    lookback_days: int = Field(400, ge=100)


class OutputConfig(BaseModel):
    min_composite_score: float = 55.0
    max_results: int = Field(25, ge=1)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class AppConfig(BaseModel):
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    weights: Weights = Field(default_factory=Weights)
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    setups: SetupConfig = Field(default_factory=SetupConfig)
    base_rate: BaseRateConfig = Field(default_factory=BaseRateConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load and validate config from YAML, then apply environment overrides.

    Environment wins over the file because hosted platforms configure through
    env vars, and because it lets you run a smaller universe on a memory-limited
    free instance without committing a different config to the repo.

    Supported:
      ANALYSER_UNIVERSE    NIFTY50 | NIFTY100 | NIFTY200 | NIFTY500
      ANALYSER_CAPITAL     rupees, e.g. 250000
      ANALYSER_RISK_PCT    percent of capital risked per trade, e.g. 0.75
      ANALYSER_MAX_POS_PCT max percent of capital in one position
      ANALYSER_FUND_TOP_N  how many leaders get fundamentals fetched
    """
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    else:
        raw = {}

    _apply_env(raw)
    return AppConfig.model_validate(raw)


def _apply_env(raw: dict) -> None:
    """Overlay ANALYSER_* environment variables onto the raw config dict.

    Invalid values are ignored rather than raising: a typo in a platform's
    dashboard should not stop the app booting, and pydantic still validates
    whatever does get through.
    """
    import os

    def section(name: str) -> dict:
        node = raw.get(name)
        if not isinstance(node, dict):
            node = {}
            raw[name] = node
        return node

    if (v := os.environ.get("ANALYSER_UNIVERSE")):
        section("universe")["index"] = v.strip().upper().replace(" ", "")

    for env_key, sec, field, cast in (
        ("ANALYSER_CAPITAL", "risk", "capital", float),
        ("ANALYSER_RISK_PCT", "risk", "risk_per_trade_pct", float),
        ("ANALYSER_MAX_POS_PCT", "risk", "max_position_pct", float),
        ("ANALYSER_FUND_TOP_N", "data", "fundamentals_top_n", int),
    ):
        val = os.environ.get(env_key)
        if not val:
            continue
        try:
            section(sec)[field] = cast(val)
        except (TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------
#  Persistence
#
#  The settings page writes config back to disk. A plain yaml.dump would strip
#  every explanatory comment out of the file, so instead the YAML is rendered
#  from a template that keeps the documentation intact. The file stays as
#  readable after the app writes it as it was when hand-edited.
# ---------------------------------------------------------------------------

_TEMPLATE = """\
# ============================================================================
#  Stock Analyser configuration
#  Everything here is tunable. Nothing in the code hardcodes these values.
#  Edited from the Settings page, or by hand. Both are fine.
# ============================================================================

universe:
  # Which NSE index to scan. Options: NIFTY50 | NIFTY100 | NIFTY200 | NIFTY500
  # Bigger = slower scan but more candidates.
  index: {u_index}

  # Skip illiquid names: minimum 20-day average traded value in INR crore.
  # Protects you from stocks you cannot exit without moving the price.
  min_avg_turnover_cr: {u_turnover}

  # Hard exclusions by symbol, if you never want to see something.
  exclude: {u_exclude}

data:
  # 200-day MA needs 200 sessions; the surplus exists so the base-rate test has
  # enough past setup occurrences to mean anything. 750 (~3 years) is the floor.
  history_days: {d_history}

  # Cache lifetime in minutes. During market hours, drop this to 15.
  cache_ttl_minutes: {d_ttl}

  # yfinance batch size. Larger = fewer HTTP calls, bigger failure blast radius.
  batch_size: {d_batch}

  # Fundamentals need one HTTP call per stock, so only the top technical
  # candidates get them.
  fundamentals_top_n: {d_fund_n}

# ----------------------------------------------------------------------------
#  Scoring weights. Must sum to 1.0 (validated at startup).
#  Raise 'fundamental' if you are a longer-horizon investor.
#  Raise 'momentum' and 'volume' if you are a shorter-horizon swing trader.
# ----------------------------------------------------------------------------
weights:
  trend: {w_trend}
  momentum: {w_momentum}
  volume: {w_volume}
  volatility: {w_volatility}
  fundamental: {w_fundamental}

indicators:
  sma_fast: {i_sma_fast}
  sma_slow: {i_sma_slow}
  ema_signal: {i_ema}
  rsi_period: {i_rsi}
  atr_period: {i_atr}
  adx_period: {i_adx}
  macd_fast: {i_macd_fast}
  macd_slow: {i_macd_slow}
  macd_signal: {i_macd_signal}
  volume_lookback: {i_vol_lb}
  roc_short: {i_roc_s}
  roc_long: {i_roc_l}
  swing_lookback: {i_swing}
  bb_period: {i_bb_p}
  bb_std: {i_bb_std}

# ----------------------------------------------------------------------------
#  Risk and position sizing. This is the part that actually protects you.
# ----------------------------------------------------------------------------
risk:
  # Your total investable capital in INR. Used for position sizing.
  capital: {r_capital}

  # Max % of capital you are willing to LOSE on one idea if the stop hits.
  # 1.0 means a losing trade costs you 1% of capital. Keep this small.
  risk_per_trade_pct: {r_risk_pct}

  # Never let one position exceed this % of capital, whatever the stop distance.
  max_position_pct: {r_max_pos}

  # Stop loss distance in ATR multiples, per setup type.
  atr_stop_multiple_trend: {r_atr_trend}
  atr_stop_multiple_swing: {r_atr_swing}

  # Reject any idea whose reward:risk to the structural target is below this.
  min_reward_risk: {r_min_rr}

# ----------------------------------------------------------------------------
#  Setup detection thresholds.
# ----------------------------------------------------------------------------
setups:
  adx_trending: {s_adx}            # ADX above this = a real trend, not chop
  rsi_overbought: {s_rsi_ob}
  rsi_oversold: {s_rsi_os}
  rsi_pullback_low: {s_rsi_pl}     # pullback zone in an intact uptrend
  rsi_pullback_high: {s_rsi_ph}
  breakout_proximity_pct: {s_breakout}  # within this % of 52w high = breakout
  volume_surge_ratio: {s_vol_surge}     # volume vs its 20-day average

# ----------------------------------------------------------------------------
#  Base rate testing. The honesty layer: for each detected setup we replay the
#  same condition through history and report how often it actually worked.
# ----------------------------------------------------------------------------
base_rate:
  enabled: {b_enabled}
  min_samples: {b_min_samples}     # below this, confidence is downgraded
  lookback_days: {b_lookback}

output:
  # Only surface ideas scoring above this composite.
  min_composite_score: {o_min_score}
  max_results: {o_max}

server:
  host: "{sv_host}"
  port: {sv_port}
"""


def dump_config(cfg: AppConfig) -> str:
    """Render config as commented YAML."""
    return _TEMPLATE.format(
        u_index=cfg.universe.index,
        u_turnover=cfg.universe.min_avg_turnover_cr,
        u_exclude=("[]" if not cfg.universe.exclude else "[" + ", ".join(cfg.universe.exclude) + "]"),
        d_history=cfg.data.history_days,
        d_ttl=cfg.data.cache_ttl_minutes,
        d_batch=cfg.data.batch_size,
        d_fund_n=cfg.data.fundamentals_top_n,
        w_trend=cfg.weights.trend,
        w_momentum=cfg.weights.momentum,
        w_volume=cfg.weights.volume,
        w_volatility=cfg.weights.volatility,
        w_fundamental=cfg.weights.fundamental,
        i_sma_fast=cfg.indicators.sma_fast,
        i_sma_slow=cfg.indicators.sma_slow,
        i_ema=cfg.indicators.ema_signal,
        i_rsi=cfg.indicators.rsi_period,
        i_atr=cfg.indicators.atr_period,
        i_adx=cfg.indicators.adx_period,
        i_macd_fast=cfg.indicators.macd_fast,
        i_macd_slow=cfg.indicators.macd_slow,
        i_macd_signal=cfg.indicators.macd_signal,
        i_vol_lb=cfg.indicators.volume_lookback,
        i_roc_s=cfg.indicators.roc_short,
        i_roc_l=cfg.indicators.roc_long,
        i_swing=cfg.indicators.swing_lookback,
        i_bb_p=cfg.indicators.bb_period,
        i_bb_std=cfg.indicators.bb_std,
        r_capital=cfg.risk.capital,
        r_risk_pct=cfg.risk.risk_per_trade_pct,
        r_max_pos=cfg.risk.max_position_pct,
        r_atr_trend=cfg.risk.atr_stop_multiple_trend,
        r_atr_swing=cfg.risk.atr_stop_multiple_swing,
        r_min_rr=cfg.risk.min_reward_risk,
        s_adx=cfg.setups.adx_trending,
        s_rsi_ob=cfg.setups.rsi_overbought,
        s_rsi_os=cfg.setups.rsi_oversold,
        s_rsi_pl=cfg.setups.rsi_pullback_low,
        s_rsi_ph=cfg.setups.rsi_pullback_high,
        s_breakout=cfg.setups.breakout_proximity_pct,
        s_vol_surge=cfg.setups.volume_surge_ratio,
        b_enabled=str(cfg.base_rate.enabled).lower(),
        b_min_samples=cfg.base_rate.min_samples,
        b_lookback=cfg.base_rate.lookback_days,
        o_min_score=cfg.output.min_composite_score,
        o_max=cfg.output.max_results,
        sv_host=cfg.server.host,
        sv_port=cfg.server.port,
    )


def save_config(cfg: AppConfig, path: Path | str | None = None) -> None:
    """Persist config atomically.

    Written to a temp file then moved into place, so an interrupted write cannot
    leave you with a truncated config that fails to parse on next start.
    """
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    rendered = dump_config(cfg)
    # Parse-check before committing: never write a file we cannot read back.
    AppConfig.model_validate(yaml.safe_load(rendered) or {})

    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(target)
