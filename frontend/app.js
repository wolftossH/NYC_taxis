"use strict";
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const METRICS = {
  attractiveness: { label: "Attractiveness index", fmt: d3.format(".0f") },
  pickups_per_day: { label: "Pickups per day", fmt: d3.format(",.0f") },
  rate: { label: "Fare + tip − tolls per occupied min", fmt: d3.format("$.2f") },
  avg_duration: { label: "Avg occupied duration (min)", fmt: d3.format(".1f") },
  tip_rate: { label: "Good-tip trips by pickup zone, card (%)", fmt: d3.format(".0f") },
  do_tip_rate: { label: "Good-tip trips by dropoff zone, card (%)", fmt: d3.format(".0f") },
  card_tip_pct: { label: "Avg card tip (% of fare)", fmt: d3.format(".1f") },
  dropoffs_per_day: { label: "Dropoffs per day", fmt: d3.format(",.0f") },
  pickups_per_dropoff: { label: "Pickups per dropoff", fmt: d3.format(".2f") },
  od_prob: { label: "Origin–destination probability from the selected zone (%)", fmt: d3.format(".2f") },
};
/* OD probability (%) of each destination for the selected pickup zone, or null when there is none to show. */
const odProbs = () => (state.zone && od && !od.error && od.total && od.all
  ? new Map(od.all.map((d) => [d.zone, 100 * d.prob])) : null);
/* Time of day = 15-minute slots 0-95, inclusive range s0..s1. The UI shows "from" and an exclusive "to" time. */
const SLOTS = 96;
const hhmm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const toMin = (v) => { const [h, m] = String(v).split(":").map(Number); return Number.isFinite(h) ? h * 60 + (m || 0) : null; };
const slotLabel = (t) => hhmm(Math.round(t * 60)); // t in hours (slot / 4)
const THIN = 10; // pickups/day below which a zone's rate rests on a small sample
const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};
const state = { start: "", end: "", weekdays: new Set([0, 1, 2, 3, 4, 5, 6]), s0: 0, s1: SLOTS - 1,
  minTrips: 30, metric: "attractiveness", zone: null, compare: null };
let od = null;
let timeBtns = [];
let zones, basemap, byId = new Map(), summary, series, meta, paths;

/* Backdrop labels: [text, lon, lat, kind, rotation in degrees]. Water in italics, land in small caps. */
const PLACES = [
  ["Hudson River", -74.0105, 40.792, "water", -68],
  ["Upper Bay", -74.047, 40.668, "water", 0],
  ["Lower Bay", -74.045, 40.535, "water", 0],
  ["Jamaica Bay", -73.835, 40.612, "water", 0],
  ["Atlantic Ocean", -73.86, 40.548, "water", 0],
  ["Newark Bay", -74.137, 40.67, "water", -60],
  ["Long Island Sound", -73.765, 40.835, "water", 0],
  ["New Jersey", -74.195, 40.79, "land", 0],
  ["Nassau", -73.63, 40.705, "land", 0],
];

/* Build a DOM node safely: labels come from data, so only textContent is used. */
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  Object.assign(n, props);
  kids.forEach((k) => n.append(k));
  return n;
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

const query = () => new URLSearchParams({ start: state.start, end: state.end,
  weekdays: [...state.weekdays].sort().join(","), slot_from: state.s0, slot_to: state.s1, min_trips: state.minTrips });

async function init() {
  // The land backdrop is decoration: an older server without /api/basemap just gets plain water.
  [meta, zones, basemap] = await Promise.all([getJSON("/api/meta"), getJSON("/api/zones"),
    getJSON("/api/basemap").catch(() => ({ type: "FeatureCollection", features: [] }))]);
  zones.features.forEach((f) => byId.set(f.properties.LocationID, f.properties));
  applyMeta();

  const addDays = (iso, n) => new Date(Date.parse(iso) + n * 864e5).toISOString().slice(0, 10);
  const setRange = (a, b) => { state.start = $("start").value = a < meta.min_date ? meta.min_date : a; state.end = $("end").value = b; refresh(); };
  [["Full period", () => setRange(meta.min_date, meta.max_date)],
   ["Last 30 days", () => setRange(addDays(meta.max_date, -29), meta.max_date)],
   ["Last 14 days", () => setRange(addDays(meta.max_date, -13), meta.max_date)],
   ["Last 7 days", () => setRange(addDays(meta.max_date, -6), meta.max_date)]]
    .forEach(([t, f]) => $("presets").append(el("button", { textContent: t, onclick: f })));

  const dayBtns = [];
  const syncDays = () => dayBtns.forEach((b, i) => { const on = state.weekdays.has(i); b.classList.toggle("on", on); b.ariaPressed = String(on); });
  DAYS.forEach((d, i) => {
    const b = el("button", { textContent: d, type: "button" });
    b.onclick = () => {
      state.weekdays.has(i) ? state.weekdays.delete(i) : state.weekdays.add(i);
      if (!state.weekdays.size) state.weekdays.add(i);
      syncDays(); refresh();
    };
    dayBtns.push(b); $("weekdays").append(b);
  });
  [["Every day", [0, 1, 2, 3, 4, 5, 6]], ["Weekdays", [0, 1, 2, 3, 4]], ["Weekends", [5, 6]]].forEach(([t, days]) =>
    $("dayPresets").append(el("button", { textContent: t, type: "button", onclick: () => { state.weekdays = new Set(days); syncDays(); refresh(); } })));
  syncDays();
  // Presets in whole hours: [label, from hour, to hour (exclusive)]
  timeBtns = [["Morning rush", 6, 10], ["Midday", 10, 16], ["Evening rush", 16, 20], ["Late night", 20, 24], ["Overnight", 0, 6], ["All day", 0, 24]]
    .map(([t, a, b]) => {
      const btn = el("button", { textContent: t, type: "button", title: `${hhmm(a * 60)} to ${hhmm(b * 60)}` });
      btn.dataset.a = a * 4; btn.dataset.b = b * 4 - 1;
      btn.onclick = () => setSlots(a * 4, b * 4 - 1);
      $("hourPresets").append(btn); return btn;
    });
  for (const [k, m] of Object.entries(METRICS)) $("metric").add(new Option(m.label, k));
  const bind = (id, key, num) => $(id).addEventListener("change", (e) => {
    state[key] = num ? +e.target.value : e.target.value; refresh();
  });
  bind("start", "start"); bind("end", "end");
  bind("minTrips", "minTrips", 1); bind("metric", "metric");
  // "To" is exclusive; 00:00 as the end means midnight. Times snap down to the 15-minute grid.
  const readTimes = () => {
    const a = toMin($("t0").value), b = toMin($("t1").value);
    if (a == null || b == null) return;
    setSlots(Math.floor(a / 15), (b === 0 ? 1440 : Math.ceil(b / 15) * 15) / 15 - 1);
  };
  $("t0").addEventListener("change", readTimes); $("t1").addEventListener("change", readTimes);
  // Optional deep link: ?zone=161&compare=236&t0=16:00&t1=20:00 (old h0=16&h1=19 still works)
  const url = new URLSearchParams(location.search);
  if (url.get("zone")) state.zone = +url.get("zone");
  if (url.get("compare")) state.compare = +url.get("compare");
  if (METRICS[url.get("metric")]) { state.metric = url.get("metric"); $("metric").value = state.metric; }
  if (url.get("h0")) state.s0 = 4 * +url.get("h0");
  if (url.get("h1")) state.s1 = 4 * +url.get("h1") + 3;
  if (url.get("t0") && toMin(url.get("t0")) != null) state.s0 = Math.floor(toMin(url.get("t0")) / 15);
  if (url.get("t1") && toMin(url.get("t1")) != null) state.s1 = (toMin(url.get("t1")) || 1440) / 15 - 1;
  setSlots(state.s0, state.s1, false);
  $("themeBtn").onclick = () => { document.documentElement.dataset.theme = isDark() ? "light" : "dark"; render(); };
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render());
  buildMap();
  initDataPanel();
  await refresh();
}

/* Date range and subtitle from /api/meta; also used after the data panel loads new data. */
function applyMeta() {
  state.start = meta.min_date; state.end = meta.max_date;
  $("start").min = $("end").min = meta.min_date; $("start").max = $("end").max = meta.max_date;
  $("start").value = state.start; $("end").value = state.end;
  $("sub").textContent = `${meta.trips.toLocaleString()} trips, ${meta.min_date} to ${meta.max_date}`;
}

function setSlots(a, b, go = true) {
  a = Math.max(0, Math.min(SLOTS - 1, Math.floor(a))); b = Math.max(0, Math.min(SLOTS - 1, Math.floor(b)));
  if (a > b) [a, b] = [b, a];
  state.s0 = a; state.s1 = b;
  $("t0").value = hhmm(a * 15); $("t1").value = hhmm((b + 1) * 15);
  if (go) refresh();
}

let token = 0;
async function refresh() {
  if (state.start > state.end) return;
  timeBtns.forEach((b) => b.classList.toggle("on", +b.dataset.a === state.s0 && +b.dataset.b === state.s1));
  const me = ++token, q = query();
  const sq = new URLSearchParams(q); sq.delete("min_trips"); if (state.zone) sq.set("zone", state.zone);
  document.body.classList.add("loading"); // keep the previous frame, dimmed
  try {
    // Destinations are optional: if that request fails, the rest of the page still updates.
    const [s, t, o] = await Promise.all([getJSON("/api/summary?" + q), getJSON("/api/timeseries?" + sq),
      state.zone ? getJSON("/api/od?" + sq).catch((e) => ({ error: e.message })) : null]);
    if (me !== token) return;
    summary = s; series = t; od = o; $("err").hidden = true; render();
  } catch (e) {
    if (me !== token) return;
    $("err").textContent = `Couldn't load data (${e.message}). Check that the server is running, then change any filter to retry.`;
    $("err").hidden = false;
  } finally { if (me === token) document.body.classList.remove("loading"); }
}

function selectZone(id) {
  state.zone = id === state.zone ? null : id;
  if (state.compare === state.zone || !state.zone) state.compare = null;
  refresh();
}

function render() {
  if (!summary) return;
  renderStats(); renderPicks(); renderMap(); renderScatter(); renderDetail(); renderTable(); renderVega();
}
const rowOf = () => new Map(summary.zones.map((z) => [z.zone, z]));
const zname = (id) => byId.get(id)?.zone ?? `Zone ${id}`;

function renderStats() {
  const z = state.zone ? rowOf().get(state.zone) : summary.totals;
  const items = [
    ["Showing", state.zone ? `${zname(state.zone)} (${byId.get(state.zone)?.borough})` : "All zones", "", 17],
    ["Pickups per day", z ? METRICS.pickups_per_day.fmt(z.pickups_per_day) : "–", `over ${summary.n_days} day(s)`],
    ["Fare + tip − tolls per min", z?.rate != null ? METRICS.rate.fmt(z.rate) : "–", "per occupied minute"],
    ["Avg occupied duration", z?.avg_duration != null ? METRICS.avg_duration.fmt(z.avg_duration) + " min" : "–", "per trip"],
    ["Card tips over 20%", z?.tip_rate != null ? METRICS.tip_rate.fmt(z.tip_rate) + "%" : "–",
      z?.card_tip_pct != null ? `of card trips; avg tip ${METRICS.card_tip_pct.fmt(z.card_tip_pct)}% of fare` : "of card trips"],
    ["Dropoffs per day", z?.dropoffs_per_day != null ? METRICS.dropoffs_per_day.fmt(z.dropoffs_per_day) : "–",
      state.zone ? (z?.pickups_per_dropoff != null ? `${METRICS.pickups_per_dropoff.fmt(z.pickups_per_dropoff)} pickups per dropoff` : "no dropoffs") : "by dropoff time"],
  ];
  $("stats").replaceChildren(...items.map(([l, v, s, fs]) => el("div", { className: "stat" + (fs ? " pick" : "") },
    el("div", { className: "l", textContent: l }),
    el("div", { className: "v", textContent: v }),
    el("div", { className: "l", textContent: s }))));
}

/* ---------- Choropleth: one-hue sequential ramp (blue 100 -> 700), flipped anchor in dark ---------- */
function ramp() {
  const stops = ["--seq100", "--seq250", "--seq400", "--seq550", "--seq700"].map(css);
  if (isDark()) stops.reverse(); // dark: low values recede toward the dark surface
  return d3.interpolateRgbBasis(stops);
}

function buildMap() {
  const w = 620, h = 640;
  const svg = d3.select("#map").append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("role", "img")
    .attr("aria-label", "Choropleth map of NYC taxi zones");
  const proj = d3.geoMercator().fitSize([w, h], zones), path = d3.geoPath(proj);
  // The SVG can be wider than its viewBox, so the water extends well past it.
  svg.append("rect").attr("class", "water").attr("x", -w).attr("y", -h).attr("width", 3 * w).attr("height", 3 * h);
  const layer = svg.append("g");
  layer.append("g").attr("aria-hidden", "true").selectAll("path").data(basemap.features).join("path").attr("class", "land").attr("d", path);
  const zoneLayer = layer.append("g");
  const labels = layer.append("g").attr("class", "places").attr("aria-hidden", "true").selectAll("text").data(PLACES).join("text")
    .attr("class", (d) => d[3]).attr("transform", (d) => `translate(${proj([d[1], d[2]])}) rotate(${d[4]})`).text((d) => d[0]);
  const sizeLabels = (k) => labels.attr("font-size", (d) => (d[3] === "land" ? 12 : 11) / k);
  sizeLabels(1);
  paths = zoneLayer.selectAll("path").data(zones.features).join("path").attr("d", path)
    .attr("tabindex", 0)
    .on("pointermove", (e, d) => showTip(e, d.properties.LocationID))
    .on("pointerleave", hideTip).on("blur", hideTip)
    .on("focus", (e, d) => showTip({ clientX: e.target.getBoundingClientRect().x, clientY: e.target.getBoundingClientRect().y }, d.properties.LocationID))
    .on("click", (e, d) => selectZone(d.properties.LocationID))
    .on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectZone(d.properties.LocationID); } });

  // Drag to pan; Ctrl+scroll / trackpad pinch / two-finger touch to zoom. Plain scroll keeps scrolling the page.
  const zoom = d3.zoom().scaleExtent([1, 12]).translateExtent([[0, 0], [w, h]]).clickDistance(4)
    .filter((e) => {
      if (e.type === "wheel") return e.ctrlKey;
      if (e.type.startsWith("touch")) return e.touches.length > 1;
      return !e.button;
    })
    .on("start", (e) => { hideTip(); if (e.sourceEvent?.type === "mousedown") svg.classed("dragging", true); })
    .on("zoom", (e) => { layer.attr("transform", e.transform); sizeLabels(e.transform.k); })
    .on("end", () => svg.classed("dragging", false));
  svg.call(zoom).on("dblclick.zoom", null).style("touch-action", "pan-x pan-y");

  const ctl = (label, title, fn) => el("button", { type: "button", textContent: label, title, ariaLabel: title,
    onclick: () => svg.transition().duration(250).call(fn) });
  $("map").append(el("div", { className: "mapctl" },
    ctl("+", "Zoom in", (t) => zoom.scaleBy(t, 1.6)),
    ctl("−", "Zoom out", (t) => zoom.scaleBy(t, 1 / 1.6)),
    ctl("Reset", "Show the whole city", (t) => zoom.transform(t, d3.zoomIdentity))));
}

function renderMap() {
  const m = METRICS[state.metric], rows = rowOf(), interp = ramp();
  const odMode = state.metric === "od_prob", probs = odMode ? odProbs() : null;
  $("mapTitle").textContent = odMode ? (state.zone ? `Origin–destination probability: where trips from ${zname(state.zone)} end`
    : "Origin–destination probability") : `${m.label} by zone`;
  $("mapNote").textContent = (odMode && !state.zone ? "Click a pickup zone to colour every zone by the share of its trips that end there. "
    : "Click a zone to select it. ") + "Drag to move the map; zoom with Ctrl + scroll, a pinch or the buttons.";
  const vals = odMode ? [...(probs?.values() ?? [])]
    : summary.zones.filter((z) => z.eligible && z[state.metric] != null).map((z) => z[state.metric]);
  const q = vals.length ? d3.scaleQuantile().domain(vals).range(d3.range(7).map((i) => interp(i / 6))) : null;
  const val = (d) => {
    const id = d.properties.LocationID;
    if (odMode) return probs?.get(id) ?? null;
    const r = rows.get(id); return r?.eligible && r[state.metric] != null ? r[state.metric] : null;
  };
  paths.style("fill", (d) => { const v = val(d); return v == null || !q ? css("--none") : q(v); })
    .classed("pareto", (d) => !odMode && !!rows.get(d.properties.LocationID)?.pareto)
    .classed("sel", (d) => d.properties.LocationID === state.zone);
  paths.filter(".pareto").raise(); paths.filter(".sel").raise();
  const lg = $("legend");
  if (!vals.length) {
    lg.textContent = odMode ? (state.zone ? "No trips left the selected zone in this period." : "Select a pickup zone to see where its trips end.")
      : "No zones meet the minimum trip count.";
    return;
  }
  // Stepped legend: one block per map class, labelled at the class boundaries.
  const colors = q.range(), cuts = [d3.min(vals), ...q.quantiles(), d3.max(vals)];
  lg.replaceChildren(
    el("div", { className: "steps" }, ...colors.map((c, i) => el("i", { style: `background:${c}`, title: `${m.fmt(cuts[i])} to ${m.fmt(cuts[i + 1])}` }))),
    el("div", { className: "stepl" }, ...cuts.map((v, i) => el("span", { textContent: m.fmt(v), style: `left:${(100 * i) / colors.length}%` }))),
    el("div", { textContent: odMode
      ? `% of the ${od.total.toLocaleString()} selected trips from ${zname(state.zone)} that end in each zone. Each shade holds an equal share of destinations. Grey: no trips.`
      : `Each shade holds an equal share of zones. Grey: under ${state.minTrips} trips. Outlined: Pareto-efficient zone.`, style: "margin-top:4px" }));
}

/* ---------- Tooltip (values lead, labels follow) ---------- */
function tipRows(title, sub, rows) {
  const tip = $("tip");
  tip.replaceChildren(el("div", {}, el("b", { textContent: title }), document.createTextNode(sub ? ` · ${sub}` : "")),
    ...rows.map(([l, v]) => el("div", {}, el("span", { className: "v", textContent: v }), document.createTextNode(" " + l))));
  tip.hidden = false;
}
function moveTip(e) {
  const tip = $("tip");
  tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8) + "px";
}
const hideTip = () => ($("tip").hidden = true);
function showTip(e, id) {
  const p = byId.get(id), r = rowOf().get(id), f = (k, v) => (v == null ? "–" : METRICS[k].fmt(v));
  const rows = r ? [[`pickups/day`, f("pickups_per_day", r.pickups_per_day)], [`fare+tip−tolls per min`, f("rate", r.rate)],
    [`min avg duration`, f("avg_duration", r.avg_duration)], [`% good tips, card, pickups here`, f("tip_rate", r.tip_rate)],
    [`% good tips, card, dropoffs here`, f("do_tip_rate", r.do_tip_rate)], [`% avg card tip of fare`, f("card_tip_pct", r.card_tip_pct)],
    [`dropoffs/day`, f("dropoffs_per_day", r.dropoffs_per_day)], [`pickups per dropoff`, f("pickups_per_dropoff", r.pickups_per_dropoff)]] : [["trips", "0"]];
  const p_od = odProbs();
  if (p_od) rows.unshift([`% of trips from ${zname(state.zone)} end here (OD probability)`, f("od_prob", p_od.get(id) ?? 0)]);
  if (r?.attractiveness != null) rows.push(["attractiveness", f("attractiveness", r.attractiveness)]);
  if (r?.pareto) rows.push(["", "Pareto-efficient"]);
  tipRows(p?.zone ?? `Zone ${id}`, p?.borough, rows); moveTip(e);
}

/* ---------- Scatter (Observable Plot): emphasis form, Pareto frontier, nearest-point hit layer ---------- */
function renderScatter() {
  const all = summary.zones.filter((z) => z.eligible && z.rate != null)
    .map((z) => ({ ...z, name: zname(z.zone), thin: z.pickups_per_day < THIN }));
  if (!all.length) { $("scatter").replaceChildren(el("p", { className: "empty", textContent: "No zone has enough trips for this selection." })); return; }
  // y axis covers every well-sampled zone plus the 1st-99th percentile of the rest; outliers are pinned to the edge as triangles.
  const solid = all.filter((d) => !d.thin).length ? all.filter((d) => !d.thin) : all;
  const rates = all.map((d) => d.rate).sort(d3.ascending);
  const cap = Math.max(d3.max(solid, (d) => d.rate), d3.quantileSorted(rates, 0.99)) * 1.03;
  const lo = Math.min(d3.min(solid, (d) => d.rate), d3.quantileSorted(rates, 0.01)) * 0.97;
  const data = all.map((d) => ({ ...d, y: Math.max(lo, Math.min(d.rate, cap)), over: d.rate > cap, under: d.rate < lo }));
  const inside = data.filter((d) => !d.over && !d.under), over = data.filter((d) => d.over), under = data.filter((d) => d.under);
  const others = inside.filter((d) => !d.pareto), front = inside.filter((d) => d.pareto);
  const frontier = data.filter((d) => d.pareto).sort((a, b) => a.pickups_per_day - b.pickups_per_day);
  const sel = data.filter((d) => d.zone === state.zone);
  const s1 = css("--s1"), surface = css("--surface"), ink = css("--ink"), ink2 = css("--ink2");
  const XY = { x: "pickups_per_day", y: "y" };
  const plot = Plot.plot({
    width: 620, height: 520, marginLeft: 52, marginBottom: 40, marginTop: 34,
    style: { background: "transparent", color: ink2, fontSize: "12px", fontFamily: "Archivo, system-ui, sans-serif" },
    x: { label: "Pickups per day (log scale) →", type: "log", ticks: [10, 100, 1000, 10000], tickFormat: "~s" },
    y: { label: "↑ Fare + tip − tolls per occupied minute ($)", tickFormat: "$.2f", domain: [lo, cap], nice: true },
    marks: [
      Plot.gridX({ ticks: [10, 100, 1000, 10000], stroke: css("--grid"), strokeOpacity: 1 }), Plot.gridY({ stroke: css("--grid"), strokeOpacity: 1 }),
      Plot.dot(others.filter((d) => !d.thin), { ...XY, r: 4, fill: s1, fillOpacity: 0.35 }),
      Plot.dot(others.filter((d) => d.thin), { ...XY, r: 3.5, fill: "none", stroke: s1, strokeOpacity: 0.6 }),
      Plot.line(frontier, { ...XY, curve: "step-before", stroke: s1, strokeWidth: 1.5 }),
      Plot.dot(front.filter((d) => !d.thin), { ...XY, r: 5, fill: s1, stroke: surface, strokeWidth: 2 }),
      Plot.dot(front.filter((d) => d.thin), { ...XY, r: 4.5, fill: surface, stroke: s1, strokeWidth: 2 }),
      Plot.dot(over, { ...XY, symbol: "triangle", r: 5, fill: surface, stroke: s1, strokeWidth: 1.5 }),
      Plot.dot(under, { ...XY, symbol: "triangle", rotate: 180, r: 5, fill: surface, stroke: s1, strokeWidth: 1.5 }),
      Plot.dot(sel, { ...XY, r: 9, stroke: css("--s2"), strokeWidth: 2.5, fill: "none" }),
    ],
  });
  $("scatter").replaceChildren(plot);
  const xs = plot.scale("x"), ys = plot.scale("y");
  const pts = data.map((d) => [xs.apply(d.pickups_per_day), ys.apply(d.y)]);

  // Labels, placed greedily in priority order; a label that would collide or leave the plot is skipped.
  const W = plot.width.baseVal.value, H = plot.height.baseVal.value, svg = d3.select(plot);
  const hit = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  const placed = sel.map((d) => ({ x: xs.apply(d.pickups_per_day) - 11, y: ys.apply(d.y) - 11, width: 22, height: 22 }));
  const reliable = frontier.filter((d) => !d.thin);
  const cands = [...sel, ...[...reliable].sort((a, b) => b.rate - a.rate).slice(0, 2),
    ...[...reliable].sort((a, b) => b.pickups_per_day - a.pickups_per_day).slice(0, 1), ...[...over].sort((a, b) => b.rate - a.rate).slice(0, 1)];
  const done = new Set();
  for (const d of cands) {
    if (done.has(d.zone)) continue; done.add(d.zone);
    const px = xs.apply(d.pickups_per_day), py = ys.apply(d.y), right = px > W * 0.6;
    for (const dy of [-12, 18]) {
      const t = svg.append("text").attr("x", px + (right ? -12 : 12)).attr("y", py + dy).attr("text-anchor", right ? "end" : "start")
        .attr("font-size", 11).attr("fill", ink).attr("stroke", surface).attr("stroke-width", 3).attr("paint-order", "stroke")
        .attr("pointer-events", "none").text(d.over ? `${d.name} ${METRICS.rate.fmt(d.rate)} ▲` : d.name);
      const b = t.node().getBBox();
      if (b.x >= 0 && b.y >= 0 && b.x + b.width <= W && b.y + b.height <= H && !placed.some((p) => hit(p, b))) { placed.push(b); break; }
      t.remove();
    }
  }

  // Nearest-point layer: the pointer only has to be closest, not dead-centre.
  const delaunay = d3.Delaunay.from(pts);
  const ring = d3.select(plot).append("circle").attr("r", 8).attr("fill", "none").attr("stroke", ink).attr("stroke-width", 1.5).attr("display", "none");
  const locate = (e) => {
    const b = plot.getBoundingClientRect(), k = plot.width.baseVal.value / b.width;
    const x = (e.clientX - b.left) * k, y = (e.clientY - b.top) * k, i = delaunay.find(x, y);
    return Math.hypot(pts[i][0] - x, pts[i][1] - y) < 28 ? i : -1; // 28px hit radius
  };
  plot.addEventListener("pointermove", (e) => {
    const i = locate(e);
    if (i < 0) { ring.attr("display", "none"); hideTip(); plot.style.cursor = ""; return; }
    const d = data[i]; plot.style.cursor = "pointer";
    ring.attr("display", null).attr("cx", pts[i][0]).attr("cy", pts[i][1]);
    tipRows(d.name, byId.get(d.zone)?.borough, [["pickups/day", METRICS.pickups_per_day.fmt(d.pickups_per_day)],
      ["fare+tip−tolls per min", METRICS.rate.fmt(d.rate)], ...(d.pareto ? [["", "Pareto-efficient"]] : []),
      ...(d.thin ? [["", "Few pickups: small sample"]] : [])]);
    moveTip(e);
  });
  plot.addEventListener("pointerleave", () => { ring.attr("display", "none"); hideTip(); });
  plot.addEventListener("click", (e) => { const i = locate(e); if (i >= 0) selectZone(data[i].zone); });
}

function renderPicks() {
  // Balanced order (attractiveness) so thin, high-rate zones don't top the list.
  const list = summary.zones.filter((z) => z.pareto && z.eligible).sort((a, b) => b.attractiveness - a.attractiveness).slice(0, 10);
  if (!list.length) { $("picks").replaceChildren(el("li", { className: "empty", textContent: "No zone has enough trips for this selection. Lower the minimum trips or widen the dates and hours." })); return; }
  $("picks").replaceChildren(...list.map((z) => el("li", { className: z.zone === state.zone ? "sel" : "" },
    el("button", { type: "button", onclick: () => selectZone(z.zone), ariaPressed: String(z.zone === state.zone) },
      el("span", { className: "n", textContent: zname(z.zone) }),
      el("span", { className: "r", textContent: METRICS.rate.fmt(z.rate) + " per min" }),
      el("span", { className: "b", textContent: byId.get(z.zone)?.borough ?? "" }),
      el("span", { className: "p", textContent: METRICS.pickups_per_day.fmt(z.pickups_per_day) + " pickups/day" }),
      ...(z.pickups_per_day < THIN ? [el("span", { className: "warn", textContent: "Few pickups: the rate rests on a small sample" })] : [])))));
}

function renderDetail() {
  const host = $("detail");
  if (!state.zone) {
    host.replaceChildren(el("h2", { textContent: "Zone detail" }),
      el("p", { className: "empty", textContent: "Select a zone on the map, the scatter plot or the ranked list to see its origin–destination probabilities (where its trips end) and to compare it with another zone." }));
    return;
  }
  const kids = [el("div", { className: "dhead" },
    el("h2", { textContent: `${zname(state.zone)}, ${byId.get(state.zone)?.borough ?? ""}` }),
    el("button", { type: "button", textContent: "Clear selection", onclick: () => selectZone(null) }))];
  if (od?.error) {
    kids.push(el("p", { className: "empty", textContent: `Destinations couldn't load (${od.error}). If the server was started before this feature was added, restart it.` }));
  } else if (od && od.total) {
    kids.push(el("h3", { className: "odh", textContent: "Origin–destination probability" }),
      el("p", { className: "note", textContent: `Share of the ${od.total.toLocaleString()} selected trips from here that end in each zone (top 10). To see every destination, colour the map by origin–destination probability.` }));
    const max = Math.max(...od.top.map((d) => d.prob));
    const rows = od.top.map((d) => el("li", {}, el("span", { className: "nm", textContent: zname(d.zone), title: zname(d.zone) }),
      el("span", { className: "bar" }, Object.assign(el("i"), { style: `width:${(100 * d.prob) / max}%` })),
      el("span", { className: "pc", textContent: d3.format(".1%")(d.prob) })));
    if (od.other > 0.001) rows.push(el("li", {}, el("span", { className: "nm", textContent: "All other zones" }), el("span", { className: "bar" }), el("span", { className: "pc", textContent: d3.format(".1%")(od.other) })));
    kids.push(el("ul", { className: "dest" }, ...rows));
  } else kids.push(el("p", { className: "empty", textContent: "No trips left this zone in the selected period." }));

  // compare with another zone
  const opts = summary.zones.filter((z) => z.eligible && z.zone !== state.zone).sort((a, b) => zname(a.zone).localeCompare(zname(b.zone)));
  const sel = el("select", { id: "cmpSel", onchange: (e) => { state.compare = e.target.value ? +e.target.value : null; renderDetail(); $("cmpSel").focus(); } },
    new Option("Choose a zone", ""), ...opts.map((z) => new Option(zname(z.zone), z.zone)));
  sel.value = state.compare ?? "";
  const cmp = el("div", { className: "cmp" }, el("label", {}, "Compare with", sel));
  const A = rowOf().get(state.zone), B = state.compare ? rowOf().get(state.compare) : null;
  if (A && B) {
    const defs = [["Pickups per day", "pickups_per_day", 1], ["Fare + tip − tolls per min", "rate", 1], ["Avg duration (min)", "avg_duration", 0],
      ["Card tips over 20%, pickups (%)", "tip_rate", 1], ["Card tips over 20%, dropoffs (%)", "do_tip_rate", 1], ["Avg card tip (% of fare)", "card_tip_pct", 1],
      ["Dropoffs per day", "dropoffs_per_day", 0], ["Pickups per dropoff", "pickups_per_dropoff", 1]];
    const cell = (k, v, win) => el("td", { className: win ? "win" : "", textContent: v == null ? "–" : METRICS[k].fmt(v) });
    cmp.append(el("table", {}, el("thead", {}, el("tr", {}, el("th"), el("th", { textContent: zname(state.zone) }), el("th", { textContent: zname(state.compare) }))),
      el("tbody", {}, ...defs.map(([l, k, higher]) => {
        const a = A[k], b = B[k], both = a != null && b != null && a !== b;
        return el("tr", {}, el("td", { textContent: l }), cell(k, a, higher && both && a > b), cell(k, b, higher && both && b > a));
      }))), el("p", { className: "note", textContent: "A check mark shows the higher value. Trip duration and dropoffs are not marked: more or less is neither better nor worse." }));
  }
  kids.push(cmp);
  host.replaceChildren(...kids);
}

function renderTable() {
  const rows = summary.zones.filter((z) => z.attractiveness != null).sort((a, b) => b.attractiveness - a.attractiveness).slice(0, 12);
  const th = (t) => el("th", { textContent: t });
  $("table").replaceChildren(el("table", {},
    el("thead", {}, el("tr", {}, th("Zone"), th("Attract."), th("Pickups/day"), th("$/min"), th("Min"), th("Pick/drop"), th("Pareto"))),
    el("tbody", {}, ...rows.map((r) => el("tr", {},
      ...[zname(r.zone), METRICS.attractiveness.fmt(r.attractiveness), METRICS.pickups_per_day.fmt(r.pickups_per_day),
        METRICS.rate.fmt(r.rate), METRICS.avg_duration.fmt(r.avg_duration),
        r.pickups_per_dropoff == null ? "–" : METRICS.pickups_per_dropoff.fmt(r.pickups_per_dropoff), r.pareto ? "yes" : ""]
        .map((t) => el("td", { textContent: t })))))));
}

/* ---------- Vega-Lite: single-axis charts, hairline grid, nearest-x crosshair tooltips ---------- */
function vegaConfig() {
  const ink2 = css("--ink2"), grid = css("--grid"), axis = css("--axis"), muted = css("--muted");
  return { background: "transparent", view: { stroke: null }, font: "Archivo, system-ui, 'Segoe UI', sans-serif",
    axis: { labelColor: muted, titleColor: ink2, domainColor: axis, tickColor: axis, gridColor: grid, gridDash: [], gridWidth: 1,
      labelFontSize: 11, titleFontSize: 11, titleFontWeight: "normal" } };
}
const cw = () => Math.max(240, ($("vRate").clientWidth || 360) - 60);
/* x = time of day in hours (slot / 4); each point sits at the start of its 15-minute window */
const HX = { field: "t", type: "quantitative", title: "Time of day", scale: { domain: [0, 24], nice: false },
  axis: { values: [0, 3, 6, 9, 12, 15, 18, 21, 24], labelExpr: "datum.value + ':00'", labelOverlap: "greedy", labelFlush: true, grid: false } };
const winLayer = (color) => ({ data: { values: state.s0 === 0 && state.s1 === SLOTS - 1 ? [] : [{ a: state.s0 / 4, b: (state.s1 + 1) / 4 }] },
  mark: { type: "rect", color, opacity: 0.08 }, encoding: { x: { field: "a", type: "quantitative", scale: { domain: [0, 24] } }, x2: { field: "b" } } });

function lineChart(host, values, yField, yTitle, color, fmt, extra = {}) {
  const win = winLayer(color);
  const y = { field: yField, type: "quantitative", title: yTitle, scale: { zero: false } };
  const hit = { name: "hov", select: { type: "point", fields: ["t"], nearest: true, on: "pointerover", clear: "pointerout" } };
  return vegaEmbed(host, {
    width: cw(), height: 220, data: { values },
    layer: [win,
      { mark: { type: "line", color, strokeWidth: 2 }, encoding: { x: HX, y } },
      { mark: { type: "point", size: 300, opacity: 0 }, params: [hit], encoding: { x: HX, y } },
      { mark: { type: "rule", color: css("--axis") }, encoding: { x: HX,
        opacity: { condition: { param: "hov", empty: false, value: 1 }, value: 0 },
        tooltip: [{ field: "time", title: "From" }, { field: yField, title: yTitle, format: fmt }] } },
      { mark: { type: "point", filled: true, size: 70, color, stroke: css("--surface"), strokeWidth: 2 },
        encoding: { x: HX, y, opacity: { condition: { param: "hov", empty: false, value: 1 }, value: 0 } } }],
    ...extra,
  }, { actions: false, config: vegaConfig(), renderer: "svg", tooltip: { theme: isDark() ? "dark" : "light" } });
}

function renderVega() {
  const s1 = css("--s1"), s2 = css("--s2");
  const H = series.by_slot.map((d) => ({ ...d, t: d.slot / 4, time: slotLabel(d.slot / 4) }));
  lineChart("#vRate", H, "rate", "$ per occupied minute", s1, "$.2f");
  lineChart("#vDur", H, "avg_duration", "Minutes", s1, ".1f");

  // Pickups and dropoffs share one unit (trips per day), so one axis, two categorical slots.
  const NP = "Pickups", ND = "Dropoffs";
  const pd = H.flatMap((d) => [{ t: d.t, time: d.time, series: NP, v: d.pickups_per_day }, { t: d.t, time: d.time, series: ND, v: d.dropoffs_per_day }]);
  $("flowKey").replaceChildren(el("span", { className: "key" }, el("i", { style: `background:${s1}` }), NP),
    el("span", { className: "key" }, el("i", { style: `background:${s2}` }), ND),
    el("div", { textContent: "Trips per selected day starting or ending in each 15-minute window." }));
  const pcol = { field: "series", type: "nominal", legend: null, scale: { domain: [NP, ND], range: [s1, s2] } };
  const py = { field: "v", type: "quantitative", title: "Trips per day" };
  const phit = { name: "hovF", select: { type: "point", fields: ["t"], nearest: true, on: "pointerover", clear: "pointerout" } };
  vegaEmbed("#vFlow", {
    width: cw(), height: 220, data: { values: pd },
    layer: [winLayer(s1),
      { mark: { type: "line", strokeWidth: 2 }, encoding: { x: HX, y: py, color: pcol } },
      { mark: { type: "point", size: 300, opacity: 0 }, params: [phit], encoding: { x: HX, y: py, color: pcol } },
      { mark: { type: "rule", color: css("--axis") }, encoding: { x: HX, opacity: { condition: { param: "hovF", empty: false, value: 1 }, value: 0 } } },
      { mark: { type: "point", filled: true, size: 70, stroke: css("--surface"), strokeWidth: 2 },
        encoding: { x: HX, y: py, color: pcol, opacity: { condition: { param: "hovF", empty: false, value: 1 }, value: 0 },
          tooltip: [{ field: "series", title: "Series" }, { field: "time", title: "From" }, { field: "v", title: "Per day", format: ",.1f" }] } }],
  }, { actions: false, config: vegaConfig(), renderer: "svg", tooltip: { theme: isDark() ? "dark" : "light" } });

  vegaEmbed("#vWeekday", {
    width: cw(), height: 220,
    data: { values: series.by_weekday.map((d) => ({ ...d, day: DAYS[d.weekday], on: state.weekdays.has(d.weekday) })) },
    mark: { type: "bar", color: s1, cornerRadiusTopLeft: 4, cornerRadiusTopRight: 4, width: { band: 0.6 } },
    encoding: { x: { field: "day", type: "ordinal", sort: DAYS, title: null, axis: { labelAngle: 0, grid: false } },
      y: { field: "pickups_per_day", type: "quantitative", title: "Pickups per day" },
      opacity: { condition: { test: "datum.on", value: 1 }, value: 0.3 },
      tooltip: [{ field: "day", title: "Day" }, { field: "pickups_per_day", title: "Pickups/day", format: ",.0f" },
        { field: "rate", title: "$ per min", format: "$.2f" }, { field: "avg_duration", title: "Avg min", format: ".1f" }] },
  }, { actions: false, config: vegaConfig(), renderer: "svg" });

  // Indexed to each series' own daily mean (=100) so both share ONE axis; no dual axis.
  // Both series are per 15-minute window. When the selection holds traffic days, the rate line is recomputed
  // over exactly those days (traffic.rate_by_slot), so both lines describe the same days (see trafficNote).
  const tr = series.traffic, T = tr.by_slot.filter((d) => d.speed != null);
  const R = (tr.matched ? tr.rate_by_slot : series.by_slot).filter((d) => d.rate != null);
  const mean = (a) => d3.mean(a);
  const mr = mean(R.map((d) => d.rate)), ms = mean(T.map((d) => d.speed));
  const NR = "Fare + tip − tolls per min", NS = tr.borough ? `Traffic speed (${tr.borough})` : "Traffic speed";
  const idx = [];
  R.forEach((d) => idx.push({ t: d.slot / 4, time: slotLabel(d.slot / 4), series: NR, index: (100 * d.rate) / mr,
    value: `$${d.rate.toFixed(2)} per min` }));
  T.forEach((d) => idx.push({ t: d.slot / 4, time: slotLabel(d.slot / 4), series: NS, index: (100 * d.speed) / ms,
    value: `${d.speed.toFixed(1)} mph` }));
  const plural = (n) => `${n} day${n === 1 ? "" : "s"}`;
  const span = tr.n_days ? `${plural(tr.n_days)}, ${tr.first} to ${tr.last}` : "no days";
  const links = tr.borough ? `${tr.borough} road links only` : "road links in all boroughs";
  $("trafficNote").textContent = tr.matched
    ? `Both lines cover the same ${span}: the selected days that also have NYC DOT speed data`
      + (tr.n_days < tr.n_taxi_days ? ` (${tr.n_days} of ${tr.n_taxi_days} selected days; traffic data starts 2026-08-01)` : "")
      + `. Traffic speed is the mean over ${links}.`
    : `None of the selected days has traffic data (it starts 2026-08-01), so the traffic line uses all ${span} of traffic`
      + ` on the selected weekdays, ${links}. The earning rate covers the selected days. This compares daily shapes, not the same days.`;
  $("idxKey").replaceChildren(el("span", { className: "key" }, el("i", { style: `background:${s1}` }), NR),
    el("span", { className: "key" }, el("i", { style: `background:${s2}` }), NS),
    el("div", { textContent: "Each series = 100 at its own daily average." }));
  const col = { field: "series", type: "nominal", legend: null, scale: { domain: [NR, NS], range: [s1, s2] } };
  const y = { field: "index", type: "quantitative", title: "Index (daily avg = 100)", scale: { zero: false } };
  const hit = { name: "hovT", select: { type: "point", fields: ["t"], nearest: true, on: "pointerover", clear: "pointerout" } };
  vegaEmbed("#vTraffic", {
    width: cw(), height: 220, data: { values: idx },
    layer: [
      { mark: { type: "line", strokeWidth: 2 }, encoding: { x: HX, y, color: col } },
      { mark: { type: "point", size: 300, opacity: 0 }, params: [hit], encoding: { x: HX, y, color: col } },
      { mark: { type: "rule", color: css("--axis") }, encoding: { x: HX,
        opacity: { condition: { param: "hovT", empty: false, value: 1 }, value: 0 } } },
      { mark: { type: "point", filled: true, size: 70, stroke: css("--surface"), strokeWidth: 2 },
        encoding: { x: HX, y, color: col, opacity: { condition: { param: "hovT", empty: false, value: 1 }, value: 0 },
          tooltip: [{ field: "series", title: "Series" }, { field: "time", title: "From" }, { field: "index", title: "Index", format: ".0f" },
            { field: "value", title: "Value" }] } },
    ],
  }, { actions: false, config: vegaConfig(), renderer: "svg", tooltip: { theme: isDark() ? "dark" : "light" } });
}

/* ---- Data panel: pick TLC months and DOT traffic days, then load them (backend/datajobs.py) ---- */
const STAGES = [["check", "Check traffic days with NYC DOT"], ["fetch", "Download and clean"],
  ["merge", "Merge with the loaded data"], ["rebuild", "Rebuild the dashboard's aggregates"]];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dp = { status: null, available: null, months: new Set(), timer: null };
const addDaysISO = (iso, n) => new Date(Date.parse(iso) + n * 864e5).toISOString().slice(0, 10);
const daysBetween = (a, b) => (a && b && a <= b ? Math.round((Date.parse(b) - Date.parse(a)) / 864e5) + 1 : 0);
const fmtSecs = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const monthLabel = (m) => `${MONTHS[+m.slice(5) - 1]} ${m.slice(0, 4)}`;
const spanText = (ms) => (ms.length ? (ms.length === 1 ? monthLabel(ms[0]) : `${monthLabel(ms[0])} – ${monthLabel(ms[ms.length - 1])}`) : "none");

async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${url}: ${r.status}`);
  return j;
}

function initDataPanel() {
  $("dataBtn").onclick = openDataPanel;
  $("trFrom").onchange = $("trTo").onchange = renderEstimate;
  $("dataGo").onclick = startDataJob;
  $("dataCancel").onclick = () => postJSON("/api/data/cancel").catch(() => {});
  // A load may already be running (started before a page reload): pick it up.
  getJSON("/api/data/status").then((s) => { dp.status = s; if (s.job.state === "running") pollJob(); }).catch(() => {});
  if (new URLSearchParams(location.search).get("panel") === "data") openDataPanel(); // deep link
}

async function openDataPanel() {
  $("dataDlg").showModal();
  try {
    dp.status = await getJSON("/api/data/status");
    const job = dp.status.job;
    if (job.state !== "running") resetSelection();
    else { // show what the running load asked for
      dp.months = new Set(job.months);
      $("trFrom").value = job.traffic_days[0] || ""; $("trTo").value = job.traffic_days.at(-1) || "";
    }
    renderDataPanel();
    if (!dp.available) {
      $("dataMonths").replaceChildren(el("p", { className: "note", textContent: "Checking which months TLC has published…" }));
      dp.available = (await getJSON("/api/data/available")).months;
      renderDataPanel();
    }
  } catch (e) {
    $("dataEst").textContent = `Couldn't reach the server (${e.message}).`;
  }
}

/* Selection starts as what is loaded, so ticking adds and unticking removes. */
function resetSelection() {
  const L = dp.status.loaded;
  dp.months = new Set(L.months);
  $("trFrom").value = L.traffic_dates[0] || ""; $("trTo").value = L.traffic_dates.at(-1) || "";
}

function renderDataPanel() {
  const s = dp.status, L = s.loaded;
  $("trFrom").max = $("trTo").max = s.max_traffic_day;
  const gaps = L.traffic_missing.length ? ` (no DOT data: ${L.traffic_missing.join(", ")})` : "";
  $("dataLoaded").replaceChildren(
    el("b", { textContent: "Loaded now. " }),
    `Taxi: ${spanText(L.months)}, ${L.trips.toLocaleString()} trips. `,
    `Traffic: ${L.traffic_dates.length ? `${L.traffic_dates[0]} to ${L.traffic_dates.at(-1)}, ${L.traffic_dates.length} days${gaps}` : "none"}.`);
  // Month grid: one row per year; a month can be picked when TLC publishes it or a copy is cached here.
  const cached = new Set(s.cached_months), loaded = new Set(L.months);
  const pickable = new Set([...(dp.available || []), ...cached, ...loaded]);
  const years = [...new Set([...pickable].map((m) => m.slice(0, 4)))].sort();
  const running = s.job.state === "running";
  $("dataMonths").replaceChildren(...years.map((y) => el("div", { className: "mrow" },
    el("span", { className: "yr", textContent: y }),
    el("div", { className: "chips" }, ...MONTHS.map((name, i) => {
      const m = `${y}-${String(i + 1).padStart(2, "0")}`, on = dp.months.has(m);
      const b = el("button", { type: "button", textContent: name, disabled: running || !pickable.has(m) });
      b.classList.toggle("on", on); b.ariaPressed = String(on);
      if (cached.has(m)) b.classList.add("cached");
      b.title = !pickable.has(m) ? "Not published by TLC yet" : loaded.has(m) ? "Loaded" : cached.has(m) ? "Downloaded before (no download needed)" : "Will be downloaded";
      b.onclick = () => { on ? dp.months.delete(m) : dp.months.add(m); renderDataPanel(); };
      return b;
    })))));
  $("trFrom").disabled = $("trTo").disabled = running;
  $("trPresets").replaceChildren(...[
    ["Loaded range", () => { $("trFrom").value = L.traffic_dates[0] || ""; $("trTo").value = L.traffic_dates.at(-1) || ""; }],
    ["Same as taxi months", () => { const ms = [...dp.months].sort(); if (!ms.length) return;
      const last = new Date(Date.UTC(+ms.at(-1).slice(0, 4), +ms.at(-1).slice(5), 0)).toISOString().slice(0, 10);
      $("trFrom").value = `${ms[0]}-01`; $("trTo").value = last < s.max_traffic_day ? last : s.max_traffic_day; }],
    ["Up to yesterday", () => { $("trTo").value = s.max_traffic_day; if (!$("trFrom").value) $("trFrom").value = addDaysISO(s.max_traffic_day, -29); }],
  ].map(([t, f]) => el("button", { type: "button", textContent: t, disabled: running, onclick: () => { f(); renderEstimate(); } })));
  renderEstimate();
  renderJob(s.job);
}

/* What the selected load would do, and whether it can start. */
function renderEstimate() {
  const s = dp.status, L = s.loaded, sel = [...dp.months].sort();
  const loaded = new Set(L.months), cached = new Set(s.cached_months), cachedDays = new Set(s.cached_traffic_days);
  const add = sel.filter((m) => !loaded.has(m)), drop = L.months.filter((m) => !dp.months.has(m));
  const dl = add.filter((m) => !cached.has(m));
  const a = $("trFrom").value, b = $("trTo").value, n = daysBetween(a, b);
  let newDays = 0; for (let i = 0; i < n; i++) if (!cachedDays.has(addDaysISO(a, i))) newDays++;
  const sameTraffic = a === L.traffic_dates[0] && b === L.traffic_dates.at(-1);
  const mb = dl.length * s.mb_per_month * 2; // raw + cleaned
  const parts = [], warn = [];
  if (add.length) parts.push(`add ${add.length} month${add.length > 1 ? "s" : ""} (${dl.length ? `${dl.length} to download, about ${mb} MB` : "all cached"})`);
  if (drop.length) parts.push(`remove ${drop.map(monthLabel).join(", ")} (raw files stay cached)`);
  if (!sameTraffic && n) parts.push(`traffic ${a} to ${b}: ${n} days, ${newDays} to download`);
  if (!sel.length) warn.push("Select at least one taxi month.");
  if (!n) warn.push("Pick a valid traffic range (from ≤ to).");
  if (n > 400) warn.push("At most 400 traffic days per load.");
  if (b > s.max_traffic_day) warn.push(`Traffic can run up to yesterday (${s.max_traffic_day}).`);
  if (s.free_gb - mb / 1000 < 2) warn.push(`Low disk space: ${s.free_gb} GB free.`);
  const changed = add.length || drop.length || !sameTraffic;
  const running = s.job.state === "running";
  if (running) {
    $("dataEst").textContent = "A load is running. Settings are locked until it finishes; you can close this panel meanwhile.";
    $("dataGo").disabled = true;
    return;
  }
  $("dataEst").replaceChildren(
    changed ? `This load will ${parts.join("; ")}. ` : "Nothing changed yet: tick months or change the traffic range. ",
    el("span", { className: "muted", textContent: `${s.free_gb} GB free. Rebuilding takes a few minutes; the dashboard keeps showing the current data until it is done.` }),
    ...warn.map((w) => el("span", { className: "warn", textContent: ` ${w}` })));
  $("dataGo").disabled = running || !changed || !sel.length || !n || n > 400 || b > s.max_traffic_day;
}

async function startDataJob() {
  $("dataGo").disabled = true;
  try {
    await postJSON("/api/data/fetch", { months: [...dp.months].sort(), traffic_from: $("trFrom").value, traffic_to: $("trTo").value });
    pollJob();
  } catch (e) {
    $("dataEst").replaceChildren(el("span", { className: "warn", textContent: e.message }));
    $("dataGo").disabled = false;
  }
}

function pollJob() {
  clearTimeout(dp.timer);
  const tick = async () => {
    try { dp.status = await getJSON("/api/data/status"); } catch { dp.timer = setTimeout(tick, 4000); return; }
    const job = dp.status.job;
    if ($("dataDlg").open) renderDataPanel(); else renderJob(job);
    if (job.state === "running") { dp.timer = setTimeout(tick, 2000); return; }
    if (job.state === "done") { // new data is live on the server: reload meta and every chart
      meta = await getJSON("/api/meta"); applyMeta(); refresh();
      resetSelection(); if ($("dataDlg").open) renderDataPanel();
    }
  };
  tick();
}

function renderJob(job) {
  const running = job.state === "running";
  $("dataBtn").textContent = running ? "Data · loading…" : "Data";
  $("dataCancel").hidden = !running || job.stage === "rebuild"; // the rebuild can't be interrupted safely
  if (!job.state || job.state === "idle") { $("dataJob").hidden = true; return; }
  $("dataJob").hidden = false;
  const at = STAGES.findIndex(([k]) => k === job.stage), p = job.progress || {};
  $("dataStages").replaceChildren(...STAGES.map(([k, label], i) => {
    const st = job.state === "done" || i < at ? "done" : i === at ? (running ? "active" : "failed") : "todo";
    let extra = "";
    if (k === "check" && job.skipped_days?.length && i <= at) extra = ` · no DOT data for ${job.skipped_days.join(", ")} (skipped)`;
    if (k === "fetch" && st === "active" && p.traffic_total != null)
      extra = ` · TLC files ${p.tlc_done ?? 0}/${p.tlc_total} · traffic days ${p.traffic_done ?? 0}/${p.traffic_total}`;
    return el("li", { className: st }, label, el("span", { className: "muted", textContent: extra }));
  }));
  $("dataMsg").textContent = running ? `Running for ${fmtSecs(job.elapsed || 0)}.`
    : job.state === "done" ? "Done. The dashboard now shows the new data."
    : job.state === "cancelled" ? "Cancelled. The previous data is still loaded."
    : `Failed: ${job.error}. The previous data is still loaded.`;
  $("dataMsg").className = job.state === "error" ? "warn" : "";
  $("dataLog").textContent = (job.log || []).join("\n");
}

init().catch((e) => document.body.prepend(el("pre", { textContent: e.message, style: "color:#d03b3b" })));
