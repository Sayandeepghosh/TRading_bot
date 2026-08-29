"""Static site generator for GitHub Pages.

Why this exists
---------------
GitHub Pages serves files. It does not run Python, so the FastAPI app cannot be
hosted there. It also cannot fetch Yahoo or NSE from browser JavaScript, because
neither sends CORS headers and NSE needs cookie priming.

The way round both problems is to move the Python to build time: run the scan
here (locally, or on a GitHub Actions runner), render every page to plain HTML
with the data baked in, and let Pages serve the result.

What you get and what you lose
------------------------------
Works:      every chart, every level, sorting, filtering, search, CSV download
Frozen:     prices are from build time, not live
Unavailable: settings (no server to write config) and the position monitor,
            which needs the Python exit-rule engine against fresh prices

The position page is therefore replaced with an explanation rather than a
JavaScript reimplementation of the exit rules. Duplicating that logic in the
browser would let the two copies drift, and a stop-loss rule that disagrees with
itself is worse than no page at all.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import PROJECT_ROOT, AppConfig, load_config
from .engine import Analyser, setup_explanation
from .models import ScanResult
from .web.app import _chart_payload, _inr, _money, _compact, _overview, _signed, _split, _stats
from .web.urls import UrlMode, _slug

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

DEFAULT_OUT = PROJECT_ROOT / "docs"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["inr"] = _inr
    env.filters["money"] = _money
    env.filters["signed"] = _signed
    env.filters["compact"] = _compact
    env.globals["now"] = lambda: datetime.now().strftime("%d %b %Y, %H:%M")
    return env


class StaticSiteBuilder:
    def __init__(
        self,
        cfg: AppConfig | None = None,
        out_dir: Path | str | None = None,
        stock_pages: int = 60,
        chart_bars: int = 400,
    ) -> None:
        self.cfg = cfg or load_config()
        self.out = Path(out_dir) if out_dir else DEFAULT_OUT
        self.stock_pages = stock_pages
        self.chart_bars = chart_bars
        self.env = _env()
        self.analyser = Analyser(self.cfg)
        self.built_at = datetime.now().strftime("%d %b %Y, %H:%M IST")
        self.linked: set[str] = set()

    # ------------------------------------------------------------------ build

    def build(self, result: ScanResult | None = None) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)

        if result is None:
            log.info("Running scan for static build ...")
            result = self.analyser.scan(
                progress=lambda s, d, t: log.info(
                    "  %s%s", s, f" {d}/{t}" if t else ""
                )
            )

        buckets = _split(result)
        stats = _stats(result, buckets)
        overview = _overview(result, self.analyser.index_series())
        universe = [
            {"symbol": i.symbol, "company": i.company, "sector": i.sector}
            for i in result.ideas
        ]

        # Decide which symbols get their own page BEFORE rendering the index, so
        # the index only links to pages that will actually exist.
        ordered = buckets["buy_now"] + buckets["waiting"] + buckets["avoid"]
        chosen = ordered[: self.stock_pages]
        self.linked = {i.symbol for i in chosen}

        # A scatter point is clickable, so drop any without a destination.
        if overview.get("scatter"):
            overview["scatter"] = [
                p for p in overview["scatter"] if p["symbol"] in self.linked
            ]

        self._copy_static()
        self._write_index(result, buckets, stats, overview, universe)
        pages = self._write_stock_pages(chosen, universe)
        self._write_positions_page(universe)
        self._write_404(universe)
        self._write_data(result, overview)
        self._write_csv(result)
        self._write_pages_marker()

        summary = {
            "out": str(self.out),
            "generated_at": self.built_at,
            "universe": result.universe,
            "ideas": len(result.ideas),
            "ready": len(buckets["buy_now"]),
            "waiting": len(buckets["waiting"]),
            "stock_pages": pages,
        }
        log.info("Static site written to %s", self.out)
        return summary

    # ---------------------------------------------------------------- pieces

    def _copy_static(self) -> None:
        dest = self.out / "static"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("style.css", "app.js", "charts.js"):
            src = STATIC_DIR / name
            if src.exists():
                shutil.copy2(src, dest / name)

    def _base_ctx(self, mode: UrlMode, universe: list[dict], **kw) -> dict:
        ctx = {
            "cfg": self.cfg,
            "progress": None,
            "has_result": True,
            "explain": setup_explanation,
            "generated_at": self.built_at,
            "universe_json": universe,
        }
        ctx.update(mode.as_globals())
        ctx["has_stock_page"] = lambda sym: sym in getattr(self, "linked", set())
        ctx.update(kw)
        return ctx

    def _write_index(self, result, buckets, stats, overview, universe) -> None:
        mode = UrlMode(static=True, depth=0)
        html = self.env.get_template("index.html").render(
            **self._base_ctx(
                mode,
                universe,
                active="scan",
                result=result,
                buy_now=buckets["buy_now"],
                waiting=buckets["waiting"],
                avoid=buckets["avoid"],
                stats=stats,
                overview=overview,
            )
        )
        (self.out / "index.html").write_text(html, encoding="utf-8")

    def _write_stock_pages(self, chosen, universe) -> int:
        """One page per chosen idea.

        Charts carry most of the payload, so the ordering (ready to buy, then
        waiting, then screened out) means the names you are likely to click get
        pages and the long tail does not.
        """
        mode = UrlMode(static=True, depth=1)
        tpl = self.env.get_template("stock.html")
        dest = self.out / "stock"
        dest.mkdir(parents=True, exist_ok=True)

        written = 0
        for idea in chosen:
            series = self.analyser.price_series(idea.symbol, self.chart_bars)
            chart = _chart_payload(series, self.chart_bars) if series is not None else None
            html = tpl.render(
                **self._base_ctx(
                    mode,
                    universe,
                    active="scan",
                    idea=idea,
                    chart=chart,
                    explanation=setup_explanation(idea.setup),
                )
            )
            (dest / f"{_slug(idea.symbol)}.html").write_text(html, encoding="utf-8")
            written += 1
            if written % 20 == 0:
                log.info("  %d stock pages ...", written)
        return written

    def _write_positions_page(self, universe) -> None:
        """Honest placeholder. See the module docstring for why."""
        mode = UrlMode(static=True, depth=0)
        html = self.env.get_template("positions_static.html").render(
            **self._base_ctx(mode, universe, active="holdings")
        )
        (self.out / "holdings.html").write_text(html, encoding="utf-8")

    def _write_404(self, universe) -> None:
        mode = UrlMode(static=True, depth=0)
        html = self.env.get_template("notfound.html").render(
            **self._base_ctx(mode, universe, active="scan", symbol="That page")
        )
        (self.out / "404.html").write_text(html, encoding="utf-8")

    def _write_data(self, result: ScanResult, overview: dict) -> None:
        d = self.out / "data"
        d.mkdir(parents=True, exist_ok=True)
        payload = result.to_dict()
        payload["generated_at_local"] = self.built_at
        (d / "scan.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (d / "overview.json").write_text(json.dumps(overview, indent=2), encoding="utf-8")

    def _write_csv(self, result: ScanResult) -> None:
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            ["symbol", "company", "sector", "last_price", "setup", "entry_state",
             "score", "confidence", "horizon", "buy_from", "buy_to", "entry_trigger",
             "stop", "risk_per_share", "target_1r", "target_2r", "target_3r",
             "reward_risk", "quantity", "capital_deployed", "max_loss_at_stop",
             "base_rate_pct", "base_rate_samples", "base_rate_reliable", "warnings"]
        )
        for i in result.ideas:
            a, p, b = i.action, i.plan, i.base_rate
            w.writerow(
                [i.symbol, i.company, i.sector, i.last_price, str(i.setup),
                 a.entry_state if a else "", i.composite_score, str(i.confidence),
                 i.horizon_label,
                 a.entry_zone_low if a else "", a.entry_zone_high if a else "",
                 a.entry_trigger if a else "", a.stop if a else "",
                 a.risk_per_share if a else "", a.target_1r if a else "",
                 a.target_2r if a else "", a.target_3r if a else "",
                 a.reward_risk if a else "",
                 p.quantity if p else "", p.capital_deployed if p else "",
                 p.max_loss_at_stop if p else "",
                 b.win_rate_pct if b else "", b.samples if b else "",
                 b.reliable if b else "", " | ".join(i.warnings)]
            )
        (self.out / "export.csv").write_text(buf.getvalue(), encoding="utf-8")

    def _write_pages_marker(self) -> None:
        # Without .nojekyll, Pages runs Jekyll and silently drops paths that
        # begin with an underscore. Cheap insurance.
        (self.out / ".nojekyll").write_text("", encoding="utf-8")


def build_static_site(
    cfg: AppConfig | None = None,
    out_dir: Path | str | None = None,
    stock_pages: int = 60,
    result: ScanResult | None = None,
) -> dict:
    return StaticSiteBuilder(cfg, out_dir, stock_pages).build(result)
