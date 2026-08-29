/* ==========================================================================
   Chart builders. Plotly for the interactive panels, hand-rolled SVG for
   sparklines (25 table rows do not each need a full plotting library).

   Colour follows the same semantics as the rest of the UI:
     green = long side / profit / target
     red   = risk / stop
     amber = caution
     blue  = an action available now
   ========================================================================== */

window.Charts = (function () {
  'use strict';

  var C = {
    green:  '#26d07c',
    greenD: '#17a05e',
    red:    '#f4574f',
    amber:  '#f5a524',
    blue:   '#3b9dff',
    violet: '#a78bfa',
    pink:   '#e879f9',
    text:   '#e4e8f1',
    dim:    '#9aa3b8',
    faint:  '#6b7488',
    grid:   '#1c212b',
    line:   '#262c38',
    bg:     '#11141b'
  };

  // Match the CSS stack so chart labels and page text render as one typeface.
  var FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",' +
             ' "Helvetica Neue", Inter, "Segoe UI", Roboto, ui-sans-serif, sans-serif';

  function theme(extra) {
    var base = {
      paper_bgcolor: C.bg,
      plot_bgcolor: C.bg,
      font: { color: C.dim, family: FONT, size: 11 },
      margin: { l: 58, r: 54, t: 28, b: 34 },
      hovermode: 'x unified',
      hoverlabel: {
        bgcolor: '#171b24', bordercolor: C.line,
        font: { color: C.text, family: FONT, size: 11 }
      },
      xaxis: { gridcolor: C.grid, linecolor: C.line, zeroline: false },
      yaxis: { gridcolor: C.grid, linecolor: C.line, zeroline: false },
      legend: { orientation: 'h', y: 1.06, x: 0, font: { size: 10 } },
      dragmode: 'pan'
    };
    return Object.assign({}, base, extra || {});
  }

  var OPTS = {
    displayModeBar: false,
    responsive: true,
    scrollZoom: true
  };

  /* ==================================================== multi-panel stock === */

  /**
   * Four stacked panels sharing one x-axis: price, volume, MACD, RSI.
   * `levels` draws entry / stop / target as horizontal bands on the price panel
   * so you can see at a glance how far price sits from each decision point.
   */
  function stock(elId, d, levels, opts) {
    if (!window.Plotly || !d || !d.dates || !d.dates.length) return null;
    opts = opts || {};
    var el = document.getElementById(elId);
    if (!el) return null;

    // Vertical layout: price gets the room, the three studies get a slice each.
    var DOM = {
      price: [0.42, 1.0],
      vol:   [0.30, 0.395],
      macd:  [0.15, 0.275],
      rsi:   [0.0,  0.125]
    };

    var volColors = d.close.map(function (c, i) {
      if (i === 0 || c == null || d.close[i - 1] == null) return C.faint;
      return c >= d.close[i - 1] ? 'rgba(38,208,124,.55)' : 'rgba(244,87,79,.55)';
    });

    var histColors = (d.macd_hist || []).map(function (v) {
      return v == null ? C.faint : (v >= 0 ? 'rgba(38,208,124,.75)' : 'rgba(244,87,79,.75)');
    });

    var traces = [];

    // --- Bollinger band drawn first so candles sit on top of the fill.
    if (d.bb_upper && d.bb_upper.length) {
      traces.push({
        x: d.dates, y: d.bb_upper, type: 'scatter', mode: 'lines',
        name: 'Bollinger', legendgroup: 'bb',
        line: { color: 'rgba(120,132,158,.45)', width: 1 },
        hoverinfo: 'skip', meta: 'bb'
      });
      traces.push({
        x: d.dates, y: d.bb_lower, type: 'scatter', mode: 'lines',
        name: 'Bollinger lower', legendgroup: 'bb', showlegend: false,
        line: { color: 'rgba(120,132,158,.45)', width: 1 },
        fill: 'tonexty', fillcolor: 'rgba(120,132,158,.07)',
        hoverinfo: 'skip', meta: 'bb'
      });
    }

    traces.push({
      x: d.dates, open: d.open, high: d.high, low: d.low, close: d.close,
      type: 'candlestick', name: 'Price', meta: 'price',
      increasing: { line: { color: C.green, width: 1 }, fillcolor: C.green },
      decreasing: { line: { color: C.red, width: 1 }, fillcolor: C.red }
    });

    traces.push({
      x: d.dates, y: d.sma_fast, type: 'scatter', mode: 'lines',
      name: '50 DMA', line: { color: C.blue, width: 1.3 }, meta: 'ma'
    });
    traces.push({
      x: d.dates, y: d.sma_slow, type: 'scatter', mode: 'lines',
      name: '200 DMA', line: { color: C.amber, width: 1.3 }, meta: 'ma'
    });
    traces.push({
      x: d.dates, y: d.ema_signal, type: 'scatter', mode: 'lines',
      name: '21 EMA', line: { color: C.violet, width: 1, dash: 'dot' }, meta: 'ma'
    });

    // --- volume
    traces.push({
      x: d.dates, y: d.volume, type: 'bar', name: 'Volume',
      marker: { color: volColors }, yaxis: 'y2', showlegend: false, meta: 'vol'
    });
    if (d.vol_avg && d.vol_avg.length) {
      traces.push({
        x: d.dates, y: d.vol_avg, type: 'scatter', mode: 'lines',
        name: 'Vol avg', line: { color: C.amber, width: 1 },
        yaxis: 'y2', showlegend: false, meta: 'vol'
      });
    }

    // --- MACD
    traces.push({
      x: d.dates, y: d.macd_hist, type: 'bar', name: 'MACD hist',
      marker: { color: histColors }, yaxis: 'y3', showlegend: false, meta: 'macd'
    });
    traces.push({
      x: d.dates, y: d.macd, type: 'scatter', mode: 'lines', name: 'MACD',
      line: { color: C.blue, width: 1.2 }, yaxis: 'y3', showlegend: false, meta: 'macd'
    });
    traces.push({
      x: d.dates, y: d.macd_signal, type: 'scatter', mode: 'lines', name: 'Signal',
      line: { color: C.amber, width: 1, dash: 'dot' }, yaxis: 'y3',
      showlegend: false, meta: 'macd'
    });

    // --- RSI
    traces.push({
      x: d.dates, y: d.rsi, type: 'scatter', mode: 'lines', name: 'RSI(14)',
      line: { color: C.pink, width: 1.3 }, yaxis: 'y4', showlegend: false, meta: 'rsi'
    });

    /* ---- decision levels as bands + right-edge labels ---- */
    var shapes = [];
    var annos = [];

    function hline(y, color, label, dash, width) {
      shapes.push({
        type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y, yref: 'y',
        line: { color: color, width: width || 1.1, dash: dash || 'dash' }
      });
      annos.push({
        xref: 'paper', x: 1.004, y: y, yref: 'y', xanchor: 'left', yanchor: 'middle',
        text: label, showarrow: false, font: { size: 9.5, color: color }
      });
    }

    if (levels) {
      // Buy zone as a filled band, because it is a range not a single price.
      if (levels.entry_low && levels.entry_high) {
        shapes.push({
          type: 'rect', xref: 'paper', x0: 0, x1: 1,
          y0: levels.entry_low, y1: levels.entry_high, yref: 'y',
          fillcolor: 'rgba(59,157,255,.13)', line: { width: 0 }, layer: 'below'
        });
        annos.push({
          xref: 'paper', x: 1.004, y: levels.entry_high, yref: 'y',
          xanchor: 'left', yanchor: 'middle',
          text: 'Buy ' + levels.entry_high, showarrow: false,
          font: { size: 9.5, color: C.blue }
        });
      }
      // Everything below the stop is the loss zone.
      if (levels.stop) {
        shapes.push({
          type: 'rect', xref: 'paper', x0: 0, x1: 1,
          y0: levels.stop * 0.9, y1: levels.stop, yref: 'y',
          fillcolor: 'rgba(244,87,79,.10)', line: { width: 0 }, layer: 'below'
        });
        hline(levels.stop, C.red, 'Stop ' + levels.stop, 'dash', 1.4);
      }
      if (levels.target_1r) hline(levels.target_1r, C.green, '+1R ' + levels.target_1r, 'dot');
      if (levels.target_2r) hline(levels.target_2r, C.greenD, '+2R ' + levels.target_2r, 'dot');
      if (levels.target_3r) hline(levels.target_3r, C.greenD, '+3R ' + levels.target_3r, 'dot');
    }

    // RSI reference lines
    [[70, C.red], [30, C.green]].forEach(function (p) {
      shapes.push({
        type: 'line', xref: 'paper', x0: 0, x1: 1, y0: p[0], y1: p[0], yref: 'y4',
        line: { color: p[1], width: 1, dash: 'dot' }
      });
    });
    // MACD zero line
    shapes.push({
      type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 0, y1: 0, yref: 'y3',
      line: { color: C.line, width: 1 }
    });

    var layout = theme({
      height: opts.height || 660,
      title: opts.title
        ? { text: opts.title, font: { size: 12.5, color: C.text }, x: 0, xanchor: 'left' }
        : undefined,
      barmode: 'overlay',
      bargap: 0.15,
      shapes: shapes,
      annotations: annos,
      xaxis: {
        gridcolor: C.grid, linecolor: C.line, zeroline: false,
        rangeslider: { visible: false },
        type: 'date',
        rangeselector: {
          buttons: [
            { count: 1, label: '1M', step: 'month', stepmode: 'backward' },
            { count: 3, label: '3M', step: 'month', stepmode: 'backward' },
            { count: 6, label: '6M', step: 'month', stepmode: 'backward' },
            { count: 1, label: '1Y', step: 'year', stepmode: 'backward' },
            { step: 'all', label: 'All' }
          ],
          bgcolor: '#171b24',
          activecolor: '#2b3444',
          bordercolor: C.line,
          borderwidth: 1,
          font: { color: C.dim, size: 10 },
          x: 0, y: 1.13
        }
      },
      yaxis:  { domain: DOM.price, gridcolor: C.grid, linecolor: C.line,
                title: { text: 'Price', font: { size: 10 } }, zeroline: false },
      yaxis2: { domain: DOM.vol, gridcolor: C.grid, linecolor: C.line,
                title: { text: 'Vol', font: { size: 10 } },
                showticklabels: false, zeroline: false },
      yaxis3: { domain: DOM.macd, gridcolor: C.grid, linecolor: C.line,
                title: { text: 'MACD', font: { size: 10 } }, zeroline: false },
      yaxis4: { domain: DOM.rsi, gridcolor: C.grid, linecolor: C.line,
                title: { text: 'RSI', font: { size: 10 } },
                range: [0, 100], dtick: 50, zeroline: false }
    });

    // Default view: last ~6 months, zoomable out to everything loaded.
    var n = d.dates.length;
    if (n > 130) layout.xaxis.range = [d.dates[n - 130], d.dates[n - 1]];

    return Plotly.newPlot(elId, traces, layout, OPTS).then(function () {
      wireToggles(elId, traces);
      return elId;
    });
  }

  /**
   * Overlay show/hide buttons. Any element with data-toggle-chart="<id>" and
   * data-series="<meta>" flips the matching traces.
   */
  function wireToggles(elId, traces) {
    var btns = document.querySelectorAll('[data-toggle-chart="' + elId + '"]');
    Array.prototype.forEach.call(btns, function (btn) {
      btn.addEventListener('click', function () {
        var want = btn.dataset.series;
        var idx = [];
        traces.forEach(function (t, i) { if (t.meta === want) idx.push(i); });
        if (!idx.length) return;

        var on = btn.getAttribute('aria-pressed') !== 'true';
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.classList.toggle('off', !on);
        Plotly.restyle(elId, { visible: on ? true : 'legendonly' }, idx);
      });
      btn.setAttribute('aria-pressed', 'true');
    });
  }

  /* ============================================================== regime === */

  function regime(elId, d, opts) {
    if (!window.Plotly || !d || !d.dates || !d.dates.length) return null;
    if (!document.getElementById(elId)) return null;
    opts = opts || {};

    return Plotly.newPlot(elId, [
      {
        x: d.dates, y: d.close, type: 'scatter', mode: 'lines', name: 'Nifty 50',
        line: { color: C.text, width: 1.6 },
        fill: 'tozeroy', fillcolor: 'rgba(228,232,241,.04)'
      },
      {
        x: d.dates, y: d.sma_fast, type: 'scatter', mode: 'lines', name: '50 DMA',
        line: { color: C.blue, width: 1.2 }
      },
      {
        x: d.dates, y: d.sma_slow, type: 'scatter', mode: 'lines', name: '200 DMA',
        line: { color: C.amber, width: 1.2 }
      }
    ], theme({
      height: opts.height || 240,
      margin: { l: 56, r: 14, t: 26, b: 28 },
      yaxis: { gridcolor: C.grid, linecolor: C.line, zeroline: false,
               autorange: true, fixedrange: false },
      legend: { orientation: 'h', y: 1.14, x: 0, font: { size: 10 } }
    }), OPTS);
  }

  /* =========================================================== setup mix === */

  function setupMix(elId, rows, opts) {
    if (!window.Plotly || !rows || !rows.length) return null;
    if (!document.getElementById(elId)) return null;
    opts = opts || {};

    var palette = {
      'Breakout': C.green,
      'Trend continuation': C.blue,
      'Pullback in uptrend': C.violet,
      'Early trend': '#2dd4bf',
      'Oversold bounce': C.amber,
      'No clear setup': C.faint,
      'Avoid': C.red
    };

    // Ascending so the biggest bar lands at the top of a horizontal chart.
    var asc = rows.slice().sort(function (a, b) { return a.count - b.count; });

    return Plotly.newPlot(elId, [{
      type: 'bar', orientation: 'h',
      x: asc.map(function (r) { return r.count; }),
      y: asc.map(function (r) { return r.label; }),
      marker: { color: asc.map(function (r) { return palette[r.label] || C.faint; }) },
      text: asc.map(function (r) { return r.count; }),
      textposition: 'auto',
      textfont: { size: 10, color: '#04120a' },
      hovertemplate: '%{y}: %{x} stocks<extra></extra>'
    }], theme({
      height: opts.height || 240,
      margin: { l: 130, r: 16, t: 20, b: 28 },
      showlegend: false,
      xaxis: { gridcolor: C.grid, linecolor: C.line, zeroline: false, dtick: 5 },
      yaxis: { gridcolor: 'rgba(0,0,0,0)', linecolor: C.line,
               tickfont: { size: 10 }, zeroline: false }
    }), OPTS);
  }

  /* ============================================================= sectors === */

  function sectors(elId, rows, opts) {
    if (!window.Plotly || !rows || !rows.length) return null;
    if (!document.getElementById(elId)) return null;
    opts = opts || {};

    var asc = rows.slice().sort(function (a, b) { return a.count - b.count; });
    return Plotly.newPlot(elId, [{
      type: 'bar', orientation: 'h',
      x: asc.map(function (r) { return r.count; }),
      y: asc.map(function (r) { return trim(r.label, 22); }),
      marker: {
        color: asc.map(function (r) { return r.count; }),
        colorscale: [[0, '#1d4ed8'], [1, C.blue]], showscale: false
      },
      hovertemplate: '%{y}: %{x} setups<extra></extra>'
    }], theme({
      height: opts.height || 240,
      margin: { l: 140, r: 16, t: 20, b: 28 },
      showlegend: false,
      xaxis: { gridcolor: C.grid, linecolor: C.line, zeroline: false, dtick: 1 },
      yaxis: { gridcolor: 'rgba(0,0,0,0)', linecolor: C.line,
               tickfont: { size: 10 }, zeroline: false }
    }), OPTS);
  }

  /* ============================================================= scatter === */

  /** Score against reward:risk. Top-right is where you want to be looking. */
  function scatter(elId, pts, opts) {
    if (!window.Plotly || !pts || !pts.length) return null;
    if (!document.getElementById(elId)) return null;
    opts = opts || {};

    function series(list, name, color, size) {
      return {
        x: list.map(function (p) { return p.score; }),
        y: list.map(function (p) { return p.rr; }),
        text: list.map(function (p) { return p.symbol; }),
        customdata: list.map(function (p) {
          return [p.setup, p.base == null ? 'n/a' : p.base + '%'];
        }),
        type: 'scatter', mode: 'markers+text', name: name,
        textposition: 'top center',
        textfont: { size: 8.5, color: C.faint },
        marker: {
          size: size, color: color, opacity: .85,
          line: { color: C.bg, width: 1 }
        },
        hovertemplate:
          '<b>%{text}</b><br>Score %{x}<br>R:R %{y}:1<br>' +
          '%{customdata[0]}<br>Base rate %{customdata[1]}<extra></extra>'
      };
    }

    var ready = pts.filter(function (p) { return p.ready; });
    var wait = pts.filter(function (p) { return !p.ready; });

    var traces = [];
    if (wait.length) traces.push(series(wait, 'Waiting', 'rgba(245,165,36,.75)', 9));
    if (ready.length) traces.push(series(ready, 'Ready to buy', C.green, 13));

    return Plotly.newPlot(elId, traces, theme({
      height: opts.height || 300,
      margin: { l: 52, r: 18, t: 26, b: 42 },
      hovermode: 'closest',
      xaxis: { gridcolor: C.grid, linecolor: C.line, zeroline: false,
               title: { text: 'Composite score', font: { size: 10 } } },
      yaxis: { gridcolor: C.grid, linecolor: C.line, zeroline: false,
               title: { text: 'Reward : risk', font: { size: 10 } } },
      legend: { orientation: 'h', y: 1.12, x: 0, font: { size: 10 } }
    }), OPTS).then(function () {
      // Clicking a point opens that stock.
      var el = document.getElementById(elId);
      if (el && el.on) {
        el.on('plotly_click', function (ev) {
          var p = ev.points && ev.points[0];
          if (p && p.text) window.location.href = stockUrl(p.text);
        });
      }
    });
  }

  /* ============================================================ intraday === */

  /**
   * Today's 1-minute session chart. Separate from the daily chart because it
   * answers a different question: not "what is the trend" but "where is price
   * right now relative to my entry and stop".
   *
   * Held in a closure so live updates can rewrite the data in place rather than
   * tearing down and rebuilding the plot on every tick.
   */
  var intradayState = {};

  function intraday(elId, bars, levels, opts) {
    if (!window.Plotly || !document.getElementById(elId)) return null;
    opts = opts || {};
    bars = bars || [];

    intradayState[elId] = { levels: levels || null, opts: opts };

    var t = bars.map(function (b) { return b.t; });
    var traces = [
      {
        x: t,
        open: bars.map(function (b) { return b.o; }),
        high: bars.map(function (b) { return b.h; }),
        low: bars.map(function (b) { return b.l; }),
        close: bars.map(function (b) { return b.c; }),
        type: 'candlestick', name: 'Price', meta: 'price',
        increasing: { line: { color: C.green, width: 1 }, fillcolor: C.green },
        decreasing: { line: { color: C.red, width: 1 }, fillcolor: C.red }
      },
      {
        x: t, y: bars.map(function (b) { return b.v; }),
        type: 'bar', name: 'Volume', yaxis: 'y2', meta: 'vol',
        marker: { color: 'rgba(120,132,158,.4)' }, showlegend: false
      }
    ];

    var shapes = [], annos = [];
    if (levels) {
      if (levels.entry_low && levels.entry_high) {
        shapes.push({
          type: 'rect', xref: 'paper', x0: 0, x1: 1,
          y0: levels.entry_low, y1: levels.entry_high, yref: 'y',
          fillcolor: 'rgba(59,157,255,.14)', line: { width: 0 }, layer: 'below'
        });
      }
      [['stop', C.red, 'Stop'], ['target_1r', C.green, '+1R'],
       ['target_2r', C.greenD, '+2R']].forEach(function (p) {
        var v = levels[p[0]];
        if (!v) return;
        shapes.push({
          type: 'line', xref: 'paper', x0: 0, x1: 1, y0: v, y1: v, yref: 'y',
          line: { color: p[1], width: 1.2, dash: 'dash' }
        });
        annos.push({
          xref: 'paper', x: 1.004, y: v, yref: 'y', xanchor: 'left',
          yanchor: 'middle', text: p[2] + ' ' + v, showarrow: false,
          font: { size: 9.5, color: p[1] }
        });
      });
    }

    var layout = theme({
      height: opts.height || 340,
      margin: { l: 58, r: 54, t: 16, b: 34 },
      barmode: 'overlay',
      shapes: shapes,
      annotations: annos,
      showlegend: false,
      xaxis: {
        gridcolor: C.grid, linecolor: C.line, zeroline: false,
        type: 'category', rangeslider: { visible: false },
        // A whole session of minute labels is unreadable; thin them out.
        nticks: 10, tickangle: 0
      },
      yaxis: { domain: [0.26, 1], gridcolor: C.grid, linecolor: C.line, zeroline: false },
      yaxis2: { domain: [0, 0.2], gridcolor: C.grid, linecolor: C.line,
                showticklabels: false, zeroline: false }
    });

    return Plotly.newPlot(elId, traces, layout, OPTS);
  }

  /** Replace intraday data in place. Cheap enough to call on every push. */
  function updateIntraday(elId, bars) {
    var el = document.getElementById(elId);
    if (!el || !window.Plotly || !bars || !bars.length) return;
    if (!intradayState[elId]) return;   // never initialised, nothing to update

    Plotly.update(elId, {
      x: [bars.map(function (b) { return b.t; }), bars.map(function (b) { return b.t; })],
      open: [bars.map(function (b) { return b.o; }), undefined],
      high: [bars.map(function (b) { return b.h; }), undefined],
      low: [bars.map(function (b) { return b.l; }), undefined],
      close: [bars.map(function (b) { return b.c; }), undefined],
      y: [undefined, bars.map(function (b) { return b.v; })]
    }, {}, [0, 1]);
  }

  /* =========================================================== sparkline === */

  /**
   * Inline SVG sparkline. Deliberately not Plotly: one per table row would mean
   * dozens of plot instances for a decoration.
   */
  function sparkline(values, w, h) {
    w = w || 76; h = h || 22;
    if (!values || values.length < 2) return '';

    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = (max - min) || 1;
    var step = w / (values.length - 1);

    var pts = values.map(function (v, i) {
      var x = (i * step).toFixed(1);
      var y = (h - 2 - ((v - min) / span) * (h - 4)).toFixed(1);
      return x + ',' + y;
    });

    var up = values[values.length - 1] >= values[0];
    var col = up ? C.green : C.red;
    var line = pts.join(' ');
    var area = 'M0,' + h + ' L' + pts.join(' L') + ' L' + w + ',' + h + ' Z';

    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h +
           '" preserveAspectRatio="none" aria-hidden="true">' +
           '<path d="' + area + '" fill="' + col + '" opacity=".12"/>' +
           '<polyline points="' + line + '" fill="none" stroke="' + col +
           '" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>' +
           '</svg>';
  }

  /** Fill every [data-spark] element from its JSON payload. */
  function renderSparklines(root) {
    var nodes = (root || document).querySelectorAll('[data-spark]');
    Array.prototype.forEach.call(nodes, function (n) {
      if (n.dataset.sparkDone) return;
      var vals;
      try { vals = JSON.parse(n.dataset.spark); } catch (e) { return; }
      n.innerHTML = sparkline(vals, +n.dataset.w || 76, +n.dataset.h || 22);
      n.dataset.sparkDone = '1';
    });
  }

  /* ============================================================== helpers === */

  function trim(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n - 1) + '\u2026' : s;
  }

  function stockUrl(sym) {
    var base = document.body.dataset.stockBase || '/stock/';
    var ext = document.body.dataset.stockExt || '';
    return base + encodeURIComponent(sym) + ext;
  }

  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  return {
    colors: C,
    theme: theme,
    stock: stock,
    regime: regime,
    setupMix: setupMix,
    sectors: sectors,
    scatter: scatter,
    intraday: intraday,
    updateIntraday: updateIntraday,
    sparkline: sparkline,
    renderSparklines: renderSparklines,
    readJSON: readJSON,
    stockUrl: stockUrl
  };
})();
