<div align="center">

# 📈 Equity Analyser

### An NSE stock screener that answers two questions most screeners dodge: **when do I buy, and when do I get out?**

[**🔴 Live dashboard →**](https://sayandeepghosh.github.io/TRading_bot/)

[![Live](https://img.shields.io/badge/dashboard-live-26d07c?logo=githubpages&logoColor=white)](https://sayandeepghosh.github.io/TRading_bot/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Plotly](https://img.shields.io/badge/charts-Plotly-3F4F75?logo=plotly&logoColor=white)](https://plotly.com/javascript/)
[![Auth](https://img.shields.io/badge/auth-fail%20closed-3b9dff)](#-security)
[![Cost](https://img.shields.io/badge/running%20cost-%E2%82%B90-26d07c)](#-hosting-for-free)
![Orders](https://img.shields.io/badge/places%20orders-never-f4574f)

*Free data · runs on your laptop or a free host · advisory only, it never touches your broker account*

</div>

---

## ⚠️ Read this before anything else

This tool applies **fixed rules** to public market data and shows you the result. It is **not investment advice**, and it does **not** predict prices.

> Nobody, including this software, knows which trade will make money.

- Every level it shows is a **rule**, not a forecast.
- Historical win rates describe **the past**. They do not carry forward.
- You place your own orders, manually. You carry the full loss.

On the current live snapshot, the honestly-measured win rates across 36 setups with adequate sample sizes run from **28% to 67%**. That is what rule-based trading actually looks like. Anything advertising 90% is not measuring properly.

**The real value is that your stop is decided before you own the position, while you are still calm.**

---

## Why this exists

Most screeners hand you a ranked list and leave the hard part to you. You get "TITAN, score 82" and still have to decide what price to pay, where you're wrong, how many shares, and when to give up.

This one commits to all four, in writing, before you enter.

<table>
<tr><th width="22%">Question</th><th>What you get</th></tr>
<tr>
<td><b>🟢 When do I buy?</b></td>
<td>A <b>price condition</b>, not a vibe:<br>
<i>"Buy only on a daily close above ₹1,352.35 with volume at least 1.5× the 20-day average. No close above the level means no trade."</i></td>
</tr>
<tr>
<td><b>🔴 Where am I wrong?</b></td>
<td>A stop placed below the swing low or 2.5× ATR, whichever sits <b>further</b> out — because being shaken out by noise is the more expensive mistake.</td>
</tr>
<tr>
<td><b>🎯 When do I take profit?</b></td>
<td>An R-multiple ladder. Sell a third at +1R and move the stop to breakeven, so the trade can no longer lose money.</td>
</tr>
<tr>
<td><b>📏 How much?</b></td>
<td>Size derived from what you're willing to <b>lose</b>, never from what you hope to gain.</td>
</tr>
</table>

```
Capital ₹1,00,000 · risk 1% · entry ₹8,131 · stop ₹7,820
  → risk per share ₹311  →  3 shares  →  worst case ₹933
```

A wider stop mechanically means fewer shares. Your rupee risk stays constant. That's the whole trick.

---

## What it looks like

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ▲ Equity Analyser    Opportunities  My positions  Settings   ⌕  ● Live   │
├──────────────────────────────────────────────────────────────────────────┤
│  MARKET REGIME: BEARISH        Nifty 24,175.65  -0.42%   ▼ below 200DMA  │
│  Long setups fail more often in this regime regardless of the chart.     │
├──────────────────────────────────────────────────────────────────────────┤
│   21       25       3.16       2        4.9k      100                     │
│  READY   WAITING   AVG R:R   HIGH     RISK AT   SCANNED                  │
│                              CONF     STOPS                              │
├──────────────────────────────────────────────────────────────────────────┤
│  ╭─ Nifty + moving averages ─╮  ╭─ Score vs reward:risk ──────────────╮  │
│  │    ╱╲    ╱╲___             │  │        · TITAN      · JSWSTEEL     │  │
│  │ ╲╱    ╲╱      ╲___         │  │   · LT        · EICHERMOT          │  │
│  ╰────────────────────────────╯  ╰────────────────────────────────────╯  │
├──────────────────────────────────────────────────────────────────────────┤
│  EICHERMOT  Eicher Motors        Trend continuation · Medium · 82.3      │
│  ┌────────────┬────────────┬────────────┬────────────┐                   │
│  │ 1 · INVEST │ 2 · STOP   │ 3 · TARGET │ 4 · SIZE   │                   │
│  │ 7,966      │ 7,777      │ 8,718      │ 1 sh       │                   │
│  │ – 8,090    │ ▼2.5×ATR   │ +2R  4.8:1 │ max ₹314   │                   │
│  └────────────┴────────────┴────────────┴────────────┘                   │
│  Exit rules · Scale out · Trend break · Time stop 120d · Invalidation    │
│  Historically: hit +1R before stop 33% of the time over 10 occurrences   │
└──────────────────────────────────────────────────────────────────────────┘
```

Or just [**open the live one**](https://sayandeepghosh.github.io/TRading_bot/).

---

## 🚀 Get it running

```bash
git clone https://github.com/Sayandeepghosh/TRading_bot.git
cd TRading_bot
./start.sh
```

Open **http://127.0.0.1:8000**. That's it — `start.sh` creates the virtualenv, installs the 11 dependencies, and starts scanning in the background. Pages render **instantly** with a live progress bar instead of blocking for 30 seconds.

<details>
<summary><b>Terminal modes</b></summary>

```bash
./start.sh                        # dashboard
./start.sh --scan                 # one scan, printed to the terminal
./start.sh --holdings             # review your recorded positions
./start.sh --stock TITAN          # analyse a single symbol
./start.sh --universe NIFTY500    # widen the net
./start.sh --build-static docs    # render a static snapshot
```

</details>

<details>
<summary><b>Scan times and memory</b></summary>

| Universe | Cold | Warm cache | Peak RAM |
|---|---|---|---|
| NIFTY50 | ~30s | ~12s | 224 MB |
| NIFTY100 | ~45s | ~26s | 305 MB |
| NIFTY200 | ~90s | ~43s | — |
| NIFTY500 | ~4min | ~2min | — |

Two passes by design: technicals batch-download across the whole universe, then fundamentals are fetched **only for the leaders**, because that costs one HTTP round trip per company.

Memory matters if you deploy to a free host with a 512 MB cap. NIFTY200 and above will be OOM-killed there.

</details>

---

## ✨ What's in it

### 📊 Interactive charts

- **Four-panel daily chart** — candles with Bollinger fill, 50/200 DMA, 21 EMA, volume coloured by direction, MACD, RSI. Range buttons 1M→All, drag to pan, scroll to zoom.
- **Your decision levels drawn on** — buy zone as a blue band, stop as a red line with the loss region shaded beneath, R-targets dotted and labelled at the edge. You can see how far price sits from every decision without doing arithmetic.
- **Intraday session chart** — 1-minute bars, refreshing while the market is open.
- **Overview charts** — regime, a score-vs-reward:risk scatter where clicking a point opens that stock, setup mix, sector concentration.
- **Sparklines** in the watchlist, hand-rolled SVG rather than dozens of plotting instances.

### 🔴 Live streaming

WebSocket push, no page reload. Prices flash when they change. A connection pill shows Live / Pre-open / Closed, and **only the open-market state pulses** — a blinking dot next to "Closed" would claim activity that isn't happening.

Polling runs at 25s while open, **stops entirely when closed**, and only fetches symbols someone is actually watching (reference-counted, so an idle dashboard costs nothing). Session state cross-checks NSE's own status against the clock, so **trading holidays aren't mistaken for trading days**.

### 📈 Base rates that don't flatter themselves

Every setup is replayed through **that stock's own history**. For each past occurrence it walks forward bar by bar and records whether the target or the stop was hit **first**. That's path-dependent, so it can't cheat the way a "return after N days" calculation does — which would happily count a trade that dropped 20% before recovering as a win.

Thin samples are **labelled unreliable** rather than rounded into a confident-looking number.

### 📋 Position monitor

Record what you bought; every visit re-checks it and returns one mechanical verdict:

| Verdict | Trigger |
|---|---|
| 🔴 **EXIT NOW** | Stop breached, or the trend that justified the position has broken |
| 🟠 **TIME EXIT** | Held past its horizon without working. Capital is idle |
| 🟢 **TAKE PARTIAL PROFIT** | Up 2R+. Bank a third, trail the rest |
| 🔵 **TIGHTEN STOP** | Up 1R. Move to breakeven so it can no longer lose |
| ⚪ **HOLD** | Nothing changed. Don't tinker |

The point is removing the moment where you talk yourself into holding a loser because it "has to come back".

### 🔎 Transparency by default

Every ranking opens into the signals that produced it — five factor scores, the bullish and bearish evidence side by side, and why the confidence label landed where it did. **Bearish signals are shown as prominently as bullish ones.**

---

## 🧮 How the scoring works

Five factors, weighted, each 0–100. Weights are editable in the app and normalise automatically.

| Factor | Default | What it reads |
|---|:---:|---|
| **Trend** | 30% | Price vs 50/200 DMA, MA stacking, ADX strength, distance from 52-week high |
| **Momentum** | 25% | RSI zone, MACD, 1M/3M rate of change, relative strength vs Nifty |
| **Volume** | 15% | Surge vs 20-day average, OBV slope, liquidity floor |
| **Volatility** | 10% | ATR% — rewards tradeable range, penalises extremes in **both** directions |
| **Fundamental** | 20% | P/E, ROE, debt/equity, earnings and revenue growth, profit margin |

When fundamentals are unavailable the weight is **redistributed across the technical factors** rather than scoring a silent neutral 50, so missing data can never masquerade as bad data.

Scores exist to **rank and explain**. They are **not probabilities** — 80 does not mean an 80% chance of profit, and the UI never presents it that way.

<details>
<summary><b>Setups and their horizons</b></summary>

| Setup | Horizon | Character |
|---|---|---|
| **Trend continuation** | 4–17 weeks | Established trend. Hold through noise |
| **Early trend** | 3–13 weeks | Forming but not ADX-confirmed. Earlier entry, higher failure rate |
| **Breakout** | 10–40 days | At 52-week highs on volume. Resolves fast, either way |
| **Pullback in uptrend** | 1–4 weeks | Better price than chasing, with the risk it keeps falling |
| **Oversold bounce** | 5–15 days | Counter-trend, short leash. Never average down |
| **Avoid** | — | Downtrend. Long ideas have poor odds here |

Horizons describe how long the **setup** typically takes to resolve. They are not profit forecasts. If the horizon elapses without reaching +1R, the time stop closes it.

</details>

<details>
<summary><b>Indicators — all implemented in numpy/pandas</b></summary>

`sma` · `ema` · `wilder` · `rsi` · `true_range` · `atr` · `adx` · `macd` · `bollinger` · `roc` · `obv` · `slope_pct` · `rolling_max` · `rolling_min` · `swing_low` · `relative_strength`

No TA-Lib, so `pip install` works without a system C library. Wilder's smoothing is used where convention varies, matching what charting platforms display. Nothing forward-fills or back-fills, because that would leak future information into a historical reading.

</details>

---

## ⚙️ Configuration

Everything lives in [`config/config.yaml`](config/config.yaml), commented throughout. The **Settings page** edits the important parts from the browser and **writes the comments back intact**.

```yaml
risk:
  capital: 100000            # your investable capital
  risk_per_trade_pct: 1.0    # loss if the stop hits. keep this small
  max_position_pct: 15.0     # concentration cap per stock
  min_reward_risk: 1.5       # flag anything paying less
```

The two that change your results most are **capital** and **risk per trade**, because every position size derives from them.

Environment variables override the file, which is how you configure a hosted deploy without committing personal numbers:

| Variable | Purpose |
|---|---|
| `ANALYSER_UNIVERSE` | `NIFTY50` … `NIFTY500` |
| `ANALYSER_CAPITAL` | Your real capital, kept out of git |
| `ANALYSER_RISK_PCT` | Risk per trade |
| `ANALYSER_MAX_POS_PCT` | Position cap |
| `ANALYSER_FUND_TOP_N` | How many leaders get fundamentals |

> **This repo is public.** `config.yaml` ships the ₹1,00,000 *default*, not anyone's real figure. Keep it that way and set `ANALYSER_CAPITAL` in your host's dashboard instead.

---

## 🔐 Security

Authentication is built in and it **fails closed**. The rule is decided by the bind address:

| Bind | Password set | Result |
|---|:---:|---|
| `127.0.0.1` | no | Auth **off** — a login prompt on your own laptop protects nothing |
| `127.0.0.1` | yes | Auth on |
| `0.0.0.0` | yes | Auth on |
| `0.0.0.0` | **no** | Auth on with a **randomly generated password**, printed to the logs |

That last row is the important one. `/settings` rewrites your config and the holdings routes create and delete records, so the risk was never someone *reading* your screener — it was someone *editing* your data. Serving an unauthenticated write API to the internet is not reachable by accident or omission.

```bash
ANALYSER_PASSWORD='something-strong' HOST=0.0.0.0 ./start.sh
```

| Variable | Purpose |
|---|---|
| `ANALYSER_PASSWORD` | Plaintext, hashed with scrypt at boot, never stored |
| `ANALYSER_PASSWORD_HASH` | Pre-hashed: `python -m analyser.auth 'your-password'` |
| `ANALYSER_SECRET_KEY` | Signs session cookies. Set it, or logins drop on restart |
| `ANALYSER_AUTH` | `off` to force disable, `on` to force enable |

<details>
<summary><b>Implementation details</b></summary>

Standard library only — no extra dependency for something this sensitive.

- **scrypt** password hashing with a per-install random salt
- **HMAC-SHA256** signed session cookies carrying their own expiry, no server-side store
- **Cookies rather than HTTP Basic**, because the browser WebSocket API cannot send an `Authorization` header but *does* send same-origin cookies on the upgrade request — so the live quote stream is covered by the same session. Basic auth is still accepted for `curl` and scripts.
- `HttpOnly`, `SameSite=Lax`, and `Secure` **only when the request actually arrived over TLS** (honouring `X-Forwarded-Proto`), otherwise the cookie is silently dropped on local HTTP
- **Per-address lockout**: 8 failures, 5 minutes, and a correct password does *not* bypass an active lockout
- `next=` restricted to same-site relative paths, so the login form can't be turned into an open redirect
- `/api/health` stays public and returns only liveness, because platform health checks run before anyone can log in
- Blank or whitespace-only `ANALYSER_PASSWORD` is treated as **unset** rather than becoming a password of spaces

</details>

---

## 🌐 Hosting for free

| | Always on | Live data | Card | Private | Status |
|---|:---:|:---:|:---:|:---:|---|
| **Local** `./start.sh` | — | ✅ | ❌ | ✅ | works |
| **GitHub Pages** | ✅ | ❌ snapshot | ❌ | needs Pro | ✅ **[deployed](https://sayandeepghosh.github.io/TRading_bot/)** |
| **Render** free | sleeps 15min | ✅ | ❌ | ✅ | ready to deploy |
| **Oracle Cloud** Always Free | ✅ | ✅ | ID only | ✅ | configs included |
| **Hugging Face** Spaces | mostly | ✅ | ❌ | ❌ public | config included |

Full commands live in **[`deploy/README.md`](deploy/README.md)**.

### ✅ Currently deployed: GitHub Pages

**→ https://sayandeepghosh.github.io/TRading_bot/**

GitHub Pages cannot run Python, so the Python runs at **build time** instead: [a scheduled Actions workflow](.github/workflows/publish.yml) does the scan on a runner, renders static HTML, and deploys it. Rebuilds **weekdays at 18:00 IST**, after the close, plus a manual trigger.

What works: every chart, all levels, sorting, filtering, symbol search, CSV export.
What doesn't: live streaming, settings, and position monitoring — all three need a running server.

> **A note on CI:** NSE refuses connections from datacenter IP ranges, so the constituent fetch fails on GitHub's runners. Real constituent lists for NIFTY50/100/200/500 are therefore **committed** (88 KB) and the fallback chain is `NSE live → bundled snapshot → hardcoded large caps`. Without this, a build asking for NIFTY100 quietly produced 49 stocks. Refresh the snapshot occasionally with `python -m analyser.sources.registry --refresh`.

### 🔜 For the live version: Render

Render runs Python, so you get WebSocket streaming and the position monitor. Free plan, no card.

1. Sign in to [Render](https://render.com) with GitHub
2. **New → Blueprint** → pick `TRading_bot` → **Apply**
3. It prompts for `ANALYSER_PASSWORD` and `ANALYSER_CAPITAL` — both marked `sync: false`, so neither enters git

[`render.yaml`](render.yaml) handles the rest, and generates a stable `ANALYSER_SECRET_KEY` so you stay logged in across redeploys.

Two things to expect: it **sleeps after 15 minutes idle** (~1 min to wake, though WebSocket messages count as traffic so an open tab keeps itself awake), and there's **no persistent disk on free**, so the price cache is rebuilt on each cold start.

### 🏆 Zero-compromise: Oracle Cloud Always Free

A real always-on VM, free forever, no sleep, no cold starts. Ships with [`deploy/analyser.service`](deploy/analyser.service) (systemd, loopback-bound, hardened) and [`deploy/Caddyfile`](deploy/Caddyfile) (automatic HTTPS plus a password).

*Note: Oracle cut the ARM allocation on 15 June 2026 from 4 OCPU/24 GB to 2 OCPU/12 GB. Still ample here.*

<details>
<summary><b>The Oracle gotcha that catches everyone</b></summary>

Oracle blocks inbound ports in **two** places. You must open both:

1. The instance firewall (`iptables`)
2. **Networking → Virtual Cloud Networks → Security Lists** in the OCI console

Miss the second and the VM appears completely unreachable. See `deploy/README.md`.

</details>

---

## 📡 Data sources

| Source | Provides | Cost | Latency |
|---|---|:---:|---|
| **Yahoo Finance** | Daily + 1-minute OHLCV, fundamentals | ₹0 | ~15 min delayed |
| **NSE public files** | Index constituents, sectors, market status | ₹0 | live |

Sources sit behind an adapter with **fallback and caching**, so no single one is trusted to be up and a failure degrades rather than crashes. The dashboard reports which source answered each request.

### The one limitation worth understanding

**Yahoo runs roughly 15 minutes behind the exchange.** The plumbing is genuinely live — the server streams, charts update in place — but the data arriving is delayed. No hosting choice changes that.

For true real-time you need a broker feed. A free **Angel One SmartAPI** account provides real WebSocket ticks. Implement it as another source in `src/analyser/sources/` and point `LiveQuotes._fetch` at it; nothing downstream changes, including the browser protocol.

Free intraday **history** is also short — Yahoo caps 1-minute data to about a week. Daily-timeframe analysis is unaffected. For intraday depth, record live ticks to your own archive from today forward.

---

## 🏗 Architecture

```
src/analyser/
├── sources/            data adapters — yahoo, nse, registry with fallback
│   └── constituents    committed index lists (NSE blocks datacenter IPs)
├── cache.py            SQLite price + metadata cache, inspectable, no pickles
├── indicators.py       numpy/pandas indicator library
├── scoring.py          factor scores, path-dependent base rates, confidence
├── rules.py            setup → entry trigger, stops, targets, position sizing
├── engine.py           scan orchestration, two-pass, progress reporting
├── holdings.py         position monitor and exit verdicts
├── live.py             market session, intraday poller, WebSocket fan-out
├── auth.py             scrypt hashing, signed sessions, lockout
├── export_static.py    static site generator for Pages
└── web/                FastAPI app, templates, charts.js, app.js
```

**~6,400 lines of Python** across 21 modules, plus 1,300 of templates and 2,000 of CSS/JS. **No build step, no bundler.** Plotly comes from a CDN; everything else is vanilla.

<details>
<summary><b>API endpoints</b></summary>

| Endpoint | Returns |
|---|---|
| `GET /api/scan` | full scan result |
| `GET /api/stock/{symbol}` | one symbol analysed |
| `GET /api/chart/{symbol}` | OHLCV + indicator series |
| `GET /api/intraday/{symbol}` | today's 1-minute bars |
| `GET /api/overview` | dashboard chart aggregates |
| `GET /api/holdings` | position verdicts |
| `GET /api/session` | market open/closed |
| `GET /api/progress` | scan progress |
| `GET /api/search?q=` | symbol lookup |
| `GET /api/health` | health check (public) |
| `GET /export.csv` | 25-column export |
| `WS /ws/quotes` | live quote stream |

Interactive docs at `/api/docs`.

</details>

---

## 🚫 What it deliberately does not do

- **Place orders.** No broker credentials, no order routing. You execute manually, in your own app.
- **Predict prices.** It reports what the rules say and what history measured.
- **Hide bad news.** Bearish signals, thin samples and poor reward:risk are surfaced, not buried.
- **Duplicate its own logic.** The static build ships an *explanation* instead of a JavaScript reimplementation of the exit rules, because two copies of a stop-loss rule can silently disagree — and a stop that contradicts itself is worse than no stop page at all.

---

<div align="center">

**Built so the stop loss is a decision you make once, in advance — not a negotiation you have while losing money.**

[Live dashboard](https://sayandeepghosh.github.io/TRading_bot/) · [Deployment guide](deploy/README.md) · [Configuration](config/config.yaml)

<sub>Not affiliated with NSE, Yahoo Finance, or any broker. Not investment advice.</sub>

</div>
