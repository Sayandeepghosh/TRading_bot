<div align="center">

# Equity Analyser

**An NSE stock screener that tells you when to buy, where to stop, and how much to size — and shows its reasoning for every call.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Plotly](https://img.shields.io/badge/Plotly-interactive-3F4F75?logo=plotly&logoColor=white)](https://plotly.com/javascript/)
[![Cost](https://img.shields.io/badge/cost-%E2%82%B90-26d07c)](deploy/README.md)
![Orders](https://img.shields.io/badge/places%20orders-never-f4574f)

*Runs locally. Free data. Advisory only — it never touches your broker account.*

</div>

---

## ⚠️ Read this first

This tool applies **fixed rules** to public market data and shows you the result. It is **not** investment advice, and it does **not** predict prices.

- Nobody, including this software, knows which trade will make money.
- Every level shown is a **rule**, not a forecast.
- The historical win rates it reports describe the past. They do not carry forward.
- You place your own orders manually. You carry the full loss.

The honest measured win rates for these setups land between **17% and 67%**. That is what rule-based trading actually looks like. Anything advertising 90% is not measuring properly.

**The real value is that your stop is decided before you own the position, while you are calm.**

---

## What it actually answers

Most screeners hand you a ranked list and leave you to guess the rest. This one commits to the two decisions that matter.

<table>
<tr>
<td width="50%" valign="top">

### 🟢 When to invest

A **price condition**, not a hunch:

> *"Buy only on a daily close above ₹1,352.35 with volume at least 1.5× the 20-day average. No close above the level means no trade."*

Ideas split into **Buy zone active** (actionable today) and **Waiting for trigger** (set an alert, don't pre-empt it).

</td>
<td width="50%" valign="top">

### 🔴 When to stop

Four separate exits, all fixed in advance:

| | |
|---|---|
| **Stop loss** | Below the swing low or 2.5× ATR, whichever is further |
| **Scale out** | Third at +1R, stop to breakeven, trail the rest |
| **Trend break** | Two closes below the 50-day average |
| **Time stop** | No +1R inside the horizon = the setup failed |

</td>
</tr>
</table>

Plus **how much**: position size derives from what you are willing to lose, never from what you hope to gain.

```
Capital ₹1,00,000 · risk 1% · entry ₹8,131 · stop ₹7,820
  → risk per share ₹311  →  3 shares  →  worst case ₹933
```

A wider stop mechanically means fewer shares. Rupee risk stays constant.

---

## The dashboard

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ▲ Equity Analyser    Opportunities  My positions  Settings   ⌕  ● Live   │
├──────────────────────────────────────────────────────────────────────────┤
│  MARKET REGIME: BEARISH        Nifty 24,175.65  -0.42%   ▼ below 200DMA  │
│  Long setups fail more often in this regime regardless of the chart.     │
├──────────────────────────────────────────────────────────────────────────┤
│   9        25       3.16       2        4.9k      50                     │
│  READY   WAITING   AVG R:R   HIGH     RISK AT   SCANNED                 │
│                              CONF     STOPS                             │
├──────────────────────────────────────────────────────────────────────────┤
│  ╭─ Nifty + moving averages ─╮  ╭─ Score vs reward:risk ──────────────╮  │
│  │    ╱╲    ╱╲___             │  │        · TITAN      · JSWSTEEL     │  │
│  │ ╲╱    ╲╱      ╲___         │  │   · LT        · EICHERMOT          │  │
│  ╰────────────────────────────╯  ╰────────────────────────────────────╯  │
├──────────────────────────────────────────────────────────────────────────┤
│  EICHERMOT  Eicher Motors        Trend continuation · Medium · 82.3      │
│  ┌────────────┬────────────┬────────────┬────────────┐                   │
│  │ 1 · INVEST │ 2 · STOP   │ 3 · TARGET │ 4 · SIZE   │                   │
│  │ 7,957      │ 7,819      │ 8,754      │ 1 sh       │                   │
│  │ – 8,131    │ ▼2.5×ATR   │ +2R  4.8:1 │ max ₹312   │                   │
│  └────────────┴────────────┴────────────┴────────────┘                   │
│  Exit rules · Scale out · Trend break · Time stop 120d · Invalidation    │
│  Historically: hit +1R before stop 33% of the time over 10 occurrences   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Get running

```bash
git clone https://github.com/Sayandeepghosh/TRading_bot.git
cd TRading_bot
./start.sh
```

Open **http://127.0.0.1:8000**. First run creates the virtualenv, installs dependencies, and starts scanning in the background — pages render instantly with a live progress bar rather than blocking.

<details>
<summary><b>Terminal modes</b></summary>

```bash
./start.sh                        # dashboard
./start.sh --scan                 # one scan, printed
./start.sh --holdings             # review your positions
./start.sh --stock TITAN          # analyse one symbol
./start.sh --universe NIFTY500    # widen the net
./start.sh --build-static docs    # render a static snapshot
```

</details>

<details>
<summary><b>Scan times</b></summary>

| Universe | Cold | Warm cache |
|---|---|---|
| NIFTY50 | ~30s | ~12s |
| NIFTY100 | ~45s | ~26s |
| NIFTY200 | ~90s | ~43s |
| NIFTY500 | ~4min | ~2min |

Two passes by design: technicals batch-download across the whole universe, then fundamentals are fetched only for the leaders, because that costs one HTTP round trip per company.

</details>

---

## Features

### 📊 Interactive charts

- **Four-panel daily chart** — candles with Bollinger fill, 50/200 DMA, 21 EMA, volume coloured by direction, MACD, RSI. Range buttons 1M→All, drag to pan, scroll to zoom.
- **Your levels drawn on** — buy zone as a blue band, stop as a red line with the loss region shaded, R-targets dotted and labelled.
- **Intraday session chart** — 1-minute bars, refreshing while the market is open.
- **Overview charts** — regime, score-vs-reward:risk scatter (click a point to open it), setup mix, sector concentration.
- **Sparklines** in the watchlist, hand-rolled SVG rather than dozens of plot instances.

### 🔴 Live streaming

WebSocket push, no page reload. Prices flash on change. Connection pill shows Live / Pre-open / Closed, and **only the open-market state pulses** — a blinking dot next to "Closed" would claim activity that isn't happening.

Polling runs at 25s while open, stops entirely when closed, and only fetches symbols someone is actually watching (reference-counted). Session state cross-checks NSE's own status against the clock, so **trading holidays aren't mistaken for trading days**.

### 📈 Honest base rates

Every setup is replayed through that stock's own history. For each past occurrence it walks forward bar by bar and records whether the target or the stop was hit **first** — path-dependent, so it can't flatter itself the way a "return after N days" calculation would. Thin samples are labelled unreliable instead of being rounded into a confident-looking number.

### 📋 Position monitor

Record what you bought; each visit re-checks it and returns one mechanical verdict:

| Verdict | Meaning |
|---|---|
| 🔴 **EXIT NOW** | Stop breached, or the trend that justified the position has broken |
| 🟠 **TIME EXIT** | Held past its horizon without working. Capital is idle |
| 🟢 **TAKE PARTIAL PROFIT** | Up 2R+. Bank a third, trail the rest |
| 🔵 **TIGHTEN STOP** | Up 1R. Move to breakeven so it can no longer lose |
| ⚪ **HOLD** | Nothing changed. Don't tinker |

The point is removing the moment where you talk yourself into holding a loser.

### 🔎 Transparency

Every ranking opens up into the signals that produced it — five factor scores, the bullish and bearish evidence side by side, and why the confidence label landed where it did. **Bearish signals are shown as prominently as bullish ones.**

---

## How the scoring works

Five factors, weighted, each 0–100. Weights are configurable and normalise automatically.

| Factor | Default | Reads |
|---|---|---|
| **Trend** | 30% | Price vs 50/200 DMA, MA stacking, ADX strength, distance from 52-week high |
| **Momentum** | 25% | RSI zone, MACD, 1M/3M rate of change, relative strength vs Nifty |
| **Volume** | 15% | Surge vs 20-day average, OBV slope, liquidity floor |
| **Volatility** | 10% | ATR% — rewards tradeable range, penalises extremes both ways |
| **Fundamental** | 20% | P/E, ROE, debt/equity, earnings and revenue growth, margin |

When fundamentals are unavailable the weight is **redistributed across the technical factors** rather than scoring a silent neutral, so missing data can never look like bad data.

Scores rank and explain. **They are not probabilities** — 80 does not mean an 80% chance of profit, and the UI never presents it that way.

<details>
<summary><b>Setups and their horizons</b></summary>

| Setup | Horizon | Character |
|---|---|---|
| Trend continuation | 4–17 weeks | Established trend, hold through noise |
| Early trend | 3–13 weeks | Forming, not yet ADX-confirmed. Earlier entry, higher failure rate |
| Breakout | 10–40 days | At 52-week highs on volume. Resolves fast, either way |
| Pullback in uptrend | 1–4 weeks | Better price than chasing, risk that it keeps falling |
| Oversold bounce | 5–15 days | Counter-trend, short leash. Never average down |
| Avoid | — | Downtrend. Long ideas have poor odds |

Horizons describe how long the **setup** typically takes to resolve. They are not profit forecasts.

</details>

<details>
<summary><b>Indicators — all implemented in numpy/pandas</b></summary>

`sma` `ema` `wilder` `rsi` `atr` `adx` `macd` `bollinger` `roc` `obv` `slope_pct` `swing_low` `relative_strength`

No TA-Lib, so `pip install` works without a system C library. Wilder's smoothing is used where convention varies, matching what charting platforms show. Nothing forward-fills or back-fills, because that would leak future information into a historical reading.

</details>

---

## Configuration

Everything lives in [`config/config.yaml`](config/config.yaml), commented throughout. The Settings page edits the important parts and **writes comments back intact**.

```yaml
risk:
  capital: 100000            # your investable capital
  risk_per_trade_pct: 1.0    # loss if the stop hits. keep this small
  max_position_pct: 15.0     # concentration cap per stock
  min_reward_risk: 1.5       # flag anything paying less
```

The two that change your results most are **capital** and **risk per trade**, because every position size derives from them.

---

## 🌐 Hosting it for free

| | Always on | Live data | Card | Private |
|---|:---:|:---:|:---:|:---:|
| **Local** (`./start.sh`) | — | ✅ | ❌ | ✅ |
| **Oracle Cloud Always Free** | ✅ | ✅ | ID only | ✅ |
| **Render free** | sleeps 15min | ✅ | ❌ | ✅ |
| **Hugging Face Spaces** | mostly | ✅ | ❌ | ❌ public |
| **GitHub Pages** | ✅ | ❌ snapshot | ❌ | needs Pro |

Full commands in **[`deploy/README.md`](deploy/README.md)**.

**Oracle Cloud** is the zero-compromise option — a real always-on VM, free forever. *Note: the ARM allocation was cut on 15 June 2026 from 4 OCPU/24GB to 2 OCPU/12GB. Still ample here.*

**Render** is the fastest to stand up: push, then New → Blueprint. [`render.yaml`](render.yaml) does the rest. It sleeps after 15 minutes idle, but WebSocket messages count as inbound traffic, so an open dashboard keeps itself awake.

**GitHub Pages** can't run Python, so it gets a daily snapshot built by [Actions](.github/workflows/publish.yml) instead — charts and levels intact, no live streaming. Pages needs a public repo or a paid plan; the workflow gates those steps behind a `PUBLISH_PAGES` variable and always uploads a downloadable artifact so it works either way.

### Authentication

Built in, and it **fails closed**. The rule is decided by the bind address:

| Bind | Password set | Result |
|---|---|---|
| `127.0.0.1` | no | Auth **off** — a login prompt on your own laptop protects nothing |
| `127.0.0.1` | yes | Auth on |
| `0.0.0.0` | yes | Auth on |
| `0.0.0.0` | **no** | Auth on with a **randomly generated password**, printed to the logs |

That last row is the important one. Exposing an unauthenticated write API to the internet is never the default, even by accident.

```bash
ANALYSER_PASSWORD='something-strong' HOST=0.0.0.0 ./start.sh
```

| Variable | Purpose |
|---|---|
| `ANALYSER_PASSWORD` | Plaintext, hashed with scrypt at boot and never stored |
| `ANALYSER_PASSWORD_HASH` | Pre-hashed: `python -m analyser.auth 'your-password'` |
| `ANALYSER_SECRET_KEY` | Signs session cookies. Set it, or logins drop on restart |
| `ANALYSER_AUTH` | `off` to force disable, `on` to force enable |

Sessions are HMAC-signed cookies (HttpOnly, SameSite=Lax, Secure over TLS) — cookies rather than HTTP Basic because the browser WebSocket API can't send an `Authorization` header but does send same-origin cookies, so the live stream is covered too. Basic auth still works for `curl` and scripts. Failed logins lock an address out for 5 minutes after 8 attempts, and the `next=` parameter only accepts same-site relative paths so the login form can't be turned into an open redirect.

`/api/health` stays public and returns only liveness, because platform health checks run before anyone can log in.

---

## Data sources

| Source | Provides | Cost | Latency |
|---|---|---|---|
| **Yahoo Finance** | Daily + 1min OHLCV, fundamentals | ₹0 | ~15 min delayed |
| **NSE public files** | Index constituents, sectors, market status | ₹0 | live |

Sources sit behind an adapter with fallback and caching, so no single one is trusted to be up and a failure degrades rather than crashes. The dashboard reports which sources answered.

### The one limitation worth understanding

**Yahoo runs roughly 15 minutes behind the exchange.** The plumbing is genuinely live — the server streams, charts update in place — but the data arriving is delayed. No hosting choice changes that.

For true real-time you need a broker feed. A free **Angel One SmartAPI** account provides real WebSocket ticks. Implement it as another source in `src/analyser/sources/` and point `LiveQuotes._fetch` at it; nothing downstream changes, including the browser protocol.

Free intraday **history** is also short — Yahoo caps 1-minute data to about a week. Daily-timeframe analysis is unaffected. If you want intraday depth, record live ticks to your own archive from today forward.

---

## Architecture

```
src/analyser/
├── sources/          data adapters — yahoo, nse, registry with fallback
├── cache.py          SQLite price + metadata cache, inspectable, no pickles
├── indicators.py     numpy/pandas indicator library
├── scoring.py        factor scores, path-dependent base rates, confidence
├── rules.py          setup classification → entry trigger, stops, targets, sizing
├── engine.py         scan orchestration, two-pass, progress reporting
├── holdings.py       position monitor and exit verdicts
├── live.py           market session, intraday poller, WebSocket fan-out
├── export_static.py  static site generator for Pages
└── web/              FastAPI app, templates, charts.js, app.js
```

**~5,800 lines of Python** across 20 modules, plus 1,250 of templates and 1,900 of CSS/JS. No build step, no bundler. Plotly from CDN; everything else is vanilla.

<details>
<summary><b>API endpoints</b></summary>

| | |
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
| `GET /api/health` | health check |
| `GET /export.csv` | 25-column export |
| `WS /ws/quotes` | live quote stream |

Interactive docs at `/api/docs`.

</details>

---

## What it deliberately does not do

- **Place orders.** No broker credentials, no order routing. You execute manually.
- **Predict prices.** It reports what rules say and what history measured.
- **Hide bad news.** Bearish signals, thin samples and poor reward:risk are surfaced, not buried.
- **Duplicate its own logic.** The static build ships an explanation instead of a JavaScript reimplementation of the exit rules, because two copies of a stop-loss rule can silently disagree.

---

<div align="center">

**Built to make the stop loss a decision you make once, in advance, instead of a negotiation you have while losing money.**

*Not affiliated with NSE, Yahoo Finance, or any broker. Not investment advice.*

</div>
