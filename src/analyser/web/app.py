"""FastAPI application.

Design notes that matter:

* A full scan takes 12-60 seconds depending on universe size. Blocking a page
  render on that is the difference between a tool and a product, so scans run on
  a background thread and every page renders immediately. The browser polls
  /api/progress and swaps in results when they land.
* Binds to 127.0.0.1 by default, so it is reachable only from this machine.
  There is no authentication. If you change `server.host` to 0.0.0.0 to reach it
  from your phone, put it behind a reverse proxy with auth first: the holdings
  and settings endpoints accept writes.
* Nothing here places an order. Execution is manual, in your broker app.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import logging
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..auth import (
    COOKIE_NAME,
    SESSION_TTL,
    AuthConfig,
    banner,
    check_token,
    issue_token,
    resolve_auth,
)
from ..config import AppConfig, load_config, save_config
from ..engine import Analyser, setup_explanation
from ..holdings import Holding, HoldingsMonitor, load_holdings, save_holdings
from ..live import LiveQuotes, MarketSession
from ..models import ScanResult, Setup
from .urls import SERVER

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))

# Each stage maps to a slice of the overall progress bar. Price download gets the
# widest band because it genuinely takes the longest, so the bar moves at a rate
# that reflects reality rather than jumping.
_STAGE_BANDS: dict[str, tuple[float, float]] = {
    "Starting": (0.0, 2.0),
    "Loading universe": (2.0, 6.0),
    "Reading market regime": (6.0, 12.0),
    "Downloading price history": (12.0, 66.0),
    "Computing indicators": (66.0, 80.0),
    "Fetching fundamentals": (80.0, 92.0),
    "Building entry and exit plans": (92.0, 99.0),
    "Done": (100.0, 100.0),
}


@dataclass
class Progress:
    active: bool = False
    stage: str = "Idle"
    done: int = 0
    total: int = 0
    pct: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    @property
    def elapsed_s(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.monotonic()
        return round(end - self.started_at, 1)

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "pct": round(self.pct, 1),
            "elapsed_s": self.elapsed_s,
            "error": self.error,
        }


@dataclass
class State:
    """Everything the request handlers share. Guarded by a lock."""

    cfg: AppConfig
    analyser: Analyser = field(init=False)
    monitor: HoldingsMonitor = field(init=False)
    session: MarketSession = field(init=False)
    live: LiveQuotes = field(init=False)
    result: ScanResult | None = None
    progress: Progress = field(default_factory=Progress)
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._build()

    def _build(self) -> None:
        self.analyser = Analyser(self.cfg)
        self.monitor = HoldingsMonitor(self.cfg, self.analyser)
        self.session = MarketSession(self.analyser.registry)
        # Recreate on config change, but keep any existing subscriptions alive so
        # an open browser tab does not go dark after you save settings.
        prev = getattr(self, "live", None)
        self.live = LiveQuotes(registry=self.analyser.registry, session=self.session)
        if prev is not None:
            self.live.subscribe(prev.watched)

    # ------------------------------------------------------------- scanning

    def start_scan(self, force: bool = False) -> bool:
        """Kick a scan onto a background thread. False if one is already running."""
        with self._lock:
            if self.progress.active:
                return False
            self.progress = Progress(
                active=True, stage="Starting", started_at=time.monotonic()
            )
            self._thread = threading.Thread(
                target=self._run, args=(force,), name="scan", daemon=True
            )
            self._thread.start()
            return True

    def _run(self, force: bool) -> None:
        def on_progress(stage: str, done: int, total: int) -> None:
            lo, hi = _STAGE_BANDS.get(stage, (self.progress.pct, self.progress.pct))
            frac = (done / total) if total else 0.0
            self.progress.stage = stage
            self.progress.done = done
            self.progress.total = total
            # Never let the bar go backwards; a stall reads better than a rewind.
            self.progress.pct = max(self.progress.pct, lo + (hi - lo) * frac)

        try:
            result = self.analyser.scan(force_refresh=force, progress=on_progress)
            self.result = result
            self.progress.stage = "Done"
            self.progress.pct = 100.0
            self.progress.error = None
        except Exception as exc:
            log.exception("scan failed")
            self.progress.error = f"{type(exc).__name__}: {exc}"
            self.progress.stage = "Failed"
        finally:
            self.progress.active = False
            self.progress.finished_at = time.monotonic()

    # -------------------------------------------------------------- config

    def apply_config(self, cfg: AppConfig) -> None:
        """Swap in new settings and drop stale results computed under the old ones."""
        with self._lock:
            self.cfg = cfg
            self._build()
            self.result = None
            self.progress = Progress(stage="Settings changed, rescan needed")

    # -------------------------------------------------------------- lookup

    def universe_index(self) -> list[dict]:
        """Symbol list for the search box. Cheap: the universe is cached."""
        try:
            return [
                {"symbol": m.symbol, "company": m.company, "sector": m.sector}
                for m in self.analyser.registry.universe()
            ]
        except Exception:
            return []


# Reachable without a session. Everything else requires one when auth is on.
#   /api/health  the platform health check runs before anyone can log in, so a
#                401 here would make Render mark the deploy dead. It returns
#                only liveness when unauthenticated.
#   /login       obviously
#   /static      CSS and JS are not secrets, and the login page needs them
_PUBLIC_PATHS = frozenset({"/api/health", "/login", "/logout", "/favicon.ico"})
_PUBLIC_PREFIXES = ("/static/",)


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES)


def _safe_next(target: str) -> str:
    """Only allow same-site relative redirects.

    Without this, /login?next=https://evil.example turns the login form into an
    open redirect that lends this app's name to a phishing page.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return "/"
    return target


def _is_https(request: Request) -> bool:
    """True when the original request used TLS, honouring proxy headers."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def create_app(
    cfg: AppConfig | None = None,
    autoscan: bool = True,
    auth: AuthConfig | None = None,
) -> FastAPI:
    state = State(cfg or load_config())
    auth = auth if auth is not None else resolve_auth("127.0.0.1")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Warm the cache immediately so the first page view has data waiting
        # instead of a spinner.
        if autoscan:
            state.start_scan(force=False)
        state.live.start()
        try:
            yield
        finally:
            await state.live.stop()

    app = FastAPI(
        title="Equity Analyser",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.core = state

    app.state.auth = auth

    static_dir = WEB_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ------------------------------------------------------------------- auth

    def _client_addr(request: Request) -> str:
        """Client address for rate limiting.

        Behind Render or Caddy the socket peer is the proxy, so the real client
        is the first entry in X-Forwarded-For. Only the leftmost hop is taken;
        the rest is attacker-controllable.
        """
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _authed(request: Request) -> bool:
        if not auth.enabled:
            return True
        token = request.cookies.get(COOKIE_NAME)
        if token and check_token(auth.secret, token):
            return True
        # Basic auth, so curl and scripts still work without a browser session.
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                raw = base64.b64decode(header[6:]).decode("utf-8", "replace")
                _, _, pw = raw.partition(":")
            except (ValueError, TypeError):
                return False
            return auth.check(pw)
        return False

    @app.middleware("http")
    async def require_auth(request: Request, call_next):
        if not auth.enabled or _is_public(request.url.path):
            return await call_next(request)

        if _authed(request):
            return await call_next(request)

        # HTML gets a login page; anything else gets a clean 401.
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html and request.method == "GET":
            nxt = request.url.path
            if request.url.query:
                nxt += "?" + request.url.query
            return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)
        return JSONResponse(
            {"error": "authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Equity Analyser"'},
        )

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/", error: str = ""):
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        if _authed(request):
            return RedirectResponse(_safe_next(next), status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {
                "cfg": state.cfg,
                "next": _safe_next(next),
                "error": error,
                "generated": auth.generated_password is not None,
                **SERVER.as_globals(),
                "generated_at": "",
            },
            status_code=401 if error else 200,
        )

    @app.post("/login")
    async def login_submit(
        request: Request, password: str = Form(...), next: str = Form("/")
    ):
        addr = _client_addr(request)
        target = _safe_next(next)

        locked = auth.is_locked(addr)
        if locked:
            return RedirectResponse(
                f"/login?next={quote(target, safe='')}"
                f"&error=Too+many+attempts.+Try+again+in+{int(locked)}s.",
                status_code=303,
            )

        if not auth.check(password):
            auth.record_failure(addr)
            log.warning("auth: failed login from %s", addr)
            return RedirectResponse(
                f"/login?next={quote(target, safe='')}&error=Incorrect+password.",
                status_code=303,
            )

        auth.record_success(addr)
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(
            COOKIE_NAME,
            issue_token(auth.secret),
            max_age=SESSION_TTL,
            httponly=True,
            samesite="lax",
            # Only mark Secure when the request actually arrived over TLS,
            # otherwise the cookie is silently dropped on plain-HTTP localhost.
            secure=_is_https(request),
            path="/",
        )
        log.info("auth: login from %s", addr)
        return resp

    @app.post("/logout")
    @app.get("/logout")
    async def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp

    TEMPLATES.env.filters["inr"] = _inr
    TEMPLATES.env.filters["money"] = _money
    TEMPLATES.env.filters["signed"] = _signed
    TEMPLATES.env.filters["compact"] = _compact
    TEMPLATES.env.globals["now"] = lambda: datetime.now().strftime("%d %b %Y, %H:%M")
    TEMPLATES.env.globals.update(SERVER.as_globals())
    TEMPLATES.env.globals["generated_at"] = ""

    def ctx(request: Request, **kw) -> dict:
        base = {
            "cfg": state.cfg,
            "progress": state.progress,
            "has_result": state.result is not None,
            "explain": setup_explanation,
            "auth_enabled": auth.enabled,
        }
        base.update(kw)
        return base

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        result = state.result
        buckets = _split(result)
        overview = {}
        if result is not None:
            # Off the event loop: this touches the price cache, and a stall here
            # would delay live quote pushes to every connected browser.
            overview = await asyncio.to_thread(
                lambda: _overview(result, state.analyser.index_series())
            )
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            ctx(
                request,
                active="scan",
                result=result,
                buy_now=buckets["buy_now"],
                waiting=buckets["waiting"],
                avoid=buckets["avoid"],
                stats=_stats(result, buckets),
                overview=overview,
            ),
        )

    @app.get("/stock/{symbol}", response_class=HTMLResponse)
    async def stock_detail(request: Request, symbol: str):
        symbol = _clean_symbol(symbol)
        if not symbol:
            raise HTTPException(400, "Invalid symbol")
        idea = await asyncio.to_thread(state.analyser.analyse_one, symbol)
        if idea is None:
            return TEMPLATES.TemplateResponse(
                request,
                "notfound.html",
                ctx(request, active="scan", symbol=symbol),
                status_code=404,
            )
        series = await asyncio.to_thread(state.analyser.price_series, symbol, 400)
        return TEMPLATES.TemplateResponse(
            request,
            "stock.html",
            ctx(
                request,
                active="scan",
                idea=idea,
                chart=_chart_payload(series) if series is not None else None,
                explanation=setup_explanation(idea.setup),
            ),
        )

    @app.get("/holdings", response_class=HTMLResponse)
    async def holdings_page(request: Request):
        holdings = load_holdings()
        reviews = await asyncio.to_thread(state.monitor.review_all, holdings)
        rank = {
            "EXIT NOW": 0,
            "TIME EXIT": 1,
            "TAKE PARTIAL PROFIT": 2,
            "TIGHTEN STOP": 3,
            "HOLD": 4,
        }
        reviews.sort(key=lambda r: rank.get(str(r.verdict), 9))
        invested = sum(r.entry_price * r.quantity for r in reviews)
        current = sum(r.last_price * r.quantity for r in reviews)
        total_pnl = current - invested
        return TEMPLATES.TemplateResponse(
            request,
            "holdings.html",
            ctx(
                request,
                active="holdings",
                reviews=reviews,
                unresolved=[
                    h for h in holdings if h.symbol not in {r.symbol for r in reviews}
                ],
                invested=invested,
                current=current,
                total_pnl=total_pnl,
                pnl_pct=(total_pnl / invested * 100.0) if invested else 0.0,
                action_count=sum(
                    1 for r in reviews if str(r.verdict) not in ("HOLD",)
                ),
                today=datetime.now().strftime("%Y-%m-%d"),
            ),
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, saved: bool = False, err: str = ""):
        return TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            ctx(request, active="settings", saved=saved, err=err),
        )

    @app.post("/settings")
    async def settings_save(
        capital: float = Form(...),
        risk_per_trade_pct: float = Form(...),
        max_position_pct: float = Form(...),
        min_reward_risk: float = Form(...),
        index: str = Form(...),
        min_avg_turnover_cr: float = Form(...),
        fundamentals_top_n: int = Form(...),
        w_trend: float = Form(...),
        w_momentum: float = Form(...),
        w_volume: float = Form(...),
        w_volatility: float = Form(...),
        w_fundamental: float = Form(...),
    ):
        try:
            data = state.cfg.model_dump()
            data["risk"].update(
                capital=capital,
                risk_per_trade_pct=risk_per_trade_pct,
                max_position_pct=max_position_pct,
                min_reward_risk=min_reward_risk,
            )
            data["universe"].update(
                index=index, min_avg_turnover_cr=min_avg_turnover_cr
            )
            data["data"].update(fundamentals_top_n=fundamentals_top_n)

            total = w_trend + w_momentum + w_volume + w_volatility + w_fundamental
            if total <= 0:
                raise ValueError("Weights cannot all be zero.")
            # Normalise so the user does not have to make them sum to 1 by hand.
            data["weights"] = {
                "trend": round(w_trend / total, 4),
                "momentum": round(w_momentum / total, 4),
                "volume": round(w_volume / total, 4),
                "volatility": round(w_volatility / total, 4),
                "fundamental": round(w_fundamental / total, 4),
            }
            drift = round(1.0 - sum(data["weights"].values()), 4)
            if drift:
                data["weights"]["trend"] = round(data["weights"]["trend"] + drift, 4)

            new_cfg = AppConfig.model_validate(data)
            save_config(new_cfg)
        except Exception as exc:
            msg = str(exc).split("\n")[0][:200]
            return RedirectResponse(f"/settings?err={msg}", status_code=303)

        state.apply_config(new_cfg)
        state.start_scan(force=False)
        return RedirectResponse("/settings?saved=1", status_code=303)

    # ----------------------------------------------------------------- actions

    @app.post("/refresh")
    async def refresh(force: bool = Query(True)):
        state.start_scan(force=force)
        return RedirectResponse("/", status_code=303)

    @app.post("/holdings/add")
    async def add_holding(
        symbol: str = Form(...),
        quantity: int = Form(...),
        entry_price: float = Form(...),
        entry_date: str = Form(...),
        stop_price: str = Form(""),
        target_price: str = Form(""),
        notes: str = Form(""),
    ):
        sym = _clean_symbol(symbol)
        if not sym or quantity < 1 or entry_price <= 0:
            return RedirectResponse("/holdings?err=1", status_code=303)
        holdings = load_holdings()
        holdings.append(
            Holding(
                symbol=sym,
                quantity=quantity,
                entry_price=entry_price,
                entry_date=entry_date,
                stop_price=_maybe_float(stop_price),
                target_price=_maybe_float(target_price),
                notes=notes.strip()[:300],
            )
        )
        save_holdings(holdings)
        return RedirectResponse("/holdings", status_code=303)

    @app.post("/holdings/delete")
    async def delete_holding(symbol: str = Form(...), entry_date: str = Form(...)):
        sym = _clean_symbol(symbol)
        save_holdings(
            [
                h
                for h in load_holdings()
                if not (h.symbol == sym and h.entry_date == entry_date)
            ]
        )
        return RedirectResponse("/holdings", status_code=303)

    @app.post("/holdings/stop")
    async def update_stop(
        symbol: str = Form(...), entry_date: str = Form(...), stop_price: str = Form("")
    ):
        """Accept the suggested trailing stop in one click."""
        sym = _clean_symbol(symbol)
        new_stop = _maybe_float(stop_price)
        holdings = load_holdings()
        for h in holdings:
            if h.symbol == sym and h.entry_date == entry_date:
                h.stop_price = new_stop
                break
        save_holdings(holdings)
        return RedirectResponse("/holdings", status_code=303)

    @app.get("/export.csv")
    async def export_csv():
        result = state.result
        if result is None:
            raise HTTPException(409, "No scan data yet. Run a scan first.")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "symbol", "company", "sector", "last_price", "setup", "entry_state",
                "score", "confidence", "horizon", "buy_from", "buy_to",
                "entry_trigger", "stop", "risk_per_share", "target_1r", "target_2r",
                "target_3r", "reward_risk", "quantity", "capital_deployed",
                "max_loss_at_stop", "base_rate_pct", "base_rate_samples",
                "base_rate_reliable", "warnings",
            ]
        )
        for i in result.ideas:
            a, p, b = i.action, i.plan, i.base_rate
            w.writerow(
                [
                    i.symbol, i.company, i.sector, i.last_price, str(i.setup),
                    a.entry_state if a else "",
                    i.composite_score, str(i.confidence), i.horizon_label,
                    a.entry_zone_low if a else "", a.entry_zone_high if a else "",
                    a.entry_trigger if a else "",
                    a.stop if a else "", a.risk_per_share if a else "",
                    a.target_1r if a else "", a.target_2r if a else "",
                    a.target_3r if a else "", a.reward_risk if a else "",
                    p.quantity if p else "", p.capital_deployed if p else "",
                    p.max_loss_at_stop if p else "",
                    b.win_rate_pct if b else "", b.samples if b else "",
                    b.reliable if b else "",
                    " | ".join(i.warnings),
                ]
            )
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="analyser-{result.universe}-{stamp}.csv"'
                )
            },
        )

    # --------------------------------------------------------------------- api

    @app.get("/api/progress")
    async def api_progress():
        p = state.progress.to_dict()
        p["has_result"] = state.result is not None
        p["generated_at"] = state.result.generated_at if state.result else None
        return JSONResponse(p)

    @app.get("/api/search")
    async def api_search(q: str = Query("", max_length=40)):
        term = q.strip().upper()
        if len(term) < 1:
            return JSONResponse([])
        rows = state.universe_index()
        starts = [r for r in rows if r["symbol"].startswith(term)]
        contains = [
            r
            for r in rows
            if r not in starts
            and (term in r["symbol"] or term in r["company"].upper())
        ]
        return JSONResponse((starts + contains)[:12])

    @app.get("/api/scan")
    async def api_scan():
        if state.result is None:
            return JSONResponse(
                {"error": state.progress.error or "no scan yet", "progress": state.progress.to_dict()},
                status_code=503,
            )
        return JSONResponse(state.result.to_dict())

    @app.get("/api/stock/{symbol}")
    async def api_stock(symbol: str):
        idea = await asyncio.to_thread(state.analyser.analyse_one, _clean_symbol(symbol))
        if idea is None:
            raise HTTPException(404, f"No data for {symbol}")
        return JSONResponse(idea.to_dict())

    @app.get("/api/chart/{symbol}")
    async def api_chart(symbol: str, bars: int = Query(400, ge=60, le=1000)):
        """Raw OHLCV plus indicator series, for charting a symbol on demand."""
        sym = _clean_symbol(symbol)
        series = (
            await asyncio.to_thread(state.analyser.price_series, sym, bars) if sym else None
        )
        if series is None or series.empty:
            raise HTTPException(404, f"No chart data for {symbol}")
        return JSONResponse({"symbol": sym, **_chart_payload(series, bars)})

    @app.get("/api/overview")
    async def api_overview():
        if state.result is None:
            raise HTTPException(503, "No scan data yet")
        return JSONResponse(_overview(state.result, state.analyser.index_series()))

    # -------------------------------------------------------------------- live

    @app.get("/api/session")
    async def api_session():
        """Market open/closed, and how the answer was reached."""
        s = state.session.state().to_dict()
        s["watched"] = state.live.watched
        s["last_poll"] = state.live.last_poll
        s["poll_seconds"] = (
            state.live.poll_open if s["is_open"] else state.live.poll_closed
        )
        return JSONResponse(s)

    @app.get("/api/intraday/{symbol}")
    async def api_intraday(symbol: str):
        """Today's 1-minute bars. Subscribes the symbol so the poller keeps it warm."""
        sym = _clean_symbol(symbol)
        if not sym:
            raise HTTPException(400, "Invalid symbol")

        # prime() rather than subscribe(): an HTTP caller has no disconnect event,
        # so a reference taken here would never be released.
        await state.live.prime([sym])
        bars = state.live.bars(sym)
        snap = state.live.snapshot([sym])
        return JSONResponse(
            {
                "symbol": sym,
                "bars": bars,
                "quote": snap["quotes"].get(sym),
                "session": snap["session"],
                "last_poll": state.live.last_poll,
            }
        )

    @app.websocket("/ws/quotes")
    async def ws_quotes(ws: WebSocket):
        """Push live quotes.

        Protocol, client to server:
          {"action": "subscribe",   "symbols": ["TITAN", ...]}
          {"action": "unsubscribe", "symbols": [...]}
          {"action": "ping"}

        Server to client:
          {"type": "quotes",  "quotes": {...}, "session": {...}}
          {"type": "session", "session": {...}}
          {"type": "pong"}
        """
        # Middleware does not cover WebSocket scope, so gate it here. Browsers
        # send same-origin cookies on the upgrade request, which is exactly why
        # sessions are cookie-based rather than HTTP Basic.
        if auth.enabled:
            token = ws.cookies.get(COOKIE_NAME)
            if not token or not check_token(auth.secret, token):
                await ws.close(code=1008, reason="authentication required")
                return

        await ws.accept()
        queue = state.live.add_client()
        mine: set[str] = set()

        async def pump() -> None:
            """Queue to socket."""
            while True:
                msg = await queue.get()
                # Only forward symbols this client asked for.
                if msg.get("type") == "quotes" and mine:
                    quotes = {k: v for k, v in msg.get("quotes", {}).items() if k in mine}
                    if not quotes:
                        continue
                    msg = {**msg, "quotes": quotes}
                await ws.send_json(msg)

        async def listen() -> None:
            """Socket to subscription state."""
            while True:
                data = await ws.receive_json()
                action = str(data.get("action", "")).lower()

                if action == "subscribe":
                    syms = [
                        s for s in (_clean_symbol(x) for x in data.get("symbols", [])) if s
                    ]
                    taken = state.live.subscribe(syms)
                    mine.update(taken)
                    # Send whatever is already cached straight away, then fill in
                    # anything missing so a closed-market visit is not blank.
                    await ws.send_json(state.live.snapshot(list(mine)))
                    if taken:
                        asyncio.create_task(state.live.prime(taken))

                elif action == "unsubscribe":
                    syms = [
                        s for s in (_clean_symbol(x) for x in data.get("symbols", [])) if s
                    ]
                    state.live.unsubscribe(syms)
                    mine.difference_update(syms)

                elif action == "ping":
                    await ws.send_json(
                        {"type": "pong", "session": state.session.state().to_dict()}
                    )

        pump_task = asyncio.create_task(pump())
        listen_task = asyncio.create_task(listen())
        try:
            await asyncio.wait(
                {pump_task, listen_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except WebSocketDisconnect:
            pass
        finally:
            for t in (pump_task, listen_task):
                t.cancel()
            state.live.remove_client(queue)
            # Release this client's references so the poller stops fetching
            # symbols nobody is watching any more.
            state.live.unsubscribe(list(mine))

    @app.get("/api/holdings")
    async def api_holdings():
        reviews = await asyncio.to_thread(state.monitor.review_all, load_holdings())
        return JSONResponse([r.to_dict() for r in reviews])

    @app.get("/api/health")
    async def api_health(request: Request):
        """Public liveness probe.

        Platform health checks run before anyone can log in, so this must never
        401. When unauthenticated it reports only that the process is alive, and
        leaks nothing about the portfolio or configuration.
        """
        if auth.enabled and not _authed(request):
            return {"status": "ok", "auth": "required"}
        return {
            "status": "ok",
            "auth": "enabled" if auth.enabled else "disabled",
            "universe": state.cfg.universe.index,
            "has_result": state.result is not None,
            "last_scan": state.result.generated_at if state.result else None,
            "progress": state.progress.to_dict(),
            "server_time": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    return app


# ------------------------------------------------------------------- helpers


def _split(result: ScanResult | None) -> dict[str, list]:
    """Partition ideas into the three buckets the dashboard leads with."""
    buy_now, waiting, avoid = [], [], []
    if result is not None:
        for idea in result.ideas:
            a = idea.action
            if a is None:
                avoid.append(idea)
            elif a.entry_state == "Buy zone active" and idea.plan and idea.plan.quantity > 0:
                buy_now.append(idea)
            elif a.entry_state in ("Wait for trigger", "Extended, wait for pullback"):
                waiting.append(idea)
            else:
                avoid.append(idea)
    return {"buy_now": buy_now, "waiting": waiting, "avoid": avoid}


def _stats(result: ScanResult | None, buckets: dict[str, list]) -> dict:
    """Headline tiles for the top of the dashboard."""
    if result is None:
        return {}
    buy = buckets["buy_now"]
    high_conf = [i for i in buy if str(i.confidence) == "High"]
    reliable = [
        i for i in buy if i.base_rate and i.base_rate.reliable and i.base_rate.win_rate_pct >= 50
    ]
    total_risk = sum(i.plan.max_loss_at_stop for i in buy if i.plan)
    return {
        "buy_now": len(buy),
        "waiting": len(buckets["waiting"]),
        "screened_out": len(buckets["avoid"]),
        "scanned": result.scanned,
        "high_conf": len(high_conf),
        "reliable": len(reliable),
        "total_risk": total_risk,
        "avg_rr": (
            round(sum(i.action.reward_risk for i in buy if i.action) / len(buy), 2)
            if buy
            else 0.0
        ),
    }


def _chart_payload(df, bars: int = 400) -> dict:
    """Series for the multi-panel stock chart.

    Sends more history than the chart initially shows, so the range selector can
    zoom out to 1Y or All without another round trip.
    """
    d = df.tail(bars)
    cols = [
        "open", "high", "low", "close", "volume",
        "sma_fast", "sma_slow", "ema_signal",
        "rsi", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_mid", "bb_lower", "atr", "adx", "vol_avg",
    ]
    out: dict = {"dates": [x.strftime("%Y-%m-%d") for x in d.index]}
    for c in cols:
        out[c] = [_n(v) for v in d[c]] if c in d.columns else []
    return out


def _overview(result: ScanResult | None, index_df) -> dict:
    """Aggregate data for the dashboard charts.

    Four views, each answering a question you would otherwise have to work out
    by reading the whole table:
      regime  - is the index trending, and where does price sit vs its averages
      setups  - what kind of market is this (breakouts vs pullbacks vs nothing)
      sectors - where is the strength concentrated
      scatter - reward:risk against score, so outliers are obvious
    """
    out: dict = {}

    if index_df is not None and not index_df.empty:
        d = index_df.tail(260)
        out["regime"] = {
            "dates": [x.strftime("%Y-%m-%d") for x in d.index],
            "close": [_n(v) for v in d["close"]],
            "sma_fast": [_n(v) for v in d["sma_fast"]] if "sma_fast" in d else [],
            "sma_slow": [_n(v) for v in d["sma_slow"]] if "sma_slow" in d else [],
        }

    if result is None:
        return out

    setups: dict[str, int] = {}
    sectors: dict[str, int] = {}
    scatter: list[dict] = []

    for i in result.ideas:
        key = str(i.setup)
        setups[key] = setups.get(key, 0) + 1

        actionable = i.action is not None and i.setup not in (
            Setup.AVOID,
            Setup.NO_SETUP,
        )
        if actionable:
            sectors[i.sector] = sectors.get(i.sector, 0) + 1
            if i.action:
                scatter.append(
                    {
                        "symbol": i.symbol,
                        "score": i.composite_score,
                        "rr": i.action.reward_risk,
                        "setup": key,
                        "ready": i.action.entry_state == "Buy zone active",
                        "base": i.base_rate.win_rate_pct if i.base_rate else None,
                    }
                )

    out["setups"] = sorted(
        [{"label": k, "count": v} for k, v in setups.items()],
        key=lambda x: x["count"],
        reverse=True,
    )
    out["sectors"] = sorted(
        [{"label": k, "count": v} for k, v in sectors.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]
    out["scatter"] = scatter
    return out


def _n(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 2)  # f != f catches NaN


def _clean_symbol(s: str) -> str:
    """NSE symbols are alphanumeric plus & and -. Reject anything else."""
    s = (s or "").strip().upper()
    return s if s and all(c.isalnum() or c in "&-" for c in s) else ""


def _maybe_float(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return f if f > 0 else None


def _inr(value) -> str:
    """Indian digit grouping: 12,34,567.89"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "-"
    neg = f < 0
    int_part, dec = f"{abs(f):.2f}".split(".")
    if len(int_part) > 3:
        head, tail = int_part[:-3], int_part[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        int_part = ",".join(groups) + "," + tail
    out = f"{int_part}.{dec}"
    return f"-{out}" if neg else out


def _money(value) -> str:
    """Sign outside the symbol: -Rs2,945.00, never Rs-2,945.00."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "-"
    return ("-Rs" if f < 0 else "Rs") + _inr(abs(f))


def _signed(value) -> str:
    try:
        return f"{float(value):+.2f}"
    except (TypeError, ValueError):
        return "-"


def _compact(value) -> str:
    """Large rupee figures as crore / lakh."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "-"
    a = abs(f)
    if a >= 1e7:
        return f"{f / 1e7:,.2f} cr"
    if a >= 1e5:
        return f"{f / 1e5:,.2f} L"
    return _inr(f)
