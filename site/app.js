/* Transit pipeline transparency: renders data/site.json (hourly) and data/status.json (about every
   15 minutes). No data is ever inserted as HTML; charts are Plotly (bundled in vendor/). */
(function () {
  "use strict";
  var STATE_COLOR = { early: "var(--c2)", on_time: "var(--c3)", late: "var(--c5)", uncertain: "var(--c6)" };
  var PALETTE = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"];
  var STATUS_STALE_MIN = 45, DATA_STALE_H = 3, POLL_MS = 300000, TICK_MS = 15000;
  var PROBE_LABELS = {
    landing_vehicle_positions_newest_file: "Collector: newest bus-position file",
    landing_trip_updates_newest_file: "Collector: newest prediction file",
    landing_alerts_newest_file: "Collector: newest alert file",
    ingest_job: "Databricks ingest job (every 30 min)",
    monitor_job: "Databricks monitor job (hourly)",
    silver_job_daily_run: "Daily processing job"
  };

  var state = { data: null, status: null, loadedGenerated: null };
  var pending = [], plots = [];

  /* ---------- small DOM helpers (text only) ---------- */
  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    apply(node, attrs, kids);
    return node;
  }
  function sv(tag, attrs, kids) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    apply(node, attrs, kids);
    return node;
  }
  function apply(node, attrs, kids) {
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (kid) {
      if (kid !== null && kid !== undefined) node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
    });
  }
  function utc(s) {
    if (s === null || s === undefined) return null;
    var t = String(s);
    if (/^\d{4}-\d\d-\d\d$/.test(t)) t += "T00:00:00Z";
    else if (!/(Z|[+-]\d\d:\d\d)$/.test(t)) t += "Z";
    var d = new Date(t);
    return isNaN(d) ? null : d;
  }
  function num(n, digits) {
    if (n === null || n === undefined || isNaN(n)) return "–";
    var v = Number(n);
    if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString("en-US");
    return digits === undefined ? String(Math.round(v * 100) / 100) : v.toFixed(digits);
  }
  function dur(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return "–";
    if (sec < 90) return Math.round(sec) + " s";
    if (sec < 5400) return Math.round(sec / 60) + " min";
    if (sec < 172800) return (sec / 3600).toFixed(1) + " h";
    return (sec / 86400).toFixed(1) + " days";
  }
  function ago(date, now) { return date ? dur((now - date) / 1000) + " ago" : "–"; }
  /* every time shown on the page is Seattle wall-clock time (PDT in summer, PST in winter) */
  var PT_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  var PT_ZONE = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", timeZoneName: "short" });
  function ptParts(d) { var o = {}; PT_FMT.formatToParts(d).forEach(function (p) { o[p.type] = p.value; }); return o; }
  function ptZone(d) { var z = PT_ZONE.formatToParts(d).filter(function (p) { return p.type === "timeZoneName"; })[0]; return z ? z.value : "PT"; }
  function ptNaive(d) { var p = ptParts(d); return p.year + "-" + p.month + "-" + p.day + " " + p.hour + ":" + p.minute + ":" + p.second; }
  function stamp(d) { if (!d) return "–"; var p = ptParts(d); return p.year + "-" + p.month + "-" + p.day + " " + p.hour + ":" + p.minute + " " + ptZone(d); }
  function stampSec(d) { return ptNaive(d) + " " + ptZone(d); }
  function whenText(value) {  /* a date stays a date; a timestamp becomes Seattle time */
    if (value === null || value === undefined) return "–";
    var t = String(value);
    if (/^\d{4}-\d\d-\d\d$/.test(t)) return t;
    var d = utc(t);
    return d ? stamp(d) : t;
  }
  var ISO_TS = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?$/;
  function localize(value, key) {  /* timestamps inside a sample row, shown in Seattle time */
    if (typeof value === "string" && ISO_TS.test(value)) { var d = utc(value); return d ? stampSec(d) : value; }
    if (typeof value === "number" && /(_ts|_time)$/.test(key || "") && value > 1e9 && value < 3e9) return stampSec(new Date(value * 1000));
    if (Array.isArray(value)) return value.map(function (v) { return localize(v, key); });
    if (value && typeof value === "object") { var o = {}; Object.keys(value).forEach(function (k) { o[k] = localize(value[k], k); }); return o; }
    return value;
  }
  function group(rows, key) {
    var out = {};
    rows.forEach(function (r) { (out[r[key]] = out[r[key]] || []).push(r); });
    return out;
  }

  /* ---------- Plotly charts ---------- */
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function color(spec, index) {
    if (!spec) return cssVar(PALETTE[index % PALETTE.length]);
    var m = String(spec).match(/^var\((--[\w-]+)\)$/);
    return m ? cssVar(m[1]) : spec;
  }
  function themeLayout() {
    var fg = cssVar("--fg"), muted = cssVar("--muted"), line = cssVar("--line");
    return {
      "font.color": fg, "xaxis.gridcolor": line, "yaxis.gridcolor": line, "xaxis.zerolinecolor": line, "yaxis.zerolinecolor": line,
      "xaxis.linecolor": line, "yaxis.linecolor": line, "xaxis.tickfont.color": muted, "yaxis.tickfont.color": muted, "legend.font.color": fg,
      "hoverlabel.bgcolor": cssVar("--card"), "hoverlabel.font.color": fg, "hoverlabel.bordercolor": line
    };
  }
  function baseLayout(extra) {
    var layout = {
      margin: { l: 58, r: 14, t: 8, b: 44 }, height: 300, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "system-ui, -apple-system, 'Segoe UI', sans-serif", size: 12 }, hovermode: "closest",
      legend: { orientation: "h", y: -0.22, x: 0 }, xaxis: {}, yaxis: { automargin: true }, hoverlabel: {}
    };
    Object.keys(extra || {}).forEach(function (k) {
      layout[k] = (extra[k] && typeof extra[k] === "object" && !Array.isArray(extra[k]) && layout[k]) ? Object.assign(layout[k], extra[k]) : extra[k];
    });
    var theme = themeLayout();
    Object.keys(theme).forEach(function (path) {
      var parts = path.split("."), node = layout;
      for (var i = 0; i < parts.length - 1; i++) { node[parts[i]] = node[parts[i]] || {}; node = node[parts[i]]; }
      node[parts[parts.length - 1]] = theme[path];
    });
    return layout;
  }
  function frame(title, caption, plotDiv, numbers) {
    return el("div", { "class": "chart" }, [
      title ? el("h4", { text: title }) : null,
      caption ? el("p", { "class": "cap", text: caption }) : null,
      plotDiv,
      numbers ? el("details", { "class": "nums" }, [el("summary", { text: "Show the numbers behind this chart" }), numbers]) : null
    ]);
  }
  function plot(traces, layout, title) {
    var div = el("div", { "class": "plot", role: "img", "aria-label": title || "chart" });
    pending.push({ div: div, traces: traces, layout: layout });
    return div;
  }
  function flushPlots() {
    var cfg = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"], toImageButtonOptions: { format: "png", scale: 2 } };
    pending.forEach(function (p) {
      if (!window.Plotly) { p.div.textContent = "Charts could not load (Plotly missing)."; return; }
      window.Plotly.newPlot(p.div, p.traces, p.layout, cfg);
      plots.push(p.div);
    });
    pending = [];
  }
  function restyleTheme() {
    if (!window.Plotly) return;
    var t = themeLayout();
    plots.forEach(function (div) { window.Plotly.relayout(div, t); });
  }

  /* categories: [label]; series: [{name, values[], color?}]; mode: "stack" | "group" */
  function barChart(o) {
    var cats = o.categories, pctFmt = o.pct ? ".1%" : ",.0f";
    var traces = o.series.map(function (s, k) {
      return { type: "bar", name: s.name, x: cats, y: s.values, marker: { color: color(s.color, k) }, hovertemplate: "%{x}<br>" + s.name + ": %{y:" + pctFmt + "}<extra></extra>" };
    });
    var layout = baseLayout({
      barmode: o.mode === "stack" ? "stack" : "group", showlegend: o.series.length > 1, hovermode: "x unified",
      xaxis: { type: "category", title: o.xLabel ? { text: o.xLabel } : undefined, fixedrange: false },
      yaxis: { tickformat: o.pct ? ".0%" : ",", range: o.yMax ? [0, o.yMax] : undefined, rangemode: "tozero" }
    });
    var head = [o.xLabel || "category"].concat(o.series.map(function (s) { return s.name; }));
    var rows = cats.map(function (c, i) { return [c].concat(o.series.map(function (s) { return o.pct ? ((s.values[i] || 0) * 100).toFixed(1) + "%" : num(s.values[i] || 0); })); });
    return frame(o.title, o.caption, plot(traces, layout, o.title), table(head, rows, true));
  }
  /* series: [{name, points: [{x: Date, y}]}] */
  function lineChart(o) {
    var traces = o.series.map(function (s, k) {
      var pts = s.points.filter(function (p) { return p.x && p.y !== null && p.y !== undefined; }).sort(function (a, b) { return a.x - b.x; });
      return { type: "scatter", mode: "lines", name: s.name, x: pts.map(function (p) { return ptNaive(p.x); }), y: pts.map(function (p) { return p.y; }), line: { color: color(s.color, k), width: 2 }, hovertemplate: s.name + ": %{y:,.1f}<extra></extra>" };
    });
    var n = traces.reduce(function (a, t) { return Math.max(a, t.x.length); }, 0);
    var layout = baseLayout({
      showlegend: o.series.length > 1, hovermode: "x unified", height: n > 40 ? 380 : 320,
      legend: { orientation: "h", x: 0, y: 1.0, yanchor: "bottom" }, margin: { t: 34 },
      xaxis: { type: "date", title: { text: "Seattle time (" + ptZone(new Date()) + ")" }, hoverformat: "%b %d, %H:%M", rangeslider: n > 40 ? { visible: true, thickness: 0.1 } : { visible: false } },
      yaxis: { rangemode: "tozero", range: o.yMax ? [0, o.yMax] : undefined }
    });
    var rows = [];
    o.series.forEach(function (s) { s.points.slice(-120).forEach(function (p) { if (p.x) rows.push([s.name, stamp(p.x), num(p.y)]); }); });
    return frame(o.title, o.caption, plot(traces, layout, o.title), n ? table(["series", "time", "value"], rows.slice(0, 400), true) : null);
  }
  function hbars(o) {
    var items = o.items.slice().reverse(), fmt = o.fmt || num;
    var traces = [{ type: "bar", orientation: "h", x: items.map(function (i) { return i.value; }), y: items.map(function (i) { return i.label; }), text: items.map(function (i) { return fmt(i.value); }), textposition: "outside", cliponaxis: false, marker: { color: color(null, 0) }, hovertemplate: "%{y}: %{text}<extra></extra>" }];
    var layout = baseLayout({ height: Math.max(180, items.length * 30 + 40), margin: { l: 150, r: 60, t: 8, b: 30 }, showlegend: false, yaxis: { automargin: true }, xaxis: { rangemode: "tozero" } });
    return frame(o.title, o.caption, plot(traces, layout, o.title), table(["item", "value"], o.items.map(function (i) { return [i.label, fmt(i.value)]; }), true));
  }
  function table(headers, rows, inline) {
    var t = el("table", {}, [
      el("thead", {}, [el("tr", {}, headers.map(function (h) { return el("th", { text: h }); }))]),
      el("tbody", {}, rows.map(function (r) { return el("tr", {}, r.map(function (c) { return el("td", {}, [c instanceof Node ? c : String(c === null || c === undefined ? "–" : c)]); })); }))
    ]);
    return inline ? el("div", { "class": "numwrap" }, [t]) : el("div", { "class": "chart" }, [t]);
  }
  function chip(status) { return el("span", { "class": "chip " + status, text: status }); }

  /* ---------- per-layer content ---------- */
  function cardsFor(layer, data) {
    var samples = {}, rows = (data.tables.pipeline_tables || []).filter(function (r) { return r.layer === layer; });
    (data.tables.samples || []).forEach(function (s) { samples[s.table_name] = s.sample_json; });
    if (!rows.length) return null;
    return el("div", { "class": "cards" }, rows.map(function (r) {
      var sample = samples[layer + "." + r.name], pretty = null;
      if (sample) { try { pretty = JSON.stringify(localize(JSON.parse(sample)), null, 1); } catch (e) { pretty = sample; } }
      return el("div", { "class": "card" }, [
        el("h4", { text: layer + "." + r.name }),
        el("p", { text: r.purpose }),
        el("p", { "class": "meta" }, [el("b", { text: "One row is: " }), r.grain]),
        el("p", { "class": "meta" }, [el("b", { text: "Built by: " }), r.built_by]),
        el("p", { "class": "meta" }, [el("b", { text: "Rows now: " }), num(r.row_count), "  ", el("b", { text: "Newest data: " }), whenText(r.newest)]),
        pretty ? el("details", {}, [el("summary", { text: "Show one example row (masked)" }), el("pre", { text: pretty })]) : null
      ]);
    }));
  }
  function dailyMetric(data, stage, metric) {
    return (data.tables.daily_metrics || []).filter(function (r) { return r.stage === stage && r.metric === metric; }).sort(function (a, b) { return a.day < b.day ? -1 : 1; });
  }
  function days(rows) { return rows.map(function (r) { return String(r.day).slice(5); }); }
  function dist(data, name) {
    return (data.tables.distributions || []).filter(function (r) { return r.name === name; }).sort(function (a, b) { return a.bucket_order - b.bucket_order; });
  }
  function distDay(rows) { return rows.length ? String(rows[0].day) : ""; }

  var EXTRA = {
    landing: function (data) {
      var thisHour = utc(data.generated_at); thisHour.setUTCMinutes(0, 0, 0);
      var full = (data.tables.hourly_snapshots || []).filter(function (r) { return utc(r.hour) < thisHour; });
      var by = group(full, "source");
      var series = Object.keys(by).sort().map(function (s) { return { name: s.replace("_", " "), points: by[s].map(function (r) { return { x: utc(r.hour), y: r.snapshots }; }) }; });
      return [lineChart({ title: "Files landed per hour (last 48 hours)", caption: "Hover for exact values; drag to zoom, double-click to reset, or use the slider below. Each point is one full hour (the current, partial hour is left out). Each file is one download. Expected: about 180 vehicle downloads, 60 trip-update downloads and 12 alert downloads per hour. Dips in the vehicle line around 3:05–3:16 am Seattle time are the agency's nightly period when buses are not reporting.", series: series })];
    },
    bronze: function (data) {
      var out = [];
      ["vehicle_positions", "trip_updates", "alerts"].forEach(function (s) {
        var files = dailyMetric(data, "bronze." + s, "distinct files"), snaps = dailyMetric(data, "bronze." + s, "distinct snapshots");
        if (files.length) out.push(barChart({ title: s.replace("_", " ") + ": files landed vs distinct snapshots per day", caption: "A download can return a snapshot the feed had already published, so there can be fewer distinct snapshots than files. The first and latest days may be partial.", categories: days(files), mode: "group", series: [{ name: "files", values: files.map(function (r) { return r.value; }) }, { name: "distinct snapshots", values: snaps.map(function (r) { return r.value; }) }] }));
      });
      return out;
    },
    silver: function (data) {
      var out = [], fixes = dailyMetric(data, "silver.vehicle_positions", "distinct fixes"), rej = dailyMetric(data, "silver.quarantine_vehicle_positions", "rejected rows");
      var rejBy = {}; rej.forEach(function (r) { rejBy[r.day] = r.value; });
      if (fixes.length) out.push(barChart({ title: "GPS fixes kept vs rejected per day", caption: "Rejected rows are not deleted: they sit in the quarantine table with a reason (no timestamp, zero coordinates, outside the region, stale).", categories: days(fixes), mode: "stack", series: [{ name: "kept (distinct fixes)", values: fixes.map(function (r) { return r.value; }) }, { name: "rejected", values: fixes.map(function (r) { return rejBy[r.day] || 0; }), color: "var(--c5)" }] }));
      var sched = dailyMetric(data, "silver.stop_visits", "scheduled stops"), meas = dailyMetric(data, "silver.stop_visits", "stops with a measured arrival");
      if (sched.length) out.push(barChart({ title: "Scheduled stops vs stops with a measured arrival, per day", caption: "Not every scheduled stop gets a measured arrival: the bus may be first seen after it, or its GPS may be missing. The share measured is the coverage.", categories: days(sched), mode: "group", series: [{ name: "scheduled stops", values: sched.map(function (r) { return r.value; }) }, { name: "measured arrival", values: meas.map(function (r) { return r.value; }) }] }));
      var mix = dist(data, "arrival_method_mix");
      if (mix.length) out.push(hbars({ title: "How arrivals were worked out (" + distDay(mix) + ")", caption: "interpolated: two GPS fixes close together bracket the arrival; wide_gap: the fixes were more than 3 minutes apart; next_stop_advance: only the feed's next-stop pointer showed the bus had passed; not_reached / no_fixes / before_first_fix: no usable arrival.", items: mix.map(function (r) { return { label: r.bucket, value: r.value }; }).sort(function (a, b) { return b.value - a.value; }) }));
      var w = dist(data, "interval_width_seconds");
      if (w.length) out.push(barChart({ title: "How precisely arrivals are known: interval width (" + distDay(w) + ")", caption: "The interval is the time between the last GPS fix before the bus reached the stop and the first one after. Narrow is better.", categories: w.map(function (r) { return r.bucket; }), series: [{ name: "stops", values: w.map(function (r) { return r.value; }) }], xLabel: "seconds" }));
      var cov = dist(data, "coverage_by_pacific_hour");
      if (cov.length) out.push(barChart({ title: "Share of stops with a measured arrival, by hour of day (Pacific, " + distDay(cov) + ")", caption: "Late-night coverage is lower because fewer buses run and the agency's feed is quiet.", categories: cov.map(function (r) { return r.bucket; }), series: [{ name: "share measured", values: cov.map(function (r) { return r.value; }) }], pct: true, yMax: 1, xLabel: "hour (Pacific)" }));
      var pl = dist(data, "pointer_lead_feet");
      if (pl.length) out.push(barChart({ title: "How far short of the stop was the bus when the agency's next-stop pointer moved? (" + distDay(pl) + ")", caption: "The feed's next-stop pointer is supposed to say which stop is next. It usually moves a little before the bus reaches the stop: this counts the arrivals where it moved while the bus was still short of the stop (median about 75 feet). When that conflicts with the GPS we keep the GPS interval and record the gap, rather than trusting the pointer.", categories: pl.map(function (r) { return r.bucket; }), series: [{ name: "stops", values: pl.map(function (r) { return r.value; }) }], xLabel: "feet short of the stop" }));
      var fx = dist(data, "fixes_per_trip");
      if (fx.length) out.push(barChart({ title: "GPS fixes per trip (" + distDay(fx) + ")", caption: "A typical trip has around 100 fixes (one about every 20 seconds). Trips with none were never seen.", categories: fx.map(function (r) { return r.bucket; }), series: [{ name: "trips", values: fx.map(function (r) { return r.value; }) }], xLabel: "fixes" }));
      return out;
    },
    gold: function (data) {
      var out = [], hist = dist(data, "delay_histogram_minutes");
      if (hist.length) out.push(barChart({ title: "How late were buses at timepoints? (" + distDay(hist) + ")", caption: "Each bar counts arrivals at timepoints (stops with a published time) by minutes late; negative is early. The ends pool everything beyond −10 and +20 minutes.", categories: hist.map(function (r) { return r.bucket; }), series: [{ name: "arrivals", values: hist.map(function (r) { return r.value; }) }], xLabel: "minutes late (negative = early)" }));
      var ot = dist(data, "on_time_by_day_common_lens"), byDay = group(ot, "day"), ds = Object.keys(byDay).sort();
      if (ds.length) {
        var totals = ds.map(function (d) { return byDay[d].reduce(function (a, r) { return a + r.value; }, 0) || 1; });
        out.push(barChart({ title: "Early, on time, late and uncertain, per day (share of measured timepoint arrivals)", caption: "On time = between 1 minute early and 5 minutes late, judged on the whole interval. 'Uncertain' means the interval straddles a limit, so we do not guess. The first day is partial.", categories: ds.map(function (d) { return d.slice(5); }), mode: "stack", pct: true, yMax: 1, series: ["early", "on_time", "late", "uncertain"].map(function (k) { return { name: k.replace("_", " "), color: STATE_COLOR[k], values: ds.map(function (d, i) { var r = byDay[d].filter(function (x) { return x.bucket === k; })[0]; return r ? r.value / totals[i] : 0; }) }; }) }));
      }
      var status = ["observed", "not_observed", "canceled"].map(function (k) { return { k: k, rows: dailyMetric(data, "gold.trip_performance", "trips " + k) }; });
      if (status[0].rows.length) out.push(barChart({ title: "Scheduled trips by outcome, per day", caption: "observed: seen on the road; not_observed: scheduled but never seen (or the day is incomplete); canceled: flagged canceled by the agency (some of those still ran part of the trip).", categories: days(status[0].rows), mode: "stack", series: status.map(function (s, i) { var by = {}; s.rows.forEach(function (r) { by[r.day] = r.value; }); return { name: s.k.replace("_", " "), values: status[0].rows.map(function (r) { return by[r.day] || 0; }), color: [STATE_COLOR.on_time, "var(--c6)", "var(--c5)"][i] }; }) }));
      var pred = dist(data, "prediction_median_abs_error_seconds");
      if (pred.length) {
        var horizons = [5, 15, 30], srcs = ["kcm", "schedule", "persistence"];
        out.push(barChart({ title: "How accurate are the agency's arrival predictions? (median error, last 3 complete days)", caption: "Median absolute error in seconds, comparing the agency's prediction with the arrival we inferred later, at 5, 15 and 30 minutes ahead. 'schedule' always predicts the timetable time; 'persistence' assumes the bus's current delay carries on. Lower is better.", categories: horizons.map(function (h) { return h + " min ahead"; }), mode: "group", series: srcs.map(function (s) { return { name: s === "kcm" ? "agency (KCM)" : s, values: horizons.map(function (h) { var r = pred.filter(function (x) { return x.bucket === s + " " + h + " min"; })[0]; return r ? r.value : 0; }) }; }) }));
      }
      return out;
    },
    ops: function (data) {
      var out = [], checks = (data.tables.check_latest || []).slice().sort(function (a, b) {
        var o = { fail: 0, warn: 1, pass: 2 }; return (o[a.status] - o[b.status]) || (a.check_name < b.check_name ? -1 : 1);
      });
      if (checks.length) {
        out.push(el("h3", { text: "Newest result of every check" }));
        out.push(el("p", { "class": "note", text: "fail = a rule broke and the job alerts; warn = a known limit we have written down; pass = healthy. Open a row to see the SQL that produced the number." }));
        out.push(table(["status", "suite", "check", "value", "rule", "what it proves"], checks.map(function (c) {
          return [chip(c.status), c.suite, c.check_name, num(c.value, 4), c.rule, el("details", {}, [el("summary", { text: c.explanation }), el("pre", { text: c.sql })])];
        })));
      }
      var vals = data.tables.check_values_hourly || [], pick = function (name, label) { return { name: label, points: vals.filter(function (r) { return r.check_name === name; }).map(function (r) { return { x: utc(r.run_ts), y: r.value }; }) }; };
      out.push(lineChart({ title: "Minutes since each source was last ingested (hourly checks)", caption: "The ingest job runs every 30 minutes, so this should saw-tooth between about 0 and 35. A line that keeps climbing past 100 means runs were missed.", series: [pick("bronze_vehicle_positions_minutes_since_last_ingest", "vehicle positions"), pick("bronze_trip_updates_minutes_since_last_ingest", "trip updates"), pick("bronze_alerts_minutes_since_last_ingest", "alerts")] }));
      var trend = data.tables.check_trend || [];
      if (trend.length) {
        var byDay = group(trend, "day"), dk = Object.keys(byDay).sort();
        out.push(barChart({ title: "Checks that failed or warned, per day", caption: "Failures alert the owner; warnings are known limits and are expected to stay until fixed.", categories: dk.map(function (d) { return d.slice(5); }), mode: "stack", series: [{ name: "failures", color: "var(--c5)", values: dk.map(function (d) { return byDay[d].reduce(function (a, r) { return a + r.failures; }, 0); }) }, { name: "warnings", color: "var(--warn)", values: dk.map(function (d) { return byDay[d].reduce(function (a, r) { return a + r.warnings; }, 0); }) }] }));
      }
      return out;
    }
  };

  function renderLayers(data) {
    var host = document.getElementById("layers"), toc = document.getElementById("toc");
    data.layers.forEach(function (layer) {
      var id = "layer-" + layer.id;
      toc.appendChild(el("a", { href: "#" + id, text: layer.title.replace(/^\d+\.\s*/, "").replace(/\s*\(.*$/, "") }));
      var kids = [el("h2", { text: layer.title }), el("p", { text: layer.what }), el("div", { "class": "why" }, [el("b", { text: "Why it exists: " }), layer.why])];
      var cards = cardsFor(layer.id, data); if (cards) kids.push(cards);
      if (EXTRA[layer.id]) EXTRA[layer.id](data).forEach(function (n) { kids.push(n); });
      host.appendChild(el("section", { id: id, "class": "layer" }, kids));
    });
    ["lineage-section", "jobs-section", "cost-section", "limits-section", "glossary-section"].forEach(function (s) {
      var h = document.querySelector("#" + s + " h2");
      if (h) toc.appendChild(el("a", { href: "#" + s, text: h.textContent.split(":")[0] }));
    });
  }

  function renderMap(data) {
    var t = data.tables, byLayer = group(t.pipeline_tables || [], "layer"), now = utc(data.generated_at);
    var snaps = t.hourly_snapshots || [], lastHour = {};
    var thisHour = new Date(now); thisHour.setUTCMinutes(0, 0, 0);
    snaps.forEach(function (r) { var h = utc(r.hour); if (h >= thisHour) return; if (!lastHour[r.source] || h > lastHour[r.source].h) lastHour[r.source] = { h: h, n: r.snapshots }; });
    var sum = function (layer) { return (byLayer[layer] || []).reduce(function (a, r) { return a + (r.row_count || 0); }, 0); };
    var checks = t.check_latest || [], c = { pass: 0, warn: 0, fail: 0 };
    checks.forEach(function (r) { c[r.status] = (c[r.status] || 0) + 1; });
    var boxes = [
      ["collector", "Collector", "every minute", "pulls the feeds"],
      ["landing", "Landing", snaps.length ? num(Object.keys(lastHour).reduce(function (a, k) { return a + lastHour[k].n; }, 0)) + " files/hour" : "–", "raw files, untouched (last full hour)"],
      ["bronze", "Bronze", num(sum("bronze")) + " rows", "decoded, nothing removed"],
      ["silver", "Silver", num(sum("silver")) + " rows", "cleaned, matched, inferred"],
      ["gold", "Gold", num(sum("gold")) + " rows", "the answers"],
      ["ops", "Checks", c.pass + " pass · " + c.warn + " warn · " + c.fail + " fail", "proof it works (at last build)"]
    ];
    var host = document.getElementById("pipeline-map");
    boxes.forEach(function (b) { host.appendChild(el("a", { "class": "pipebox", href: "#layer-" + b[0] }, [el("div", { "class": "t", text: b[1] }), el("div", { "class": "n", text: b[2] }), el("div", { "class": "s", text: b[3] })])); });
  }

  /* ---------- freshness: what is live, what is hourly, and what is stale ---------- */
  function verdict(now) {
    var data = state.data, status = state.status, built = utc(data.generated_at), checked = status ? utc(status.checked_at) : null;
    var checkedMin = checked ? (now - checked) / 60000 : null, builtH = (now - built) / 3600000;
    var failing = status ? status.findings.filter(function (f) { return !f.ok; }) : [];
    var dataFails = (data.tables.check_latest || []).filter(function (c) { return c.status === "fail"; });
    if (!checked || checkedMin > STATUS_STALE_MIN) {
      return { cls: "bad", text: "Cannot confirm the pipeline is alive. " + (checked ? "The live probes were last checked " + ago(checked, now) + " (they should refresh every 15 minutes)" : "The live probe file is missing") + ", so publishing of this page may be down; the tables below are from " + ago(built, now) + "." };
    }
    if (failing.length) {
      return { cls: "bad", text: "Pipeline problem detected (probes checked " + ago(checked, now) + "): " + failing.map(function (f) { return (PROBE_LABELS[f.name] || f.name) + " — " + f.detail; }).join("; ") + "." };
    }
    if (builtH > DATA_STALE_H) {
      return { cls: "bad", text: "The live probes look healthy, but the charts and tables are " + dur((now - built) / 1000) + " old: their hourly refresh has stopped." };
    }
    if (dataFails.length) {
      return { cls: "bad", text: dataFails.length + " data check" + (dataFails.length > 1 ? "s were" : " was") + " failing when the tables were built (" + ago(built, now) + "): " + dataFails.map(function (c) { return c.check_name; }).join(", ") + "." };
    }
    return { cls: "good", text: "Pipeline healthy. Live probes checked " + ago(checked, now) + "; charts and tables built " + ago(built, now) + "." };
  }
  function renderFreshness() {
    var host = document.getElementById("freshness"), now = new Date(), data = state.data, status = state.status;
    var built = utc(data.generated_at), v = verdict(now);
    var newest = (data.tables.pipeline_tables || []).filter(function (r) { return r.layer === "bronze" && r.name === "vehicle_positions"; })[0];
    var newestBus = newest && newest.newest ? utc(newest.newest) : null;
    var checks = data.tables.check_latest || [], c = { pass: 0, warn: 0, fail: 0 };
    checks.forEach(function (r) { c[r.status] = (c[r.status] || 0) + 1; });
    var probes = status ? status.findings.map(function (f) {
      return el("li", {}, [chip(f.ok ? "pass" : "fail"), " ", el("b", { text: (PROBE_LABELS[f.name] || f.name) + ": " }), f.detail]);
    }) : [el("li", { text: "No live probe data available." })];
    host.replaceChildren(
      el("div", { "class": "verdict " + v.cls, role: "status", text: v.text }),
      el("div", { "class": "fgrid" }, [
        el("div", { "class": "fcol" }, [
          el("h4", { text: "Live probes · " + (status ? "checked " + ago(utc(status.checked_at), now) : "unavailable") }),
          el("ul", { "class": "probes" }, probes),
          el("p", { "class": "note", text: "Run from outside Databricks about every 15 minutes: it asks Databricks what has happened and looks at the newest collector file. It costs no Databricks compute." })
        ]),
        el("div", { "class": "fcol" }, [
          el("h4", { text: "Charts and tables · built " + ago(built, now) }),
          el("p", { text: "Refreshed hourly (data built " + stamp(built) + "). Checks at that time: " + c.pass + " pass, " + c.warn + " known-limit warnings, " + c.fail + " failing." + (newestBus ? " The newest bus position then was " + dur((built - newestBus) / 1000) + " before the build." : "") }),
          el("p", { "class": "note", text: "Between refreshes the charts do not change. If a number looks stuck, compare these two ages: old probes mean publishing stopped, a failing probe means the pipeline itself has a problem." })
        ])
      ]),
      el("div", { id: "newdata" })
    );
    if (state.loadedGenerated && state.data.generated_at !== state.loadedGenerated) showNewData();
  }
  function showNewData() {
    var holder = document.getElementById("newdata");
    if (!holder) return;
    var b = el("button", { type: "button", "class": "btn", text: "New data is available: reload the page" });
    b.onclick = function () { location.reload(); };
    holder.appendChild(b);
  }
  function fetchJson(path, optional) {
    return fetch(path + "?t=" + Date.now(), { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(path + ": HTTP " + r.status);
      return r.json();
    }).catch(function (e) { if (optional) return null; throw e; });
  }
  function poll() {
    Promise.all([fetchJson("data/site.json", true), fetchJson("data/status.json", true)]).then(function (res) {
      if (res[0]) state.data = res[0];
      state.status = res[1];  // a missing status file is itself a finding
      renderFreshness();
    });
  }

  /* ---------- jobs, cost, lineage, glossary ---------- */
  function renderJobs(data) {
    var runs = data.tables.job_runs || [], now = utc(data.generated_at), host = document.getElementById("job-strips");
    var cadence = { ingest: 30, monitor: 60, silver: 1440 }, by = group(runs, "job_name");
    Object.keys(by).sort().forEach(function (name) {
      var short = name.replace("transit-reliability-prod-", ""), step = cadence[short]; if (!step) return;
      var list = by[name].map(function (r) { return { s: utc(r.start_time), e: utc(r.end_time), res: r.result_state }; }).filter(function (r) { return r.s; }).sort(function (a, b) { return a.s - b.s; });
      var strip = el("div", { "class": "strip" }), prev = null;
      list.forEach(function (r) {
        if (prev) { var missed = Math.round((r.s - prev) / 60000 / step) - 1; for (var i = 0; i < missed && i < 400; i++) strip.appendChild(el("i", { "class": "gap", title: "expected run missing (after " + stamp(prev) + ")" })); }
        var cls = r.res === "SUCCEEDED" ? "" : r.res ? "bad" : "run";
        strip.appendChild(el("i", { "class": cls, title: stamp(r.s) + " · " + (r.res || "running") + " · " + dur((r.e - r.s) / 1000) }));
        prev = r.s;
      });
      var durs = list.map(function (r) { return (r.e - r.s) / 1000; }).sort(function (a, b) { return a - b; }), med = durs.length ? durs[Math.floor(durs.length / 2)] : null;
      var ok = list.filter(function (r) { return r.res === "SUCCEEDED"; }).length;
      host.appendChild(el("div", { "class": "chart" }, [el("h4", { text: short + " job: every " + (step >= 1440 ? "day" : step + " minutes") }), el("p", { "class": "cap", text: list.length + " runs in 7 days, " + ok + " succeeded, typical run " + dur(med) + ", latest " + ago(list.length ? list[list.length - 1].s : null, now) + " (as of the last build). Hover a square for its time and duration." }), strip]));
    });
    var tasks = (data.tables.job_task_runs || []).filter(function (r) { return /-silver$/.test(r.job_name); });
    if (tasks.length) {
      var latestRun = tasks.reduce(function (a, r) { return !a || r.start_time > a.start_time ? r : a; }, null).run_id;
      var items = tasks.filter(function (r) { return r.run_id === latestRun; }).map(function (r) { return { label: r.task_key, value: (utc(r.end_time) - utc(r.start_time)) / 1000 }; }).sort(function (a, b) { return b.value - a.value; });
      document.getElementById("task-times").appendChild(hbars({ title: "Duration of each task in the latest daily run", caption: "The tasks run as a graph (parsing and matching before stop visits, stop visits before gold). Stop visits and trip updates are the heaviest steps.", items: items, fmt: dur }));
    }
  }

  function renderBilling(data) {
    var rows = data.tables.billing_daily || [], host = document.getElementById("billing");
    if (!rows.length) { host.appendChild(el("p", { text: "No usage data yet." })); return; }
    var dates = Object.keys(group(rows, "usage_date")).sort(), labels = Object.keys(group(rows, "label")).sort();
    host.appendChild(barChart({ title: "DBU used per day, by what used it (last 14 days)", caption: "Days are Seattle calendar days. Usage data arrives about a day late, so the latest bar is partial. Click a legend entry to hide or isolate a series.", categories: dates.map(function (d) { return d.slice(5); }), mode: "stack", series: labels.map(function (l) { return { name: l, values: dates.map(function (d) { var r = rows.filter(function (x) { return x.usage_date === d && x.label === l; })[0]; return r ? Number(r.dbu) : 0; }) }; }) }));
  }

  function renderLineage(data) {
    var edges = data.tables.lineage_edges || [], host = document.getElementById("lineage");
    if (!edges.length) { host.appendChild(el("p", { text: "No lineage recorded yet." })); return; }
    var names = {}; edges.forEach(function (e) { names[e.source_table] = 1; names[e.target_table] = 1; });
    var colOf = function (n) { var m = n.match(/_(bronze|silver|gold)\./); return m ? ["bronze", "silver", "gold"].indexOf(m[1]) : 1; };
    var cols = [[], [], []]; Object.keys(names).sort().forEach(function (n) { cols[colOf(n)].push(n); });
    var rowH = 26, W = 860, H = Math.max.apply(null, cols.map(function (c) { return c.length; })) * rowH + 40, xs = [10, 300, 590], bw = 260, pos = {}, paths = [];
    var root = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "table lineage", width: W });
    ["bronze", "silver", "gold"].forEach(function (t, i) { root.appendChild(sv("text", { x: xs[i], y: 14, "font-size": 12, "font-weight": 700, fill: "var(--muted)", text: t })); });
    cols.forEach(function (c, i) { c.forEach(function (n, j) { pos[n] = { x: xs[i], y: 28 + j * rowH }; }); });
    edges.forEach(function (e) {
      var a = pos[e.source_table], b = pos[e.target_table]; if (!a || !b) return;
      var x1 = a.x + bw, y1 = a.y + 10, x2 = b.x, y2 = b.y + 10, same = a.x === b.x;
      var d = same ? "M" + (a.x + bw) + " " + y1 + " C" + (a.x + bw + 30) + " " + y1 + " " + (b.x + bw + 30) + " " + y2 + " " + (b.x + bw) + " " + y2 : "M" + x1 + " " + y1 + " C" + (x1 + 20) + " " + y1 + " " + (x2 - 20) + " " + y2 + " " + x2 + " " + y2;
      var p = sv("path", { d: d, fill: "none", stroke: "var(--accent)", "stroke-opacity": 0.3, "stroke-width": 1.4 });
      root.appendChild(p); paths.push({ el: p, a: e.source_table, b: e.target_table });
    });
    function highlight(name) {
      paths.forEach(function (p) {
        var on = !name || p.a === name || p.b === name;
        p.el.setAttribute("stroke-opacity", name ? (on ? 0.95 : 0.06) : 0.3);
        p.el.setAttribute("stroke-width", name && on ? 2.4 : 1.4);
      });
    }
    Object.keys(pos).forEach(function (n) {
      var p = pos[n], short = n.replace("workspace.", "").replace(/^[a-z_]*?transit_/, "");
      var g = sv("g", { style: "cursor:pointer" }, [
        sv("title", { text: n + " — hover to highlight what it reads and what reads it" }),
        sv("rect", { x: p.x, y: p.y, width: bw, height: 20, rx: 4, fill: "var(--soft)", stroke: "var(--line)" }),
        sv("text", { x: p.x + 6, y: p.y + 14, "font-size": 11, fill: "var(--fg)", text: short })
      ]);
      g.addEventListener("mouseenter", function () { highlight(n); });
      g.addEventListener("mouseleave", function () { highlight(null); });
      root.appendChild(g);
    });
    host.appendChild(root);
  }

  function renderGlossary(data) {
    var dl = document.getElementById("glossary");
    Object.keys(data.glossary).forEach(function (k) { dl.appendChild(el("dt", { text: k })); dl.appendChild(el("dd", { text: data.glossary[k] })); });
  }

  function fail(message) {
    var node = document.getElementById("freshness");
    node.replaceChildren(el("div", { "class": "verdict bad", role: "status", text: message }));
  }

  function start(data, status) {
    state.data = data; state.status = status; state.loadedGenerated = data.generated_at;
    try {
      renderMap(data); renderLayers(data); renderLineage(data); renderJobs(data); renderBilling(data); renderGlossary(data); renderFreshness();
      flushPlots();
      document.getElementById("attribution").textContent = data.attribution;
      document.getElementById("generated").textContent = "Charts and tables built " + stamp(utc(data.generated_at)) + (Object.keys(data.errors || {}).length ? " · sections unavailable: " + Object.keys(data.errors).join(", ") : "");
    } catch (e) { fail("The page could not be drawn: " + e.message); throw e; }
    setInterval(function () { renderFreshness(); }, TICK_MS);
    setInterval(poll, POLL_MS);
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", restyleTheme);
    }
  }

  Promise.all([fetchJson("data/site.json", false), fetchJson("data/status.json", true)])
    .then(function (res) { start(res[0], res[1]); })
    .catch(function (e) { fail("Could not load the data: " + e.message + ". If you can read this, the page is up but its data file is missing or unreachable."); });
})();
