"""Scan orchestration.

Pipeline per run:
  universe -> price history -> indicators -> setup -> entry/exit -> score
           -> fundamentals (shortlist only) -> base rate -> confidence -> rank

Two-pass by design: technicals run on the whole universe cheaply from batched
downloads, then fundamentals are fetched only for the leaders, because that call
costs one HTTP round trip per company.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Callable

import pandas as pd

from . import indicators as ind
from . import scoring
from .config import AppConfig
from .models import (
    SETUP_HORIZON_DAYS,
    SETUP_RATIONALE,
    ActionPlan,
    Confidence,
    FactorScores,
    Fundamentals,
    Idea,
    MarketContext,
    Regime,
    ScanResult,
    Setup,
    Signal,
)
from .rules import (
    EntryPlan,
    EntryState,
    ExitPlan,
    build_entry,
    build_exit,
    build_position_plan,
    classify_setup,
    to_levels,
)

# progress(stage_label, items_done, items_total)
ProgressFn = Callable[[str, int, int], None]


def _action_plan(entry: EntryPlan, ex: ExitPlan) -> ActionPlan:
    """Flatten the entry and exit rules into one screen-ready object."""
    return ActionPlan(
        entry_state=str(entry.state),
        entry_trigger=entry.trigger_price,
        entry_zone_low=entry.zone_low,
        entry_zone_high=entry.zone_high,
        entry_condition=entry.condition,
        entry_invalidation=entry.invalidation,
        stop=ex.initial_stop,
        stop_basis=ex.stop_basis,
        risk_per_share=ex.risk_per_share,
        target_1r=ex.target_1r,
        target_2r=ex.target_2r,
        target_3r=ex.target_3r,
        structural_target=ex.structural_target,
        target_basis=ex.target_basis,
        reward_risk=ex.reward_risk,
        trail_rule=ex.trail_rule,
        time_stop_days=ex.time_stop_days,
        signal_exit_rule=ex.signal_exit_rule,
    )
from .sources.registry import DataRegistry

log = logging.getLogger(__name__)


class Analyser:
    def __init__(self, cfg: AppConfig, registry: DataRegistry | None = None) -> None:
        self.cfg = cfg
        self.registry = registry or DataRegistry(cfg)

    # ---------------------------------------------------------------- regime

    def market_context(self, force_refresh: bool = False) -> MarketContext:
        """Index-level backdrop. Gates confidence on every long idea."""
        idx = self.registry.index_history(force_refresh=force_refresh)
        snap = self.registry.market_snapshot()

        if idx is None or idx.empty:
            return MarketContext(
                regime=Regime.NEUTRAL,
                nifty_last=float(snap.get("nifty_last") or 0.0),
                nifty_change_pct=float(snap.get("nifty_change_pct") or 0.0),
                pct_from_52w_high=0.0,
                above_sma50=False,
                above_sma200=False,
                advance_decline="n/a",
                market_status=str(snap.get("market_status") or "Unknown"),
                note="Index history unavailable, so regime could not be assessed. "
                "Treat all signals with extra caution.",
            )

        d = ind.compute_all(idx, self.cfg.indicators)
        last = d.iloc[-1]
        close = float(last["close"])
        sma50 = last.get("sma_fast")
        sma200 = last.get("sma_slow")
        above50 = bool(pd.notna(sma50) and close > float(sma50))
        above200 = bool(pd.notna(sma200) and close > float(sma200))
        from_high = float(last["pct_from_52w_high"]) if pd.notna(last["pct_from_52w_high"]) else 0.0

        if above50 and above200 and from_high > -8:
            regime = Regime.BULLISH
            note = (
                "Nifty is above both its 50 and 200-day averages and near its highs. "
                "This is the friendliest backdrop for long positions."
            )
        elif not above200 or from_high < -12:
            regime = Regime.BEARISH
            note = (
                "Nifty is below its 200-day average or well off its highs. Most long "
                "setups fail in this regime. Consider smaller size, or simply waiting."
            )
        else:
            regime = Regime.NEUTRAL
            note = (
                "Nifty is mixed: neither trending up cleanly nor broken down. "
                "Be selective and expect more false starts."
            )

        adv, dec = snap.get("advances"), snap.get("declines")
        ad = f"{adv} advancing / {dec} declining" if adv and dec else "n/a"

        change_pct = snap.get("nifty_change_pct")
        if change_pct is None and len(d) > 1:
            prev = float(d["close"].iloc[-2])
            change_pct = (close / prev - 1.0) * 100.0

        return MarketContext(
            regime=regime,
            nifty_last=round(float(snap.get("nifty_last") or close), 2),
            nifty_change_pct=round(float(change_pct or 0.0), 2),
            pct_from_52w_high=round(from_high, 2),
            above_sma50=above50,
            above_sma200=above200,
            advance_decline=ad,
            market_status=str(snap.get("market_status") or "Unknown"),
            note=note,
        )

    # ------------------------------------------------------------------ scan

    def scan(
        self,
        force_refresh: bool = False,
        progress: ProgressFn | None = None,
    ) -> ScanResult:
        started = datetime.now(UTC)
        cfg = self.cfg
        errors: list[str] = []

        def report(stage: str, done: int = 0, total: int = 0) -> None:
            if progress:
                progress(stage, done, total)

        report("Loading universe")
        members = self.registry.universe(force_refresh=force_refresh)
        by_symbol = {m.symbol: m for m in members}
        symbols = list(by_symbol)
        log.info("Universe %s: %d symbols", cfg.universe.index, len(symbols))

        report("Reading market regime")
        context = self.market_context(force_refresh=force_refresh)
        idx_hist = self.registry.index_history()
        idx_close = idx_hist["close"] if idx_hist is not None and not idx_hist.empty else None

        report("Downloading price history", 0, len(symbols))
        history, missing = self.registry.history(
            symbols,
            force_refresh=force_refresh,
            progress=lambda d, t: report("Downloading price history", d, t),
        )
        if missing:
            errors.append(f"No price data for {len(missing)} symbols: {', '.join(missing[:8])}")

        # ---------------- pass 1: technicals across the whole universe
        prelim: list[tuple[float, str, pd.DataFrame, pd.Series, pd.Series, Setup, list[Signal], FactorScores, float | None]] = []
        skipped = 0
        n_done = 0
        n_total = len(history)
        report("Computing indicators", 0, n_total)

        for sym, raw in history.items():
            n_done += 1
            if n_done % 10 == 0 or n_done == n_total:
                report("Computing indicators", n_done, n_total)
            try:
                df = ind.compute_all(raw, cfg.indicators)
            except (KeyError, ValueError) as exc:
                log.debug("indicator failure for %s: %s", sym, exc)
                skipped += 1
                continue

            if len(df) < 60:
                skipped += 1
                continue

            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last

            # Liquidity gate: turnover in rupee crore, 20-day average.
            turnover_cr = float((df["turnover"].tail(20).mean() or 0.0) / 1e7)
            if turnover_cr < cfg.universe.min_avg_turnover_cr:
                skipped += 1
                continue

            setup = classify_setup(last, cfg.setups)
            rel = (
                ind.relative_strength(df["close"], idx_close, cfg.indicators.roc_long)
                if idx_close is not None
                else None
            )

            t_score, t_sig = scoring.score_trend(last, cfg.setups)
            m_score, m_sig = scoring.score_momentum(last, rel, cfg.setups)
            v_score, v_sig = scoring.score_volume(last, cfg.setups)
            vol_score, vol_sig = scoring.score_volatility(last)

            factors = FactorScores(
                trend=round(t_score, 1),
                momentum=round(m_score, 1),
                volume=round(v_score, 1),
                volatility=round(vol_score, 1),
                fundamental=50.0,
                fundamental_available=False,
            )
            signals = t_sig + m_sig + v_sig + vol_sig
            tech_score = scoring.composite(factors, cfg.weights.as_dict())
            prelim.append((tech_score, sym, df, last, prev, setup, signals, factors, rel))

        prelim.sort(key=lambda t: t[0], reverse=True)

        # ---------------- pass 2: fundamentals for the leaders only
        tradeable = [p for p in prelim if p[5] not in (Setup.AVOID, Setup.NO_SETUP)]
        shortlist = [p[1] for p in tradeable[: cfg.data.fundamentals_top_n]]
        fundamentals_raw: dict[str, dict] = {}
        if shortlist:
            report("Fetching fundamentals", 0, len(shortlist))
            try:
                fundamentals_raw = self.registry.fundamentals(
                    shortlist,
                    progress=lambda d, t: report("Fetching fundamentals", d, t),
                )
            except Exception as exc:  # network layer can raise broadly
                errors.append(f"Fundamentals fetch failed: {exc}")
                log.warning("fundamentals failed: %s", exc)

        # ---------------- build ideas
        report("Building entry and exit plans", 0, len(prelim))
        ideas: list[Idea] = []
        built = 0
        for tech_score, sym, df, last, prev, setup, signals, factors, rel in prelim:
            built += 1
            if built % 10 == 0 or built == len(prelim):
                report("Building entry and exit plans", built, len(prelim))
            member = by_symbol.get(sym)
            fund_dict = fundamentals_raw.get(sym)
            fundamentals = Fundamentals(**fund_dict) if fund_dict else None

            f_score, f_avail, f_sig = scoring.score_fundamental(fundamentals)
            factors = FactorScores(
                trend=factors.trend,
                momentum=factors.momentum,
                volume=factors.volume,
                volatility=factors.volatility,
                fundamental=round(f_score, 1),
                fundamental_available=f_avail,
            )
            all_signals = signals + f_sig
            comp = scoring.composite(factors, cfg.weights.as_dict())

            entry = build_entry(last, prev, setup, cfg.setups)
            ex = build_exit(last, setup, entry, cfg.risk, cfg.indicators)

            levels = None
            action = None
            plan = None
            warnings: list[str] = []

            if ex is not None and entry.state is not EntryState.NO_ENTRY:
                levels = to_levels(entry, ex)
                action = _action_plan(entry, ex)
                entry_px = entry.zone_high if entry.zone_high > 0 else float(last["close"])
                plan = build_position_plan(entry_px, ex.initial_stop, cfg.risk)

                if ex.reward_risk < cfg.risk.min_reward_risk:
                    warnings.append(
                        f"Reward:risk is only {ex.reward_risk:.2f}:1 against the nearest "
                        f"overhead level, below your {cfg.risk.min_reward_risk:.1f}:1 floor. "
                        "Thin edge even if the direction is right."
                    )
                stop_dist_pct = ex.risk_per_share / max(entry_px, 0.01) * 100.0
                if stop_dist_pct > 12:
                    warnings.append(
                        f"The stop sits {stop_dist_pct:.1f}% away, which is wide. "
                        "Position size has been reduced to compensate."
                    )
                if plan.quantity == 0:
                    warnings.append("Not actionable at your capital and risk limit.")

            atr_mult = (
                cfg.risk.atr_stop_multiple_swing
                if setup in (Setup.OVERSOLD_BOUNCE, Setup.PULLBACK_IN_UPTREND, Setup.BREAKOUT)
                else cfg.risk.atr_stop_multiple_trend
            )
            br = (
                scoring.base_rate(
                    df, setup, cfg.setups, cfg.indicators, atr_mult, cfg.base_rate.min_samples
                )
                if cfg.base_rate.enabled
                else None
            )

            confidence, reasons = scoring.assess_confidence(
                factors, setup, context.regime, br, all_signals
            )

            if context.regime is Regime.BEARISH and setup is not Setup.AVOID:
                warnings.append(
                    "Nifty is in a downtrend. Even good-looking long setups have "
                    "materially worse odds right now."
                )

            lo, hi = SETUP_HORIZON_DAYS.get(setup, (0, 0))
            snapshot = {
                k: round(float(last[k]), 2)
                for k in (
                    "close", "sma_fast", "sma_slow", "ema_signal", "rsi", "atr", "atr_pct",
                    "adx", "plus_di", "minus_di", "macd", "macd_signal", "macd_hist",
                    "vol_ratio", "roc_short", "roc_long", "high_52w", "low_52w",
                    "pct_from_52w_high", "swing_low", "bb_upper", "bb_lower",
                )
                if k in last and pd.notna(last[k])
            }
            if rel is not None:
                snapshot["rel_strength_vs_nifty"] = round(rel, 2)

            ideas.append(
                Idea(
                    symbol=sym,
                    company=member.company if member else sym,
                    sector=member.sector if member else "Unknown",
                    last_price=round(float(last["close"]), 2),
                    as_of=df.index[-1].date(),
                    composite_score=comp,
                    factors=factors,
                    setup=setup,
                    horizon_low_days=lo,
                    horizon_high_days=hi,
                    confidence=confidence,
                    confidence_reasons=reasons,
                    levels=levels,
                    action=action,
                    plan=plan,
                    base_rate=br,
                    signals=all_signals,
                    fundamentals=fundamentals,
                    indicators=snapshot,
                    warnings=warnings,
                    spark=_spark(df),
                )
            )

        # Rank: actionable setups first, then score.
        actionable = {
            Setup.BREAKOUT, Setup.TREND_CONTINUATION,
            Setup.PULLBACK_IN_UPTREND, Setup.EARLY_TREND, Setup.OVERSOLD_BOUNCE,
        }
        ideas.sort(
            key=lambda i: (i.setup in actionable, i.composite_score),
            reverse=True,
        )

        report("Done", len(ideas), len(ideas))
        return ScanResult(
            ideas=ideas,
            context=context,
            scanned=len(history),
            skipped=skipped,
            generated_at=started.isoformat(timespec="seconds"),
            universe=cfg.universe.index,
            source_health=self.registry.source_health(),
            errors=errors,
        )

    # -------------------------------------------------------------- single

    def analyse_one(self, symbol: str) -> Idea | None:
        """Full analysis of one symbol, including fundamentals."""
        symbol = symbol.strip().upper()
        history, _ = self.registry.history([symbol])
        if symbol not in history:
            return None

        cfg = self.cfg
        df = ind.compute_all(history[symbol], cfg.indicators)
        if len(df) < 60:
            return None

        last, prev = df.iloc[-1], df.iloc[-2] if len(df) > 1 else df.iloc[-1]
        context = self.market_context()
        idx_hist = self.registry.index_history()
        idx_close = idx_hist["close"] if idx_hist is not None and not idx_hist.empty else None
        rel = (
            ind.relative_strength(df["close"], idx_close, cfg.indicators.roc_long)
            if idx_close is not None
            else None
        )

        setup = classify_setup(last, cfg.setups)
        t_score, t_sig = scoring.score_trend(last, cfg.setups)
        m_score, m_sig = scoring.score_momentum(last, rel, cfg.setups)
        v_score, v_sig = scoring.score_volume(last, cfg.setups)
        vol_score, vol_sig = scoring.score_volatility(last)

        fund_dict = self.registry.fundamentals([symbol]).get(symbol)
        fundamentals = Fundamentals(**fund_dict) if fund_dict else None
        f_score, f_avail, f_sig = scoring.score_fundamental(fundamentals)

        factors = FactorScores(
            trend=round(t_score, 1),
            momentum=round(m_score, 1),
            volume=round(v_score, 1),
            volatility=round(vol_score, 1),
            fundamental=round(f_score, 1),
            fundamental_available=f_avail,
        )
        signals = t_sig + m_sig + v_sig + vol_sig + f_sig
        comp = scoring.composite(factors, cfg.weights.as_dict())

        entry = build_entry(last, prev, setup, cfg.setups)
        ex = build_exit(last, setup, entry, cfg.risk, cfg.indicators)
        levels = to_levels(entry, ex) if ex else None
        action = _action_plan(entry, ex) if ex else None
        entry_px = entry.zone_high if entry.zone_high > 0 else float(last["close"])
        plan = build_position_plan(entry_px, ex.initial_stop, cfg.risk) if ex else None

        atr_mult = (
            cfg.risk.atr_stop_multiple_swing
            if setup in (Setup.OVERSOLD_BOUNCE, Setup.PULLBACK_IN_UPTREND, Setup.BREAKOUT)
            else cfg.risk.atr_stop_multiple_trend
        )
        br = scoring.base_rate(
            df, setup, cfg.setups, cfg.indicators, atr_mult, cfg.base_rate.min_samples
        )
        confidence, reasons = scoring.assess_confidence(
            factors, setup, context.regime, br, signals
        )
        lo, hi = SETUP_HORIZON_DAYS.get(setup, (0, 0))

        member = next((m for m in self.registry.universe() if m.symbol == symbol), None)
        snapshot = {
            k: round(float(last[k]), 2)
            for k in last.index
            if k not in ("open", "high", "low", "volume") and pd.notna(last[k])
        }
        if rel is not None:
            snapshot["rel_strength_vs_nifty"] = round(rel, 2)

        return Idea(
            symbol=symbol,
            company=(member.company if member else (fundamentals.name if fundamentals else symbol)) or symbol,
            sector=(member.sector if member else (fundamentals.sector if fundamentals else "Unknown")) or "Unknown",
            last_price=round(float(last["close"]), 2),
            as_of=df.index[-1].date(),
            composite_score=comp,
            factors=factors,
            setup=setup,
            horizon_low_days=lo,
            horizon_high_days=hi,
            confidence=confidence,
            confidence_reasons=reasons,
            levels=levels,
            action=action,
            plan=plan,
            base_rate=br,
            signals=signals,
            fundamentals=fundamentals,
            indicators=snapshot,
            warnings=[],
            spark=_spark(df),
        )

    def price_series(self, symbol: str, days: int = 180) -> pd.DataFrame | None:
        """Indicator-enriched history for charting."""
        history, _ = self.registry.history([symbol.strip().upper()])
        raw = history.get(symbol.strip().upper())
        if raw is None or raw.empty:
            return None
        return ind.compute_all(raw, self.cfg.indicators).tail(days)

    def index_series(self, days: int = 260) -> pd.DataFrame | None:
        """Nifty history with indicators, for the regime chart."""
        idx = self.registry.index_history()
        if idx is None or idx.empty:
            return None
        return ind.compute_all(idx, self.cfg.indicators).tail(days)


def setup_explanation(setup: Setup) -> str:
    return SETUP_RATIONALE.get(setup, "")


def _spark(df: pd.DataFrame, points: int = 40) -> list[float]:
    """Recent closes for an inline sparkline.

    Rounded to 2dp and capped at `points` values so this stays cheap when it is
    embedded once per row in the scan payload.
    """
    if df is None or df.empty or "close" not in df.columns:
        return []
    tail = df["close"].tail(points).to_numpy(dtype=float)
    return [round(float(v), 2) for v in tail if v == v]  # v == v drops NaN
