#!/usr/bin/env python3
"""Entry point.

  python run.py                  start the dashboard
  python run.py --scan           run one scan and print it to the terminal
  python run.py --holdings       review recorded positions in the terminal
  python run.py --stock TITAN    analyse one symbol in the terminal

The dashboard binds to 127.0.0.1 by default, so it is reachable only from this
machine. Nothing here places an order; execution is manual in your broker app.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)


def main() -> int:
    ap = argparse.ArgumentParser(description="Indian equity analysis dashboard")
    ap.add_argument("--scan", action="store_true", help="run a scan in the terminal and exit")
    ap.add_argument("--holdings", action="store_true", help="review recorded positions and exit")
    ap.add_argument("--stock", metavar="SYMBOL", help="analyse one NSE symbol and exit")
    ap.add_argument("--universe", help="override universe, e.g. NIFTY50 or NIFTY500")
    ap.add_argument(
        "--build-static",
        nargs="?",
        const="docs",
        metavar="OUT_DIR",
        help="render a static snapshot for GitHub Pages (default: docs/)",
    )
    ap.add_argument(
        "--stock-pages",
        type=int,
        default=60,
        help="how many per-stock pages to render in the static build (default 60)",
    )
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)-28s %(message)s",
    )
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("peewee").setLevel(logging.ERROR)

    from analyser.config import load_config

    cfg = load_config()
    if args.universe:
        cfg.universe.index = args.universe.upper().replace(" ", "")

    if args.build_static:
        return _cli_build_static(cfg, args.build_static, args.stock_pages)
    if args.stock:
        return _cli_stock(cfg, args.stock)
    if args.scan:
        return _cli_scan(cfg)
    if args.holdings:
        return _cli_holdings(cfg)

    import uvicorn

    from analyser.web.app import create_app

    # PORT and HOST are how Render, Cloud Run, Fly and Hugging Face Spaces tell
    # an app where to listen. Honour them before the config file, or the
    # platform's health check hits a closed socket and the deploy is marked dead.
    env_port = os.environ.get("PORT")
    env_host = os.environ.get("HOST")
    host = args.host or env_host or cfg.server.host
    port = args.port or (int(env_port) if env_port and env_port.isdigit() else cfg.server.port)

    if host not in ("127.0.0.1", "localhost"):
        print(
            "\n  NOTE: binding to "
            f"{host}, so this dashboard is reachable from outside this machine.\n"
            "  There is no authentication, and /settings plus the holdings routes\n"
            "  accept writes. Put it behind a reverse proxy with auth, or a\n"
            "  firewall rule, before exposing it to a network you do not trust.\n"
        )
    print(f"\n  Dashboard  ->  http://{host}:{port}\n  Ctrl+C to stop\n")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
    return 0


# ------------------------------------------------------------------ CLI modes


def _cli_scan(cfg) -> int:
    from analyser.engine import Analyser

    res = Analyser(cfg).scan()
    c = res.context
    print(f"\n{'=' * 96}")
    print(f"  MARKET REGIME: {c.regime.upper()}   Nifty {c.nifty_last:,.2f} "
          f"({c.nifty_change_pct:+.2f}%)   {c.pct_from_52w_high:+.1f}% from 52w high")
    print(f"  {c.note}")
    print(f"{'=' * 96}\n")

    ready = [i for i in res.ideas if i.action and i.action.is_buyable_now
             and i.plan and i.plan.quantity > 0]
    wait = [i for i in res.ideas if i.action and not i.action.is_buyable_now
            and i.action.entry_state != "No entry"]

    print(f"BUY ZONE ACTIVE ({len(ready)})")
    print("-" * 96)
    if not ready:
        print("  Nothing qualifies. That is a valid answer.\n")
    for i in ready:
        a = i.action
        print(f"\n  {i.symbol:<12} {i.setup:<22} score {i.composite_score:<5} "
              f"{i.confidence} confidence   hold {i.horizon_label}")
        print(f"    BUY    Rs{a.entry_zone_low:,.2f} - Rs{a.entry_zone_high:,.2f}")
        print(f"    STOP   Rs{a.stop:,.2f}  ({a.stop_basis})")
        print(f"    TARGET Rs{a.target_1r:,.2f} / Rs{a.target_2r:,.2f} / Rs{a.target_3r:,.2f}"
              f"   R:R {a.reward_risk}:1")
        print(f"    SIZE   {i.plan.quantity} sh = Rs{i.plan.capital_deployed:,.0f}, "
              f"max loss Rs{i.plan.max_loss_at_stop:,.0f} "
              f"({i.plan.pct_capital_at_risk}% of capital)")
        if i.base_rate:
            flag = "" if i.base_rate.reliable else "  [thin sample]"
            print(f"    PAST   {i.base_rate.win_rate_pct}% hit +1R first over "
                  f"{i.base_rate.samples} occurrences{flag}")
        for w in i.warnings:
            print(f"    WARN   {w}")

    print(f"\n\nWAITING FOR TRIGGER ({len(wait)})")
    print("-" * 96)
    print(f"  {'SYMBOL':<12} {'SETUP':<22} {'LAST':>10} {'BUY ABOVE':>11} "
          f"{'STOP':>10} {'+2R':>10} {'R:R':>5}")
    for i in wait[:25]:
        a = i.action
        trig = f"{a.entry_trigger:,.2f}" if a.entry_trigger else "-"
        print(f"  {i.symbol:<12} {i.setup[:21]:<22} {i.last_price:>10,.2f} {trig:>11} "
              f"{a.stop:>10,.2f} {a.target_2r:>10,.2f} {a.reward_risk:>5}")

    print(f"\n  {res.scanned} scanned, {res.skipped} filtered, universe {res.universe}")
    for e in res.errors:
        print(f"  ! {e}")
    print()
    return 0


def _cli_holdings(cfg) -> int:
    from analyser.engine import Analyser
    from analyser.holdings import HoldingsMonitor, load_holdings

    holdings = load_holdings()
    if not holdings:
        print("\n  No positions recorded. Add them in config/holdings.json or via the dashboard.\n")
        return 0

    reviews = HoldingsMonitor(cfg, Analyser(cfg)).review_all(holdings)
    order = {"EXIT NOW": 0, "TIME EXIT": 1, "TAKE PARTIAL PROFIT": 2,
             "TIGHTEN STOP": 3, "HOLD": 4}
    reviews.sort(key=lambda r: order.get(str(r.verdict), 9))

    print(f"\n{'=' * 96}\n  POSITION REVIEW\n{'=' * 96}")
    for r in reviews:
        print(f"\n  [{r.verdict}]  {r.symbol}  {r.quantity} sh @ Rs{r.entry_price:,.2f}"
              f"  now Rs{r.last_price:,.2f}  "
              f"P&L Rs{r.pnl_abs:,.2f} ({r.pnl_pct:+.2f}%)"
              + (f"  {r.r_multiple}R" if r.r_multiple is not None else ""))
        print(f"    {r.headline}")
        for x in r.reasons:
            print(f"      - {x}")
        for x in r.actions:
            print(f"      > {x}")
    total = sum(r.pnl_abs for r in reviews)
    print(f"\n  Total open P&L: Rs{total:,.2f}\n")
    return 0


def _cli_build_static(cfg, out_dir: str, stock_pages: int) -> int:
    from analyser.export_static import build_static_site

    print(f"\n  Building static site into {out_dir}/ ...\n")
    summary = build_static_site(cfg, out_dir, stock_pages=stock_pages)
    print("\n  Done.")
    for k, v in summary.items():
        print(f"    {k:14s} {v}")
    print(
        "\n  Publish it:\n"
        f"    git add {out_dir} && git commit -m 'Update dashboard snapshot' && git push\n"
        "  Then in GitHub: Settings -> Pages -> Source 'Deploy from a branch',\n"
        f"  branch 'main', folder '/{out_dir}'.\n"
    )
    return 0


def _cli_stock(cfg, symbol: str) -> int:
    from analyser.engine import Analyser, setup_explanation

    idea = Analyser(cfg).analyse_one(symbol)
    if idea is None:
        print(f"\n  No data available for {symbol.upper()}.\n")
        return 1

    print(f"\n{'=' * 96}")
    print(f"  {idea.symbol}  {idea.company}")
    print(f"  Rs{idea.last_price:,.2f} as of {idea.as_of}   {idea.sector}")
    print(f"{'=' * 96}")
    print(f"\n  Setup      {idea.setup}")
    print(f"  Score      {idea.composite_score}   ({idea.confidence} confidence)")
    print(f"  Horizon    {idea.horizon_label}")
    print(f"\n  {setup_explanation(idea.setup)}")

    if idea.action:
        a = idea.action
        print(f"\n  --- WHEN TO INVEST -------------------------------------------")
        print(f"  State      {a.entry_state}")
        print(f"  Zone       Rs{a.entry_zone_low:,.2f} - Rs{a.entry_zone_high:,.2f}")
        print(f"  Condition  {a.entry_condition}")
        print(f"\n  --- WHEN TO STOP ---------------------------------------------")
        print(f"  Stop       Rs{a.stop:,.2f}   ({a.stop_basis})")
        print(f"  Risk/share Rs{a.risk_per_share:,.2f}")
        print(f"  Targets    +1R Rs{a.target_1r:,.2f}   +2R Rs{a.target_2r:,.2f}   "
              f"+3R Rs{a.target_3r:,.2f}")
        print(f"  R:R        {a.reward_risk}:1 to Rs{a.structural_target:,.2f} "
              f"({a.target_basis})")
        print(f"  Scale out  {a.trail_rule}")
        print(f"  Trend exit {a.signal_exit_rule}")
        print(f"  Time stop  {a.time_stop_days} days")
        print(f"  Invalid if {a.entry_invalidation}")
    if idea.plan:
        p = idea.plan
        print(f"\n  --- HOW MUCH -------------------------------------------------")
        print(f"  Quantity   {p.quantity} sh = Rs{p.capital_deployed:,.2f} "
              f"({p.pct_of_capital}% of capital)")
        print(f"  Max loss   Rs{p.max_loss_at_stop:,.2f} ({p.pct_capital_at_risk}% of capital)")
        print(f"  {p.sizing_note}")
    if idea.base_rate:
        b = idea.base_rate
        print(f"\n  --- HISTORICAL BASE RATE -------------------------------------")
        print(f"  {b.win_rate_pct}% reached +1R before the stop over {b.samples} occurrences")
        print(f"  {b.note}")

    print(f"\n  --- SIGNALS ({len(idea.signals)}) ----------------------------------------------")
    for s in idea.signals:
        print(f"  [{s.direction[:4]:>4}] {s.label}: {s.detail}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
