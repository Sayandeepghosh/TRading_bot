/* ==========================================================================
   Client behaviour. No framework, no build step, no CDN dependency.

   Three jobs:
     1. Poll scan progress and reload once results are ready, so the first
        visit shows a live progress bar instead of a frozen tab.
     2. Symbol search with keyboard navigation.
     3. Sort and filter the opportunity tables client-side.
   ========================================================================== */

(function () {
  'use strict';

  /* ---------------------------------------------------------- scan progress */

  function initProgress() {
    const box = document.getElementById('scan-progress');
    if (!box) return;

    const fill = box.querySelector('.bar-fill');
    const stage = box.querySelector('[data-stage]');
    const count = box.querySelector('[data-count]');
    const clock = box.querySelector('[data-elapsed]');
    let failures = 0;

    function tick() {
      fetch('/api/progress', { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (p) {
          failures = 0;

          if (fill) fill.style.width = Math.max(2, p.pct) + '%';
          if (stage) stage.textContent = p.stage;
          if (count) {
            count.textContent = p.total ? p.done + ' / ' + p.total : '';
          }
          if (clock) clock.textContent = p.elapsed_s.toFixed(0) + 's';

          if (p.error) {
            box.innerHTML =
              '<h2>Scan failed</h2><p class="red">' + escapeHtml(p.error) +
              '</p><form method="post" action="/refresh">' +
              '<button class="btn" type="submit">Try again</button></form>';
            return;
          }
          // Results landed: reload so the server renders them.
          if (!p.active && p.has_result) {
            window.location.reload();
            return;
          }
          setTimeout(tick, 900);
        })
        .catch(function () {
          // Server may still be booting. Back off, then keep trying.
          if (++failures > 40) return;
          setTimeout(tick, 2000);
        });
    }
    tick();
  }

  /* --------------------------------------------------------- refresh button */

  function initRefreshButtons() {
    document.querySelectorAll('form[action="/refresh"]').forEach(function (f) {
      f.addEventListener('submit', function () {
        const b = f.querySelector('button');
        if (b) {
          b.disabled = true;
          b.innerHTML = '<span class="spin"></span> Scanning';
        }
      });
    });
  }

  /* ----------------------------------------------------------------- search */

  function initSearch() {
    const input = document.getElementById('sym-search');
    const panel = document.getElementById('sym-results');
    if (!input || !panel) return;

    let timer = null;
    let items = [];
    let sel = -1;

    function close() {
      panel.classList.remove('open');
      panel.innerHTML = '';
      items = [];
      sel = -1;
    }

    function render(rows) {
      if (!rows.length) {
        panel.innerHTML = '<div class="r-empty">No match in the current universe</div>';
        panel.classList.add('open');
        items = [];
        return;
      }
      panel.innerHTML = rows.map(function (r) {
        return '<a href="/stock/' + encodeURIComponent(r.symbol) + '">' +
               '<span class="r-sym">' + escapeHtml(r.symbol) + '</span>' +
               '<span class="r-co">' + escapeHtml(r.company) + '</span></a>';
      }).join('');
      panel.classList.add('open');
      items = Array.prototype.slice.call(panel.querySelectorAll('a'));
      sel = -1;
    }

    function highlight(next) {
      if (!items.length) return;
      if (sel >= 0) items[sel].classList.remove('sel');
      sel = (next + items.length) % items.length;
      items[sel].classList.add('sel');
      items[sel].scrollIntoView({ block: 'nearest' });
    }

    // The static build has no API, so it embeds the universe and we match locally.
    var local = null;
    var embedded = document.getElementById('universe-data');
    if (embedded) {
      try { local = JSON.parse(embedded.textContent); } catch (e) { local = null; }
    }

    function matchLocal(q) {
      var term = q.toUpperCase();
      var starts = local.filter(function (r) { return r.symbol.indexOf(term) === 0; });
      var contains = local.filter(function (r) {
        return starts.indexOf(r) === -1 &&
               (r.symbol.indexOf(term) !== -1 ||
                (r.company || '').toUpperCase().indexOf(term) !== -1);
      });
      return starts.concat(contains).slice(0, 12);
    }

    input.addEventListener('input', function () {
      const q = input.value.trim();
      clearTimeout(timer);
      if (!q) { close(); return; }

      if (local) { render(matchLocal(q)); return; }

      timer = setTimeout(function () {
        fetch('/api/search?q=' + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(render)
          .catch(close);
      }, 160);
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); highlight(sel + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); highlight(sel - 1); }
      else if (e.key === 'Enter') {
        const q = input.value.trim().toUpperCase();
        if (sel >= 0 && items[sel]) { e.preventDefault(); items[sel].click(); }
        else if (q) {
          e.preventDefault();
          window.location.href = window.Charts ? Charts.stockUrl(q)
                                               : '/stock/' + encodeURIComponent(q);
        }
      } else if (e.key === 'Escape') { close(); input.blur(); }
    });

    document.addEventListener('click', function (e) {
      if (!input.contains(e.target) && !panel.contains(e.target)) close();
    });

    // "/" focuses search, the way most dashboards behave.
    document.addEventListener('keydown', function (e) {
      const tag = (document.activeElement || {}).tagName;
      if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
        e.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  /* ------------------------------------------------------------ table sort */

  function initSortableTables() {
    document.querySelectorAll('table[data-sortable]').forEach(function (table) {
      const body = table.tBodies[0];
      if (!body) return;

      table.querySelectorAll('thead th').forEach(function (th, idx) {
        if (th.dataset.nosort !== undefined) return;
        th.classList.add('sortable');
        if (!th.querySelector('.arrow')) {
          th.insertAdjacentHTML('beforeend', ' <span class="arrow">\u25B4\u25BE</span>');
        }

        th.addEventListener('click', function () {
          const numeric = th.dataset.type === 'num';
          const desc = th.classList.contains('asc') ? true : false;

          table.querySelectorAll('thead th').forEach(function (o) {
            if (o !== th) o.classList.remove('asc', 'desc');
          });
          th.classList.toggle('asc', !desc);
          th.classList.toggle('desc', desc);

          const rows = Array.prototype.slice.call(body.rows);
          rows.sort(function (a, b) {
            const av = cellVal(a.cells[idx], numeric);
            const bv = cellVal(b.cells[idx], numeric);
            if (av < bv) return desc ? 1 : -1;
            if (av > bv) return desc ? -1 : 1;
            return 0;
          });
          rows.forEach(function (r) { body.appendChild(r); });
        });
      });
    });
  }

  function cellVal(cell, numeric) {
    if (!cell) return numeric ? -Infinity : '';
    const raw = (cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent).trim();
    if (!numeric) return raw.toLowerCase();
    const n = parseFloat(raw.replace(/[^0-9.\-]/g, ''));
    return isNaN(n) ? -Infinity : n;
  }

  /* ---------------------------------------------------------- table filter */

  function initFilters() {
    document.querySelectorAll('[data-filter-for]').forEach(function (input) {
      const table = document.getElementById(input.dataset.filterFor);
      if (!table || !table.tBodies[0]) return;

      function apply() {
        const q = input.value.trim().toLowerCase();
        const setupSel = document.querySelector('[data-setup-for="' + input.dataset.filterFor + '"]');
        const setup = setupSel ? setupSel.value : '';
        let shown = 0;

        Array.prototype.forEach.call(table.tBodies[0].rows, function (row) {
          const text = row.textContent.toLowerCase();
          const rowSetup = row.dataset.setup || '';
          const ok = (!q || text.indexOf(q) !== -1) && (!setup || rowSetup === setup);
          row.classList.toggle('hidden', !ok);
          if (ok) shown++;
        });

        const badge = document.querySelector('[data-count-for="' + input.dataset.filterFor + '"]');
        if (badge) badge.textContent = shown;
      }

      input.addEventListener('input', apply);
      const setupSel = document.querySelector('[data-setup-for="' + input.dataset.filterFor + '"]');
      if (setupSel) setupSel.addEventListener('change', apply);
    });
  }

  /* ------------------------------------------------------- weight normaliser */

  function initWeights() {
    const form = document.getElementById('settings-form');
    if (!form) return;
    const out = document.getElementById('weight-sum');
    if (!out) return;

    const inputs = Array.prototype.slice.call(form.querySelectorAll('[data-weight]'));
    function update() {
      const total = inputs.reduce(function (s, i) {
        return s + (parseFloat(i.value) || 0);
      }, 0);
      out.textContent = total.toFixed(2);
      out.className = Math.abs(total - 1) < 0.005 ? 'green' : 'amber';
    }
    inputs.forEach(function (i) { i.addEventListener('input', update); });
    update();
  }

  /* ------------------------------------------------------------------ utils */

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  /* ------------------------------------------------------------------- boot */

  function boot() {
    initProgress();
    initRefreshButtons();
    initSearch();
    initSortableTables();
    initFilters();
    initWeights();
    initCharts();
    initLive();
  }

  /* ------------------------------------------------------------------- live */

  /**
   * Live quote stream.
   *
   * The DOM declares what it wants updated and this fills it in:
   *   [data-live-price="SYM"]   last traded price
   *   [data-live-change="SYM"]  change and percent, coloured by sign
   *   [data-live-ohl="SYM"]     day open / high / low
   *   #live-status              connection and session indicator
   *   #chart-intraday           session chart, redrawn as bars arrive
   *
   * Reconnects with backoff, because a laptop that slept should recover on its
   * own rather than needing a refresh.
   */
  function initLive() {
    var wanted = collectSymbols();
    var statusEl = document.getElementById('live-status');
    if (!wanted.length && !statusEl) return;

    var ws = null;
    var attempts = 0;
    var closedByUs = false;
    var pingTimer = null;

    function setStatus(kind, text, title) {
      if (!statusEl) return;
      statusEl.className = 'live-pill live-' + kind;
      statusEl.textContent = text;
      if (title) statusEl.title = title;
    }

    function connect() {
      var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
      try {
        ws = new WebSocket(proto + location.host + '/ws/quotes');
      } catch (e) {
        setStatus('off', 'Offline');
        return;
      }

      ws.onopen = function () {
        attempts = 0;
        setStatus('wait', 'Connecting');
        if (wanted.length) {
          ws.send(JSON.stringify({ action: 'subscribe', symbols: wanted }));
        }
        // Keeps intermediate proxies from idling the socket shut.
        pingTimer = setInterval(function () {
          if (ws && ws.readyState === 1) ws.send(JSON.stringify({ action: 'ping' }));
        }, 45000);
      };

      ws.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }

        if (msg.session) applySession(msg.session, msg.last_poll);
        if (msg.type === 'quotes' && msg.quotes) applyQuotes(msg.quotes);
      };

      ws.onclose = function () {
        clearInterval(pingTimer);
        if (closedByUs) return;
        attempts++;
        setStatus('off', 'Reconnecting');
        // 2s, 4s, 8s ... capped at 30s.
        setTimeout(connect, Math.min(30000, 2000 * Math.pow(2, Math.min(attempts, 4))));
      };

      ws.onerror = function () { /* onclose handles recovery */ };
    }

    function applySession(s, lastPoll) {
      if (s.is_open) {
        setStatus('on', 'Live', 'Market open. Last update ' + (lastPoll || '-'));
      } else if (s.status === 'pre-open') {
        setStatus('wait', 'Pre-open', s.label);
      } else {
        setStatus('shut', 'Closed',
          s.label + (s.next_change ? ' \u00b7 ' + s.next_change : ''));
      }
      var lbl = document.getElementById('session-label');
      if (lbl) lbl.textContent = s.label + (s.next_change ? ' \u00b7 ' + s.next_change : '');
    }

    function applyQuotes(quotes) {
      Object.keys(quotes).forEach(function (sym) {
        var q = quotes[sym];

        setAll('[data-live-price="' + cssEsc(sym) + '"]', fmt(q.price), true);

        var chg = q.change == null ? null :
          (q.change > 0 ? '+' : '') + fmt(q.change) +
          (q.change_pct == null ? '' : ' (' + (q.change_pct > 0 ? '+' : '') +
           q.change_pct.toFixed(2) + '%)');
        document.querySelectorAll('[data-live-change="' + cssEsc(sym) + '"]')
          .forEach(function (el) {
            if (chg == null) return;
            el.textContent = chg;
            el.classList.toggle('green', q.change > 0);
            el.classList.toggle('red', q.change < 0);
          });

        if (q.day_open != null) {
          setAll('[data-live-ohl="' + cssEsc(sym) + '"]',
            'O ' + fmt(q.day_open) + '  H ' + fmt(q.day_high) + '  L ' + fmt(q.day_low),
            false);
        }

        // Redraw the session chart if this page is showing that symbol.
        if (document.body.dataset.symbol === sym && window.Charts) {
          fetch('/api/intraday/' + encodeURIComponent(sym), { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (d) { Charts.updateIntraday('chart-intraday', d.bars); })
            .catch(function () {});
        }
      });
    }

    function setAll(sel, text, flash) {
      document.querySelectorAll(sel).forEach(function (el) {
        if (el.textContent === text) return;
        el.textContent = text;
        if (flash) {
          el.classList.remove('tick');
          void el.offsetWidth;        // force reflow so the animation restarts
          el.classList.add('tick');
        }
      });
    }

    window.addEventListener('beforeunload', function () {
      closedByUs = true;
      if (ws) ws.close();
    });

    connect();
  }

  /** Symbols this page cares about, deduped. */
  function collectSymbols() {
    var set = {};
    document.querySelectorAll('[data-live-price],[data-live-change]').forEach(function (el) {
      var s = el.dataset.livePrice || el.dataset.liveChange;
      if (s) set[s] = 1;
    });
    if (document.body.dataset.symbol) set[document.body.dataset.symbol] = 1;
    return Object.keys(set);
  }

  function fmt(v) {
    if (v == null) return '-';
    return Number(v).toLocaleString('en-IN', {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function cssEsc(s) {
    return String(s).replace(/["\\]/g, '\\$&');
  }

  /* ----------------------------------------------------------------- charts */

  /**
   * Charts are declared in the markup as JSON script blocks and rendered here,
   * so the same code path works for the live server and the static export.
   */
  function initCharts() {
    if (!window.Charts) return;

    Charts.renderSparklines(document);

    var ov = Charts.readJSON('overview-data');
    if (ov) {
      if (ov.regime) Charts.regime('chart-regime', ov.regime);
      if (ov.setups) Charts.setupMix('chart-setups', ov.setups);
      if (ov.sectors) Charts.sectors('chart-sectors', ov.sectors);
      if (ov.scatter) Charts.scatter('chart-scatter', ov.scatter);
    }

    var levels = Charts.readJSON('level-data');

    var series = Charts.readJSON('chart-data');
    if (series) {
      Charts.stock('chart-stock', series, levels, {
        title: (document.body.dataset.symbol || '') + ' \u00b7 daily'
      });
    }

    // Session chart, fetched separately so the daily chart paints immediately
    // rather than waiting on an intraday request.
    var sym = document.body.dataset.symbol;
    if (sym && document.getElementById('chart-intraday')) {
      fetch('/api/intraday/' + encodeURIComponent(sym), { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var host = document.getElementById('chart-intraday');
          if (!d.bars || !d.bars.length) {
            host.innerHTML = '<p class="chart-hint">No intraday bars available ' +
                             'for this symbol yet.</p>';
            return;
          }
          Charts.intraday('chart-intraday', d.bars, levels);
          var stamp = document.getElementById('intraday-stamp');
          if (stamp && d.bars.length) {
            stamp.textContent = d.bars.length + ' one-minute bars, latest ' +
                                d.bars[d.bars.length - 1].t;
          }
        })
        .catch(function () {});
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
