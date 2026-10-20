/* Transit pipeline transparency: renders data/site.json. No data is ever inserted as HTML. */
(function () {
  "use strict";
  var SVGNS = "http://www.w3.org/2000/svg";
  var COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];
  var STATE_COLOR = { early: "var(--c2)", on_time: "var(--c3)", late: "var(--c5)", uncertain: "var(--c6)" };
  var SECTIONS = ["collector", "landing", "bronze", "silver", "gold", "ops"];

  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    apply(node, attrs, kids);
    return node;
  }
  function sv(tag, attrs, kids) {
    var node = document.createElementNS(SVGNS, tag);
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
    return (sec / 3600).toFixed(1) + " h";
  }
  function ago(date, now) {
    return date ? dur((now - date) / 1000) + " ago" : "–";
  }
  function stamp(d) {
    return d ? d.toISOString().slice(0, 16).replace("T", " ") + " UTC" : "–";
  }
  function group(rows, key) {
    var out = {};
    rows.forEach(function (r) { (out[r[key]] = out[r[key]] || []).push(r); });
    return out;
  }

  /* ---------- charts (SVG) ---------- */
  function frame(title, caption, legend, svgNode) {
    return el("div", { "class": "chart" }, [
      title ? el("h4", { text: title }) : null,
      caption ? el("p", { "class": "cap", text: caption }) : null,
      legend && legend.length ? el("div", { "class": "legend" }, legend.map(function (l) {
        var sw = el("i"); sw.style.background = l.color;
        return el("span", {}, [sw, l.name]);
      })) : null,
      svgNode
    ]);
  }
  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v)));
    var f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  /* categories: [label]; series: [{name, values[], color?}]; mode: "stack" | "group" */
  function barChart(o) {
    var cats = o.categories, series = o.series, stack = o.mode === "stack";
    var W = Math.max(520, cats.length * (stack ? 34 : 18 * series.length + 14) + 70), H = 230, L = 52, B = 46, T = 8, R = 8;
    var totals = cats.map(function (_, i) {
      return stack ? series.reduce(function (a, s) { return a + (s.values[i] || 0); }, 0) : Math.max.apply(null, series.map(function (s) { return s.values[i] || 0; }));
    });
    var maxV = o.yMax || niceMax(Math.max.apply(null, totals.concat([0])));
    var root = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.title || "chart", width: W });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
      var y = T + (H - B - T) * (1 - f);
      root.appendChild(sv("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: "var(--line)" }));
      root.appendChild(sv("text", { x: L - 6, y: y + 4, "text-anchor": "end", "font-size": 10, fill: "var(--muted)", text: o.pct ? Math.round(maxV * f * 100) + "%" : num(maxV * f) }));
    });
    var slot = (W - L - R) / Math.max(cats.length, 1);
    cats.forEach(function (c, i) {
      var bw = stack ? slot * 0.7 : (slot * 0.8) / series.length, x0 = L + slot * i + (slot - (stack ? bw : bw * series.length)) / 2, acc = 0;
      series.forEach(function (s, k) {
        var v = s.values[i] || 0, hgt = (H - B - T) * (v / maxV);
        var y = stack ? T + (H - B - T) - (acc + hgt) : T + (H - B - T) - hgt, x = stack ? x0 : x0 + bw * k;
        var rect = sv("rect", { x: x, y: y, width: Math.max(bw - 1, 1), height: Math.max(hgt, 0), fill: s.color || COLORS[k % COLORS.length] }, [sv("title", { text: c + " · " + s.name + ": " + (o.pct ? (v * 100).toFixed(1) + "%" : num(v)) })]);
        root.appendChild(rect);
        acc += hgt;
      });
      if (cats.length <= 14 || i % Math.ceil(cats.length / 14) === 0) {
        var lab = sv("text", { x: L + slot * i + slot / 2, y: H - B + 14, "text-anchor": "middle", "font-size": 10, fill: "var(--muted)", text: c });
        root.appendChild(lab);
      }
    });
    if (o.xLabel) root.appendChild(sv("text", { x: (W + L) / 2, y: H - 6, "text-anchor": "middle", "font-size": 11, fill: "var(--muted)", text: o.xLabel }));
    var legend = series.length > 1 ? series.map(function (s, k) { return { name: s.name, color: s.color || COLORS[k % COLORS.length] }; }) : [];
    return frame(o.title, o.caption, legend, root);
  }
  /* series: [{name, points: [{x: Date, y}]}] */
  function lineChart(o) {
    var W = 720, H = 220, L = 52, B = 30, T = 8, R = 10;
    var pts = [].concat.apply([], o.series.map(function (s) { return s.points; })).filter(function (p) { return p.x && p.y !== null && p.y !== undefined; });
    var root = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.title || "chart", width: W });
    if (!pts.length) { root.appendChild(sv("text", { x: 20, y: 40, fill: "var(--muted)", text: "No data yet" })); return frame(o.title, o.caption, [], root); }
    var x0 = Math.min.apply(null, pts.map(function (p) { return +p.x; })), x1 = Math.max.apply(null, pts.map(function (p) { return +p.x; }));
    var maxV = o.yMax || niceMax(Math.max.apply(null, pts.map(function (p) { return p.y; })));
    var xp = function (t) { return L + (W - L - R) * ((t - x0) / Math.max(x1 - x0, 1)); };
    var yp = function (v) { return T + (H - B - T) * (1 - v / maxV); };
    [0, 0.5, 1].forEach(function (f) {
      root.appendChild(sv("line", { x1: L, x2: W - R, y1: yp(maxV * f), y2: yp(maxV * f), stroke: "var(--line)" }));
      root.appendChild(sv("text", { x: L - 6, y: yp(maxV * f) + 4, "text-anchor": "end", "font-size": 10, fill: "var(--muted)", text: num(maxV * f) }));
    });
    [0, 0.5, 1].forEach(function (f) {
      var t = x0 + (x1 - x0) * f;
      root.appendChild(sv("text", { x: xp(t), y: H - 10, "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle", "font-size": 10, fill: "var(--muted)", text: new Date(t).toISOString().slice(5, 16).replace("T", " ") }));
    });
    o.series.forEach(function (s, k) {
      var d = s.points.filter(function (p) { return p.x && p.y !== null && p.y !== undefined; }).sort(function (a, b) { return a.x - b.x; })
        .map(function (p, i) { return (i ? "L" : "M") + xp(+p.x).toFixed(1) + " " + yp(p.y).toFixed(1); }).join(" ");
      root.appendChild(sv("path", { d: d, fill: "none", stroke: s.color || COLORS[k % COLORS.length], "stroke-width": 1.8 }));
    });
    var legend = o.series.length > 1 ? o.series.map(function (s, k) { return { name: s.name, color: s.color || COLORS[k % COLORS.length] }; }) : [];
    return frame(o.title, o.caption, legend, root);
  }
  function hbars(o) {
    var rowH = 24, L = 190, W = 720, R = 70, H = o.items.length * rowH + 8;
    var maxV = niceMax(Math.max.apply(null, o.items.map(function (i) { return i.value; }).concat([1])));
    var root = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.title, width: W });
    o.items.forEach(function (it, i) {
      var y = 4 + i * rowH, w = (W - L - R) * (it.value / maxV);
      root.appendChild(sv("text", { x: L - 8, y: y + 14, "text-anchor": "end", "font-size": 12, fill: "var(--fg)", text: it.label }));
      root.appendChild(sv("rect", { x: L, y: y + 2, width: Math.max(w, 1), height: rowH - 8, fill: COLORS[0] }));
      root.appendChild(sv("text", { x: L + w + 6, y: y + 14, "font-size": 11, fill: "var(--muted)", text: o.fmt ? o.fmt(it.value) : num(it.value) }));
    });
    return frame(o.title, o.caption, [], root);
  }
  function table(headers, rows) {
    var t = el("table", {}, [
      el("thead", {}, [el("tr", {}, headers.map(function (h) { return el("th", { text: h }); }))]),
      el("tbody", {}, rows.map(function (r) { return el("tr", {}, r.map(function (c) { return el("td", {}, [c instanceof Node ? c : String(c === null || c === undefined ? "–" : c)]); })); }))
    ]);
    var wrap = el("div", { "class": "chart" }, [t]);
    return wrap;
  }
  function chip(status) { return el("span", { "class": "chip " + status, text: status }); }

  /* ---------- sections ---------- */
  function cardsFor(layer, data) {
    var samples = {}, rows = (data.tables.pipeline_tables || []).filter(function (r) { return r.layer === layer; });
    (data.tables.samples || []).forEach(function (s) { samples[s.table_name] = s.sample_json; });
    if (!rows.length) return null;
    return el("div", { "class": "cards" }, rows.map(function (r) {
      var sample = samples[layer + "." + r.name], pretty = null;
      if (sample) { try { pretty = JSON.stringify(JSON.parse(sample), null, 1); } catch (e) { pretty = sample; } }
      return el("div", { "class": "card" }, [
        el("h4", { text: layer + "." + r.name }),
        el("p", { text: r.purpose }),
        el("p", { "class": "meta" }, [el("b", { text: "One row is: " }), r.grain]),
        el("p", { "class": "meta" }, [el("b", { text: "Built by: " }), r.built_by]),
        el("p", { "class": "meta" }, [el("b", { text: "Rows now: " }), num(r.row_count), "  ", el("b", { text: "Newest data: " }), r.newest ? String(r.newest).slice(0, 19) : "–"]),
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
      return [lineChart({ title: "Files landed per hour (last 48 hours)", caption: "Each point is one full hour (the current, partial hour is left out). Each file is one download. Expected: about 180 vehicle downloads, 60 trip-update downloads and 12 alert downloads per hour. Dips in the vehicle line around 10:05–10:16 UTC are the agency's nightly period when buses are not reporting.", series: series })];
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
    var host = document.getElementById("layers"), toc = document.getElementById("toc"), now = utc(data.generated_at);
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
    void now;
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
      ["landing", "Landing", snaps.length ? num(Object.keys(lastHour).reduce(function (a, k) { return a + lastHour[k].n; }, 0)) + " files/hour" : "–", "raw files, untouched"],
      ["bronze", "Bronze", num(sum("bronze")) + " rows", "decoded, nothing removed"],
      ["silver", "Silver", num(sum("silver")) + " rows", "cleaned, matched, inferred"],
      ["gold", "Gold", num(sum("gold")) + " rows", "the answers"],
      ["ops", "Checks", c.pass + " pass · " + c.warn + " warn · " + c.fail + " fail", "proof it works"]
    ];
    var host = document.getElementById("pipeline-map");
    boxes.forEach(function (b) { host.appendChild(el("a", { "class": "pipebox", href: "#layer-" + b[0] }, [el("div", { "class": "t", text: b[1] }), el("div", { "class": "n", text: b[2] }), el("div", { "class": "s", text: b[3] })])); });
    void now;
  }

  function renderStatus(data) {
    var now = utc(data.generated_at), t = data.tables, checks = t.check_latest || [];
    var fails = checks.filter(function (c) { return c.status === "fail"; }).length, warns = checks.filter(function (c) { return c.status === "warn"; }).length;
    var newest = (t.pipeline_tables || []).filter(function (r) { return r.layer === "bronze" && r.name === "vehicle_positions"; })[0];
    var latest = newest && newest.newest ? utc(newest.newest) : null, lag = latest ? (now - latest) / 60000 : null;
    var node = document.getElementById("status"), msg, cls;
    if (fails > 0) { msg = fails + " check" + (fails > 1 ? "s" : "") + " failing right now"; cls = "bad"; }
    else if (lag !== null && lag > 120) { msg = "Data looks stale: newest bus position is " + dur(lag * 60) + " old"; cls = "bad"; }
    else { msg = "All " + (checks.length - warns) + " enforced checks passing" + (warns ? " (" + warns + " known-limit warnings)" : ""); cls = "good"; }
    node.className = "status " + cls;
    node.textContent = msg + (latest ? " · newest bus position " + ago(latest, now) : "") + " · page built " + stamp(now);
  }

  function renderJobs(data) {
    var runs = data.tables.job_runs || [], now = utc(data.generated_at), host = document.getElementById("job-strips");
    var cadence = { ingest: 30, monitor: 60, silver: 1440 }, by = group(runs, "job_name");
    Object.keys(by).sort().forEach(function (name) {
      var short = name.replace("transit-reliability-prod-", ""), step = cadence[short]; if (!step) return;
      var list = by[name].map(function (r) { return { s: utc(r.start_time), e: utc(r.end_time), res: r.result_state }; }).filter(function (r) { return r.s; }).sort(function (a, b) { return a.s - b.s; });
      var strip = el("div", { "class": "strip" }), prev = null;
      list.forEach(function (r) {
        if (prev) { var missed = Math.round((r.s - prev) / 60000 / step) - 1; for (var i = 0; i < missed && i < 400; i++) strip.appendChild(el("i", { "class": "gap", title: "expected run missing" })); }
        var cls = r.res === "SUCCEEDED" ? "" : r.res ? "bad" : "run";
        strip.appendChild(el("i", { "class": cls, title: stamp(r.s) + " · " + (r.res || "running") + " · " + dur((r.e - r.s) / 1000) }));
        prev = r.s;
      });
      var durs = list.map(function (r) { return (r.e - r.s) / 1000; }).sort(function (a, b) { return a - b; }), med = durs.length ? durs[Math.floor(durs.length / 2)] : null;
      var ok = list.filter(function (r) { return r.res === "SUCCEEDED"; }).length;
      host.appendChild(el("div", { "class": "chart" }, [el("h4", { text: short + " job: every " + (step >= 1440 ? "day" : step + " minutes") }), el("p", { "class": "cap", text: list.length + " runs in 7 days, " + ok + " succeeded, typical run " + dur(med) + ", latest " + ago(list.length ? list[list.length - 1].s : null, now) }), strip]));
    });
    var tasks = (data.tables.job_task_runs || []).filter(function (r) { return /-silver$/.test(r.job_name); });
    if (tasks.length) {
      var latestRun = tasks.reduce(function (a, r) { return !a || r.start_time > a.start_time ? r : a; }, null).run_id;
      var items = tasks.filter(function (r) { return r.run_id === latestRun; }).map(function (r) { return { label: r.task_key, value: (utc(r.end_time) - utc(r.start_time)) / 1000 }; }).sort(function (a, b) { return b.value - a.value; });
      document.getElementById("task-times").appendChild(hbars({ title: "Duration of each task in the latest daily run", caption: "The tasks run as a graph (parsing and matching before stop visits, stop visits before gold). Stop visits is the heaviest step.", items: items, fmt: dur }));
    }
  }

  function renderBilling(data) {
    var rows = data.tables.billing_daily || [], host = document.getElementById("billing");
    if (!rows.length) { host.appendChild(el("p", { text: "No usage data yet." })); return; }
    var dates = Object.keys(group(rows, "usage_date")).sort(), labels = Object.keys(group(rows, "label")).sort();
    host.appendChild(barChart({ title: "DBU used per day, by what used it (last 14 days)", caption: "Usage data arrives about a day late, so the latest bar is partial.", categories: dates.map(function (d) { return d.slice(5); }), mode: "stack", series: labels.map(function (l) { return { name: l, values: dates.map(function (d) { var r = rows.filter(function (x) { return x.usage_date === d && x.label === l; })[0]; return r ? Number(r.dbu) : 0; }) }; }) }));
  }

  function renderLineage(data) {
    var edges = data.tables.lineage_edges || [], host = document.getElementById("lineage");
    if (!edges.length) { host.appendChild(el("p", { text: "No lineage recorded yet." })); return; }
    var names = {}; edges.forEach(function (e) { names[e.source_table] = 1; names[e.target_table] = 1; });
    var colOf = function (n) { var m = n.match(/_(bronze|silver|gold)\./); return m ? ["bronze", "silver", "gold"].indexOf(m[1]) : 1; };
    var cols = [[], [], []]; Object.keys(names).sort().forEach(function (n) { cols[colOf(n)].push(n); });
    var rowH = 26, W = 860, H = Math.max.apply(null, cols.map(function (c) { return c.length; })) * rowH + 40, xs = [10, 300, 590], bw = 260, pos = {};
    var root = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "table lineage", width: W });
    ["bronze", "silver", "gold"].forEach(function (t, i) { root.appendChild(sv("text", { x: xs[i], y: 14, "font-size": 12, "font-weight": 700, fill: "var(--muted)", text: t })); });
    cols.forEach(function (c, i) { c.forEach(function (n, j) { pos[n] = { x: xs[i], y: 28 + j * rowH }; }); });
    edges.forEach(function (e) {
      var a = pos[e.source_table], b = pos[e.target_table]; if (!a || !b) return;
      var x1 = a.x + bw, y1 = a.y + 10, x2 = b.x, y2 = b.y + 10, same = a.x === b.x;
      var d = same ? "M" + (a.x + bw) + " " + y1 + " C" + (a.x + bw + 30) + " " + y1 + " " + (b.x + bw + 30) + " " + y2 + " " + (b.x + bw) + " " + y2 : "M" + x1 + " " + y1 + " C" + (x1 + 20) + " " + y1 + " " + (x2 - 20) + " " + y2 + " " + x2 + " " + y2;
      root.appendChild(sv("path", { d: d, fill: "none", stroke: "var(--accent)", "stroke-opacity": 0.35, "stroke-width": 1.4 }));
    });
    Object.keys(pos).forEach(function (n) {
      var p = pos[n];
      root.appendChild(sv("rect", { x: p.x, y: p.y, width: bw, height: 20, rx: 4, fill: "var(--soft)", stroke: "var(--line)" }));
      root.appendChild(sv("text", { x: p.x + 6, y: p.y + 14, "font-size": 11, fill: "var(--fg)", text: n.replace("workspace.", "").replace(/^[a-z_]*?transit_/, "") }));
    });
    host.appendChild(root);
  }

  function renderGlossary(data) {
    var dl = document.getElementById("glossary");
    Object.keys(data.glossary).forEach(function (k) { dl.appendChild(el("dt", { text: k })); dl.appendChild(el("dd", { text: data.glossary[k] })); });
  }

  function fail(message) {
    var node = document.getElementById("status");
    node.className = "status bad";
    node.textContent = message;
  }

  fetch("data/site.json", { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error("data/site.json: HTTP " + r.status);
    return r.json();
  }).then(function (data) {
    try {
      renderStatus(data); renderMap(data); renderLayers(data); renderLineage(data); renderJobs(data); renderBilling(data); renderGlossary(data);
      document.getElementById("attribution").textContent = data.attribution;
      document.getElementById("generated").textContent = "Data built " + stamp(utc(data.generated_at)) + (Object.keys(data.errors || {}).length ? " · sections unavailable: " + Object.keys(data.errors).join(", ") : "");
    } catch (e) { fail("The page could not be drawn: " + e.message); throw e; }
  }).catch(function (e) { fail("Could not load the data: " + e.message); });
})();
