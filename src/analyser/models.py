"""Domain models.

These are the objects that flow from the data layer through scoring and out to
the dashboard. Deliberately plain and serialisable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum


class Setup(StrEnum):
    """The character of a trade idea. Drives horizon and stop placement."""

    TREND_CONTINUATION = "Trend continuation"
    BREAKOUT = "Breakout"
    PULLBACK_IN_UPTREND = "Pullback in uptrend"
    OVERSOLD_BOUNCE = "Oversold bounce"
    EARLY_TREND = "Early trend"
    NO_SETUP = "No clear setup"
    AVOID = "Avoid"


class Regime(StrEnum):
    BULLISH = "Bullish"
    NEUTRAL = "Neutral"
    BEARISH = "Bearish"


class Confidence(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


# Horizon guidance per setup, in calendar days (low, high).
# These describe how long the SETUP typically takes to resolve. They are not
# a promise about profit, and the dashboard says so explicitly.
SETUP_HORIZON_DAYS: dict[Setup, tuple[int, int]] = {
    Setup.TREND_CONTINUATION: (30, 120),
    Setup.EARLY_TREND: (21, 90),
    Setup.BREAKOUT: (10, 40),
    Setup.PULLBACK_IN_UPTREND: (7, 30),
    Setup.OVERSOLD_BOUNCE: (5, 15),
    Setup.NO_SETUP: (0, 0),
    Setup.AVOID: (0, 0),
}

SETUP_RATIONALE: dict[Setup, str] = {
    Setup.TREND_CONTINUATION: (
        "Price is above both the 50 and 200 day averages with a confirmed "
        "directional trend (ADX). Established trends tend to persist, so this "
        "is a hold-through-noise idea rather than a quick flip."
    ),
    Setup.EARLY_TREND: (
        "A trend is forming: price has reclaimed the longer average and "
        "momentum has turned up, but the trend strength reading is not yet "
        "fully confirmed. Earlier entry, higher failure rate."
    ),
    Setup.BREAKOUT: (
        "Price is pressing against its 52-week high on above-average volume. "
        "These resolve quickly, one way or the other. Respect the stop."
    ),
    Setup.PULLBACK_IN_UPTREND: (
        "The uptrend is intact but price has pulled back toward support and "
        "RSI has cooled. Better entry price than chasing, with the risk that "
        "the pullback keeps going."
    ),
    Setup.OVERSOLD_BOUNCE: (
        "Short-term oversold while the long-term uptrend still holds. This is "
        "a mean-reversion trade with a short leash, not an investment."
    ),
    Setup.NO_SETUP: "No coherent setup. Signals disagree or the stock is in chop.",
    Setup.AVOID: "Price is in a downtrend. Long ideas here have poor odds.",
}


@dataclass(slots=True)
class FactorScores:
    """Sub-scores, each 0-100. `available` flags which had real data."""

    trend: float = 0.0
    momentum: float = 0.0
    volume: float = 0.0
    volatility: float = 0.0
    fundamental: float = 50.0
    fundamental_available: bool = False

    def as_dict(self) -> dict[str, float]:
        return {
            "trend": self.trend,
            "momentum": self.momentum,
            "volume": self.volume,
            "volatility": self.volatility,
            "fundamental": self.fundamental,
        }


@dataclass(slots=True)
class Levels:
    """Actionable price levels. All in INR."""

    entry_low: float
    entry_high: float
    stop: float
    target_1r: float
    target_2r: float
    target_3r: float
    risk_per_share: float
    reward_risk: float


@dataclass(slots=True)
class ActionPlan:
    """The whole answer to 'when do I buy and when do I get out', in words
    and numbers, so it can be read off the screen and acted on."""

    # ---- when to invest
    entry_state: str
    entry_trigger: float | None
    entry_zone_low: float
    entry_zone_high: float
    entry_condition: str
    entry_invalidation: str

    # ---- when to stop
    stop: float
    stop_basis: str
    risk_per_share: float
    target_1r: float
    target_2r: float
    target_3r: float
    structural_target: float
    target_basis: str
    reward_risk: float
    trail_rule: str
    time_stop_days: int
    signal_exit_rule: str

    @property
    def is_buyable_now(self) -> bool:
        return self.entry_state == "Buy zone active"


@dataclass(slots=True)
class PositionPlan:
    """Risk-derived sizing. Answers 'how much' without predicting profit."""

    quantity: int
    capital_deployed: float
    pct_of_capital: float
    max_loss_at_stop: float
    pct_capital_at_risk: float
    sizing_note: str


@dataclass(slots=True)
class BaseRate:
    """How often this setup historically produced a positive forward return."""

    win_rate_pct: float
    samples: int
    avg_return_pct: float
    horizon_days: int
    reliable: bool
    note: str


@dataclass(slots=True)
class Fundamentals:
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    roe: float | None = None
    debt_to_equity: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    profit_margin: float | None = None
    dividend_yield: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class Signal:
    """One piece of evidence, shown to the user verbatim."""

    label: str
    detail: str
    direction: str  # "bullish" | "bearish" | "neutral"


@dataclass(slots=True)
class Idea:
    """A complete, explainable trade idea."""

    symbol: str
    company: str
    sector: str
    last_price: float
    as_of: date

    composite_score: float
    factors: FactorScores
    setup: Setup
    horizon_low_days: int
    horizon_high_days: int
    confidence: Confidence
    confidence_reasons: list[str]

    levels: Levels | None
    action: ActionPlan | None
    plan: PositionPlan | None
    base_rate: BaseRate | None
    signals: list[Signal] = field(default_factory=list)
    fundamentals: Fundamentals | None = None

    # Raw indicator snapshot, for the detail view.
    indicators: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # Recent closes for the inline table sparkline. Small on purpose: this rides
    # along in every scan payload, so 40 points keeps the JSON reasonable.
    spark: list[float] = field(default_factory=list)

    @property
    def horizon_label(self) -> str:
        if self.horizon_high_days == 0:
            return "n/a"
        lo, hi = self.horizon_low_days, self.horizon_high_days
        if hi <= 21:
            return f"{lo}-{hi} days"
        return f"{max(1, lo // 7)}-{hi // 7} weeks"

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "company": self.company,
            "sector": self.sector,
            "last_price": self.last_price,
            "as_of": self.as_of.isoformat(),
            "composite_score": self.composite_score,
            "factors": self.factors.as_dict(),
            "fundamental_available": self.factors.fundamental_available,
            "setup": str(self.setup),
            "horizon": self.horizon_label,
            "horizon_low_days": self.horizon_low_days,
            "horizon_high_days": self.horizon_high_days,
            "confidence": str(self.confidence),
            "confidence_reasons": self.confidence_reasons,
            "levels": asdict(self.levels) if self.levels else None,
            "action": asdict(self.action) if self.action else None,
            "plan": asdict(self.plan) if self.plan else None,
            "base_rate": asdict(self.base_rate) if self.base_rate else None,
            "signals": [asdict(s) for s in self.signals],
            "fundamentals": self.fundamentals.as_dict() if self.fundamentals else None,
            "indicators": self.indicators,
            "warnings": self.warnings,
            "spark": self.spark,
        }


@dataclass(slots=True)
class MarketContext:
    """Index-level regime. Gates how much trust to put in long ideas."""

    regime: Regime
    nifty_last: float
    nifty_change_pct: float
    pct_from_52w_high: float
    above_sma50: bool
    above_sma200: bool
    advance_decline: str
    market_status: str
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "regime": str(self.regime),
            "nifty_last": self.nifty_last,
            "nifty_change_pct": self.nifty_change_pct,
            "pct_from_52w_high": self.pct_from_52w_high,
            "above_sma50": self.above_sma50,
            "above_sma200": self.above_sma200,
            "advance_decline": self.advance_decline,
            "market_status": self.market_status,
            "note": self.note,
        }


@dataclass(slots=True)
class ScanResult:
    ideas: list[Idea]
    context: MarketContext
    scanned: int
    skipped: int
    generated_at: str
    universe: str
    source_health: dict[str, str]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ideas": [i.to_dict() for i in self.ideas],
            "context": self.context.to_dict(),
            "scanned": self.scanned,
            "skipped": self.skipped,
            "generated_at": self.generated_at,
            "universe": self.universe,
            "source_health": self.source_health,
            "errors": self.errors,
        }
