/* The Desk — a dumb renderer for /api/state.
 *
 * Two rules, both learned the hard way:
 * 1. Never innerHTML. Token symbols are third-party text: a coin called
 *    `<img src=x onerror=...>` must render as that string, not as an image tag.
 *    Everything goes in through textContent.
 * 2. Own no logic. Every number comes from desk/telemetry.py so the browser and
 *    the console cannot disagree about what the desk did.
 */
"use strict";

const REFRESH_MS = 2000;
const NS = "http://www.w3.org/2000/svg";

const $ = (sel) => document.querySelector(sel);

function node(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function svg(tag, attrs) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  return n;
}

/* ---------- formatting ---------- */
const money = (v) => (v === null || v === undefined || Number.isNaN(v))
  ? "—" : "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v) => (v === null || v === undefined) ? "—"
  : (v > 0 ? "+" : v < 0 ? "−" : "") + "$" + Math.abs(Number(v)).toFixed(2);
const pct = (v) => (v === null || v === undefined) ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(1) + "%";
const price = (v) => (!v ? "—" : Number(v) < 0.01 ? Number(v).toExponential(2) : "$" + Number(v).toFixed(4));

function dur(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m " + String(s % 60).padStart(2, "0") + "s";
  const h = Math.floor(m / 60);
  return h + "h " + String(m % 60).padStart(2, "0") + "m";
}

const clock = (iso) => (iso ? iso.slice(11, 19) : "—");
const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");

/* ---------- pills ---------- */
function renderPills(s) {
  const host = $("#pills");
  host.replaceChildren();
  const mode = (s.health && s.health.mode) || "idle";
  host.appendChild(node("span", "pill info", mode.toUpperCase()));
  host.appendChild(s.health && s.health.key_ok
    ? node("span", "pill ok", "key ok")
    : node("span", "pill warn", "no key"));
  if (s.health && s.health.kill_file) host.appendChild(node("span", "pill bad", "kill armed"));
  host.appendChild(node("span", "pill", "read-only"));
}

/* ---------- equity ---------- */
function renderEquity(s) {
  const host = $("#equity-chart");
  const points = s.equity_curve || [];
  host.replaceChildren();
  $("#equity-hint").textContent = points.length === 1 ? "1 cycle" : points.length ? points.length + " cycles" : "";
  if (points.length < 2) {
    host.appendChild(node("div", "chart-empty",
      points.length ? "one cycle so far, waiting for a second point" : "no cycles yet"));
    return;
  }

  const w = host.clientWidth || 600, h = host.clientHeight || 132;
  const pad = { t: 10, r: 62, b: 16, l: 4 };
  const values = points.map((p) => p.equity);
  const base = s.starting_equity;
  let lo = Math.min(...values, base), hi = Math.max(...values, base);
  if (hi - lo < 1) { hi += 1; lo -= 1; }
  const span = hi - lo;
  lo -= span * 0.08;
  hi += span * 0.08;

  const x = (i) => pad.l + (i / (points.length - 1)) * (w - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);

  const chart = svg("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}` });
  const defs = svg("defs");
  const grad = svg("linearGradient", { id: "eqfill", x1: "0", y1: "0", x2: "0", y2: "1" });
  grad.appendChild(svg("stop", { offset: "0%", "stop-color": "#48d5ff", "stop-opacity": ".35" }));
  grad.appendChild(svg("stop", { offset: "100%", "stop-color": "#48d5ff", "stop-opacity": "0" }));
  defs.appendChild(grad);
  chart.appendChild(defs);

  if (base >= lo && base <= hi) {
    chart.appendChild(svg("line", { class: "base", x1: pad.l, x2: w - pad.r, y1: y(base), y2: y(base) }));
  }
  chart.appendChild(svg("line", { class: "axis", x1: pad.l, x2: w - pad.r, y1: h - pad.b, y2: h - pad.b }));

  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  chart.appendChild(svg("path", {
    class: "area",
    d: `${line} L${x(points.length - 1).toFixed(1)},${h - pad.b} L${pad.l},${h - pad.b} Z`,
  }));
  chart.appendChild(svg("path", { class: "line", d: line }));

  const last = points[points.length - 1].equity;
  chart.appendChild(svg("circle", { cx: x(points.length - 1), cy: y(last), r: 3, fill: "#48d5ff" }));

  for (const [value, label] of [[hi, "hi"], [lo, "lo"]]) {
    const t = svg("text", { class: "tick", x: w - 2, y: y(value) + 3, "text-anchor": "end" });
    t.textContent = money(value);
    chart.appendChild(t);
  }
  const firstLabel = svg("text", { class: "tick", x: pad.l, y: h - 4 });
  firstLabel.textContent = clock(points[0].t);
  const lastLabel = svg("text", { class: "tick", x: w - pad.r, y: h - 4, "text-anchor": "end" });
  lastLabel.textContent = clock(points[points.length - 1].t);
  chart.append(firstLabel, lastLabel);
  host.appendChild(chart);
}

/* ---------- the five seats ---------- */
function renderFunnel(s) {
  const host = $("#funnel");
  host.replaceChildren();
  const p = s.pipeline || { stages: [] };
  const scale = Math.max(1, p.seen || 0);

  for (const stage of p.stages || []) {
    const row = node("div", "stage");
    const label = node("div", "stage-label", stage.label);
    label.appendChild(node("span", "sub", stage.hint));
    const bar = node("div", "stage-bar");
    const fill = node("div", "stage-fill");
    fill.style.width = (100 * stage.in / scale).toFixed(1) + "%";
    bar.appendChild(fill);
    if (stage.cut) {
      const cut = node("div", "stage-cut");
      // Clamped for drawing only: the badge below still reports the true count,
      // but a segment wider than its own bar would spill out of the funnel.
      cut.style.width = (100 * Math.min(stage.cut, stage.in || stage.cut) / scale).toFixed(1) + "%";
      cut.title = stage.cut + " cut here";
      bar.appendChild(cut);
    }
    const count = node("div", "stage-count", stage.in);
    if (stage.cut) count.appendChild(node("span", "cut", "−" + stage.cut));
    row.append(label, bar, count);
    host.appendChild(row);
  }

  const foot = $("#funnel-foot");
  foot.replaceChildren();
  foot.append(
    node("span", null, `${p.seen || 0} seen · ${p.parked || 0} parked · ${p.benched || 0} benched · `),
    node("b", null, `${p.approved || 0} approved`),
  );
  // Five empty bars look broken. They are not: every pool was a newborn.
  if ((p.seen || 0) > 0 && !(p.judged || 0)) {
    foot.appendChild(node("div", null,
      "no pool was old enough to judge this cycle; every one is on the watchlist"));
  }
}

/* ---------- why they die ---------- */
const STAGE_COLOURS = {
  free: "rgba(245, 185, 66, .55)",
  chain: "rgba(72, 213, 255, .55)",
  analyst: "rgba(167, 139, 250, .6)",
  risk: "rgba(255, 92, 108, .55)",
};

function renderWall(host, rows, label) {
  host.replaceChildren();
  if (!rows || !rows.length) {
    host.appendChild(node("div", "wall-empty", "nothing rejected yet"));
    return;
  }
  const max = Math.max(...rows.map((r) => r.count));
  for (const r of rows) {
    const row = node("div", "wall-row");
    const outer = node("div", "wall-outer");
    const bar = node("div", "wall-bar");
    bar.style.width = (100 * r.count / max).toFixed(1) + "%";
    bar.style.background = STAGE_COLOURS[r.stage] || "rgba(127, 138, 158, .5)";
    const text = node("div", "wall-text", label ? r.stage : r.reason);
    if (!label) text.appendChild(node("span", "stage-tag", r.stage));
    text.title = label ? r.stage : r.reason + " (" + r.stage + ") · " + r.count;
    outer.append(bar, text);
    row.append(outer, node("div", "wall-n", r.count));
    host.appendChild(row);
  }
}

/* ---------- watchlist ---------- */
function renderWatch(s) {
  const w = s.watchlist || { parked: 0, next_ready_s: 0, soonest: [] };
  $("#watch-parked").textContent = w.parked;
  $("#watch-next").textContent = !w.parked ? "nothing parked"
    : w.next_ready_s < 1 ? "next one ready now" : "next ready in " + dur(w.next_ready_s);

  const host = $("#watch-list");
  host.replaceChildren();
  const floor = Math.max(1, s.min_age_minutes || 10);
  for (const row of w.soonest || []) {
    const line = node("div", "watch-row");
    const name = node("div", "watch-name", row.symbol || "?");
    name.appendChild(node("span", "dex", row.dex || ""));
    name.title = row.symbol || "?";
    const ready = row.ready_in_s < 1;
    const track = node("div", "watch-track");
    const tick = node("div", "watch-tick" + (ready ? " done" : ""));
    tick.style.width = (ready ? 100 : Math.min(100, (100 * row.waited_min) / floor)).toFixed(0) + "%";
    track.appendChild(tick);
    // Three cells always, even when ready: a missing cell used to wrap the
    // countdown onto its own line under every bar.
    line.append(name, track,
      node("div", "watch-when" + (ready ? " ready" : ""), ready ? "ready" : dur(row.ready_in_s)));
    host.appendChild(line);
  }
}

/* ---------- tables ---------- */
function table(host, headers, rows, empty) {
  host.replaceChildren();
  const head = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const [label, cls] of headers) hr.appendChild(node("th", cls, label));
  head.appendChild(hr);
  host.appendChild(head);
  const body = document.createElement("tbody");
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = node("td", "table-empty", empty);
    td.colSpan = headers.length;
    tr.appendChild(td);
    body.appendChild(tr);
  }
  for (const cells of rows) {
    const tr = document.createElement("tr");
    for (const cell of cells) {
      const td = node("td", cell.cls, cell.text);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  host.appendChild(body);
}

function renderPositions(s) {
  const rows = (s.positions || []).map((p) => ([
    { text: p.symbol, cls: "sym" },
    { text: dur(p.held_min * 60), cls: "num" },
    { text: price(p.entry), cls: "num" },
    { text: price(p.last), cls: "num" },
    { text: pct(p.pct), cls: "num " + tone(p.pct) },
    { text: money(p.cost), cls: "num" },
  ]));
  table($("#positions"), [["symbol", ""], ["held", "num"], ["entry", "num"],
    ["last", "num"], ["p/l", "num"], ["cost", "num"]], rows,
    s.ledger_exists ? "flat, no open positions" : "no ledger yet, nothing has been bought");
  $("#pos-hint").textContent = rows.length ? rows.length + " open" : "";
}

function renderClosed(s) {
  const rows = (s.closed || []).map((c) => ([
    { text: c.symbol || "?", cls: "sym" },
    { text: signed(c.pnl_usd), cls: "num " + tone(c.pnl_usd) },
    { text: c.reason || "", cls: "" },
  ]));
  table($("#closed"), [["symbol", ""], ["pnl", "num"], ["why", ""]], rows, "no closed trades");
}

/* ---------- feed ---------- */
function renderFeed(s) {
  const host = $("#feed");
  host.replaceChildren();
  const rows = s.feed || [];
  if (!rows.length) {
    host.appendChild(node("div", "wall-empty", "no journal entries yet"));
    return;
  }
  for (const row of rows) {
    const line = node("div", "feed-row k-" + (row.kind || "other"));
    const text = node("span", "x", row.text);
    text.title = row.text;          // the ellipsis must never be the only copy
    line.append(node("span", "t", clock(row.t)), text);
    host.appendChild(line);
  }
}

/* ---------- whole page ---------- */
function render(s) {
  const equity = s.equity;
  $("#equity").textContent = money(equity);
  const day = $("#day");
  day.textContent = s.day_pnl === null || s.day_pnl === undefined
    ? "no ledger yet" : "day " + signed(s.day_pnl);
  day.className = "hero-day " + tone(s.day_pnl || 0);
  $("#cash").textContent = money(s.cash);
  $("#open-count").textContent = (s.positions || []).length;
  $("#cycle-count").textContent = (s.cycles && s.cycles.count) || 0;
  $("#cycle-age").textContent = s.cycles && s.cycles.last_age_s !== null
    ? dur(s.cycles.last_age_s) + " ago" : "never";

  renderPills(s);
  renderEquity(s);
  renderFunnel(s);
  renderWall($("#wall"), s.rejects, false);
  renderWatch(s);
  renderPositions(s);
  renderClosed(s);
  renderWall($("#errors"), s.errors, true);
  renderFeed(s);

  const blank = !(s.feed || []).length && !((s.cycles || {}).count);
  $("#empty").hidden = !blank;
}

/* ---------- poll ---------- */
let failures = 0;
let timer = null;

async function tick() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    render(data);
    failures = 0;
    $("#live").classList.remove("stale");
    $("#live-text").textContent = "live · " + clock(data.generated);
  } catch (err) {
    failures += 1;
    $("#live").classList.add("stale");
    $("#live-text").textContent = "reconnecting (" + failures + ")";
  }
}

function loop() {
  clearTimeout(timer);
  timer = setTimeout(async () => { await tick(); loop(); }, REFRESH_MS);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else { tick(); loop(); }
});

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(tick, 200);   // the chart is drawn in pixels, so redraw
});

tick();
loop();