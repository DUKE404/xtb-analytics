// Wykresy SVG pisane pod telefon: pełna szerokość, odczyt pod palcem, bez zewnętrznych bibliotek.
// Każda funkcja dostaje specyfikację z serwera (server/blocks.py) i zwraca element DOM.

import { el, fmt, fmtAxis, fmtDate, isNum, niceTicks } from "./format.js";

const NS = "http://www.w3.org/2000/svg";
const PAD = 16; // margines boczny = --card-pad; wykres idzie od krawędzi do krawędzi karty

function svg(tag, attrs = {}, ...children) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) n.setAttribute(k, v);
  for (const c of children) if (c) n.append(c);
  return n;
}

export function color(c) {
  if (typeof c === "number") return `var(--s${((c % 8) + 8) % 8})`;
  return { neutral: "var(--neutral)", pos: "var(--pos)", neg: "var(--neg)" }[c] || "var(--s0)";
}

function widthOf(host) {
  return Math.max(280, Math.round(host.getBoundingClientRect().width || host.clientWidth || 390));
}

function legend(series, box = false) {
  const ul = el("ul", { class: "legend" });
  const values = series.map((s) => {
    const b = el("b");
    ul.append(el("li", {}, el("span", { class: "key" + (box ? " box" : ""), style: `background:${color(s.color)}` }), s.name, b));
    return b;
  });
  return { ul, values };
}

function pointerX(evt, node, W) {
  const r = node.getBoundingClientRect();
  return ((evt.clientX - r.left) / r.width) * W;
}

/** Przerysowuje wykres przy zmianie szerokości (obrót telefonu). */
function responsive(host, draw) {
  let last = 0;
  const run = () => {
    const w = widthOf(host);
    if (w === last) return;
    last = w;
    host.replaceChildren(draw(w));
  };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(run).observe(host);
  requestAnimationFrame(run);
}

// --------------------------------------------------------------------------- linie / obszar

// wykresy z tą samą grupą (np. wartość portfela i obsunięcie na Przeglądzie) przybliżają się razem
const groups = new Map();

function joinGroup(name, wrap, apply) {
  if (!name) return () => {};
  if (!groups.has(name)) groups.set(name, new Set());
  const set = groups.get(name);
  const member = { wrap, apply };
  set.add(member);
  return (from, to) => {
    for (const m of [...set]) {
      if (m === member) continue;
      if (!m.wrap.isConnected) { set.delete(m); continue; }   // wykres z poprzedniego ekranu
      m.apply(from, to);
    }
  };
}

/** Pierwszy indeks z datą >= iso (daty rosnąco, RRRR-MM-DD). */
function indexFrom(xs, iso) {
  let lo = 0, hi = xs.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (xs[mid] < iso) lo = mid + 1; else hi = mid; }
  return lo;
}

/** Ostatni indeks z datą <= iso. */
function indexTo(xs, iso) {
  let lo = 0, hi = xs.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (xs[mid] > iso) hi = mid - 1; else lo = mid; }
  return lo;
}

function isoShift(iso, months) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.toISOString().slice(0, 10);
}

const PRESETS = [
  { id: "1m", label: "1M", from: (end) => isoShift(end, 1) },
  { id: "3m", label: "3M", from: (end) => isoShift(end, 3) },
  { id: "6m", label: "6M", from: (end) => isoShift(end, 6) },
  { id: "ytd", label: "YTD", from: (end) => `${end.slice(0, 4)}-01-01` },
  { id: "1y", label: "1R", from: (end) => isoShift(end, 12) },
  { id: "3y", label: "3L", from: (end) => isoShift(end, 36) },
];

const MIN_SPAN = 5;     // najmniejsze okno: tyle punktów

/** Wykres liniowy z przybliżaniem: przyciski zakresu, +/−, mini-wykres z suwakiem okna, uszczypnięcie
 *  dwoma palcami, Ctrl/⌘ + kółko lub gest na gładziku, przeciągnięcie myszą = zaznacz i przybliż,
 *  dwuklik = cały okres. `rebase`: serie liczone od 100 na początku widocznego okna. */
function lineChart(spec) {
  const wrap = el("div", { class: "line-chart" });
  const many = spec.series.length > 1;
  const lg = legend(spec.series);
  const readout = el("p", { class: "readout" });
  const summary = el("p", { class: "window-sum" });
  const body = el("div", { class: "chart-body" });
  const n = spec.x.length;
  const zoomable = spec.zoom !== false && n >= 20;
  let lo = 0, hi = n - 1;            // widoczne okno (indeksy włącznie)
  let hover = null;

  // wartości w oknie: przy `rebase` każda seria startuje od 100 w pierwszym punkcie okna
  let view = spec.series.map((s) => s.values);
  const recompute = () => {
    if (!spec.rebase) { view = spec.series.map((s) => s.values); return; }
    view = spec.series.map((s) => {
      let base = null;
      for (let i = lo; i <= hi; i++) if (isNum(s.values[i]) && s.values[i] !== 0) { base = s.values[i]; break; }
      return s.values.map((v) => (isNum(v) && base ? (v / base) * 100 : null));
    });
  };
  const lastIn = (vals) => { for (let i = hi; i >= lo; i--) if (isNum(vals[i])) return i; return -1; };
  const firstIn = (vals) => { for (let i = lo; i <= hi; i++) if (isNum(vals[i])) return i; return -1; };

  const show = (i) => {
    const date = fmtDate(spec.x[i === null ? hi : i]);
    view.forEach((vals, k) => {
      const j = i === null ? lastIn(vals) : i;
      lg.values[k].textContent = j >= 0 ? fmt(vals[j], spec.fmt, spec.digits) : "–";
    });
    readout.replaceChildren(...(many ? [date] : [date + "  ", el("b", {}, lg.values[0].textContent)]));
  };

  const drawSummary = () => {
    const zoomed = lo > 0 || hi < n - 1;
    const parts = [];
    if (spec.delta && (zoomed || spec.rebase)) {
      view.forEach((vals, k) => {
        const a = firstIn(vals), b = lastIn(vals);
        if (a < 0 || b < 0 || a === b) return;
        const d = spec.delta === "rel" ? vals[b] / vals[a] - 1 : vals[b] - vals[a];
        parts.push(el("span", {}, `${spec.series[k].name} `, el("b", { class: d >= 0 ? "pos" : "neg" },
          fmt(d, spec.delta === "rel" ? "pct" : spec.fmt, spec.delta === "rel" ? 1 : spec.digits, true))));
      });
    }
    summary.replaceChildren(...(zoomed ? [el("span", { class: "window-dates" }, `${fmtDate(spec.x[lo])} – ${fmtDate(spec.x[hi])}`)] : []), ...parts);
    summary.hidden = !summary.childNodes.length;
  };

  // ---- pasek narzędzi
  const tools = el("div", { class: "chart-tools", role: "toolbar", "aria-label": "Zakres wykresu" });
  const presets = PRESETS.map((p) => ({ ...p, start: indexFrom(spec.x, p.from(spec.x[n - 1])) }))
    .filter((p) => p.start > 0 && n - 1 - p.start >= MIN_SPAN - 1);
  const chips = presets.map((p) => {
    const b = el("button", { type: "button", "aria-pressed": "false" }, p.label);
    b.addEventListener("click", () => setRange(p.start, n - 1, true));
    return [p, b];
  });
  const all = el("button", { type: "button", "aria-pressed": "true" }, "Całość");
  all.addEventListener("click", () => setRange(0, n - 1, true));
  const zoomOut = el("button", { type: "button", class: "zoom", "aria-label": "Oddal" }, "−");
  const zoomIn = el("button", { type: "button", class: "zoom", "aria-label": "Przybliż" }, "+");
  zoomOut.addEventListener("click", () => zoomBy(1.6));
  zoomIn.addEventListener("click", () => zoomBy(1 / 1.6));
  const asPeriod = el("button", { type: "button", class: "as-period", hidden: true }, "Ustaw jako okres analiz");
  asPeriod.addEventListener("click", () => wrap.dispatchEvent(new CustomEvent("chart-period", {
    bubbles: true, detail: { since: lo > 0 ? spec.x[lo] : "", until: hi < n - 1 ? spec.x[hi] : "" },
  })));
  tools.append(...chips.map(([, b]) => b), all, el("span", { class: "spacer" }), zoomOut, zoomIn);

  const broadcast = joinGroup(spec.group, wrap, (from, to) => {
    const a = indexFrom(spec.x, from), b = indexTo(spec.x, to);
    if (b - a >= MIN_SPAN - 1) setRange(a, b, false);
    else setRange(0, n - 1, false);
  });

  function syncTools() {
    const zoomed = lo > 0 || hi < n - 1;
    chips.forEach(([p, b]) => b.setAttribute("aria-pressed", String(hi === n - 1 && lo === p.start)));
    all.setAttribute("aria-pressed", String(!zoomed));
    zoomOut.disabled = !zoomed;
    zoomIn.disabled = hi - lo + 1 <= MIN_SPAN;
    asPeriod.hidden = !zoomed || spec.period === false;
  }

  let frame = 0;
  function render() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; draw(); });
  }

  function setRange(a, b, user) {
    a = Math.max(0, Math.round(a)); b = Math.min(n - 1, Math.round(b));
    if (b - a < MIN_SPAN - 1) {
      const mid = (a + b) / 2;
      a = Math.max(0, Math.round(mid - (MIN_SPAN - 1) / 2)); b = Math.min(n - 1, a + MIN_SPAN - 1); a = Math.max(0, b - MIN_SPAN + 1);
    }
    if (a === lo && b === hi) return;
    lo = a; hi = b;
    recompute();
    syncTools();
    drawSummary();
    show(null);
    render();
    if (user) broadcast(spec.x[lo], spec.x[hi]);
  }

  function zoomBy(factor, center = (lo + hi) / 2) {
    const span = Math.max(MIN_SPAN - 1, Math.min(n - 1, (hi - lo) * factor));
    const t = (center - lo) / Math.max(1, hi - lo);
    let a = center - span * t;
    a = Math.max(0, Math.min(n - 1 - span, a));
    setRange(a, a + span, true);
  }

  // ---- rysowanie
  let geo = null;     // bieżąca geometria do obsługi gestów
  function draw() {
    const W = widthOf(body);
    const H = Math.round(Math.min(260, Math.max(190, W * 0.52)));
    const top = 18, bottom = 24, plotH = H - top - bottom;
    const vis = view.flatMap((vals) => vals.slice(lo, hi + 1).filter(isNum));
    let min = vis.length ? Math.min(...vis) : 0, max = vis.length ? Math.max(...vis) : 1;
    if (spec.zero) { min = Math.min(min, 0); max = Math.max(max, 0); }
    const t = niceTicks(min, max, 3);
    const span = Math.max(1, hi - lo);
    const x = (i) => PAD + ((i - lo) / span) * (W - 2 * PAD);
    const y = (v) => top + (1 - (v - t.lo) / (t.hi - t.lo)) * plotH;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": spec.series.map((s) => s.name).join(", ") });
    const clipId = `clip${Math.random().toString(36).slice(2, 9)}`;
    root.append(svg("defs", {}, svg("clipPath", { id: clipId }, svg("rect", { x: 0, y: 0, width: W, height: H }))));

    for (const v of t.ticks) {
      root.append(svg("line", { class: v === 0 ? "zero" : "grid", x1: 0, x2: W, y1: y(v), y2: y(v) }));
      const label = svg("text", { class: "axis-text", x: PAD, y: y(v) - 4 });
      label.textContent = fmtAxis(v, spec.fmt);
      root.append(label);
    }
    const days = (Date.parse(spec.x[hi]) - Date.parse(spec.x[lo])) / 864e5;
    for (const [i, anchor] of [[lo, "start"], [Math.round((lo + hi) / 2), "middle"], [hi, "end"]]) {
      const label = svg("text", { class: "axis-text", x: x(i), y: H - 6, "text-anchor": anchor });
      label.textContent = days < 120 ? fmtDate(spec.x[i]).slice(0, 5) : fmtDate(spec.x[i], "axis");
      root.append(label);
    }
    const plot = svg("g", { "clip-path": `url(#${clipId})` });
    const half = (W - 2 * PAD) / span / 2;
    for (const [a, b] of spec.spans || []) {       // zacieniowane okresy (np. nerwowy rynek)
      if (b < lo || a > hi) continue;
      plot.append(svg("rect", { x: x(a) - half, y: top, width: x(b) - x(a) + 2 * half, height: plotH, fill: "var(--ink)", "fill-opacity": 0.09 }));
    }
    const a0 = Math.max(0, lo - 1), b0 = Math.min(n - 1, hi + 1);   // jeden punkt zapasu: linia dochodzi do krawędzi
    view.forEach((vals, k) => {
      const s = spec.series[k];
      let d = "", pen = false, first = null, lastI = null;
      for (let i = a0; i <= b0; i++) {
        const v = vals[i];
        if (!isNum(v)) { pen = false; continue; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
        if (first === null) first = i;
        lastI = i;
      }
      if (spec.area && k === 0 && first !== null) {
        const base = y(Math.max(t.lo, Math.min(t.hi, 0)));
        plot.append(svg("path", { d: `${d}L${x(lastI).toFixed(1)},${base}L${x(first).toFixed(1)},${base}Z`, fill: color(s.color), "fill-opacity": 0.12 }));
      }
      plot.append(svg("path", { d, fill: "none", stroke: color(s.color), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    });
    root.append(plot);

    const cross = svg("line", { class: "cross", y1: top, y2: top + plotH, visibility: "hidden" });
    const dots = spec.series.map((s) => svg("circle", { r: 4, fill: color(s.color), stroke: "var(--page)", "stroke-width": 2, visibility: "hidden" }));
    const brush = svg("rect", { class: "brush", y: top, height: plotH, x: 0, width: 0, visibility: "hidden" });
    root.append(brush, cross, ...dots);
    geo = { root, W, x, index: (evt) => lo + ((pointerX(evt, root, W) - PAD) / (W - 2 * PAD)) * span };
    const mark = (i) => {
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      view.forEach((vals, k) => {
        if (isNum(vals[i])) { dots[k].setAttribute("cx", x(i)); dots[k].setAttribute("cy", y(vals[i])); dots[k].setAttribute("visibility", "visible"); }
        else dots[k].setAttribute("visibility", "hidden");
      });
      show(i);
    };
    geo.mark = mark;
    geo.unmark = () => { cross.setAttribute("visibility", "hidden"); dots.forEach((c) => c.setAttribute("visibility", "hidden")); show(null); };
    geo.brush = (a, b) => {
      if (a === null) { brush.setAttribute("visibility", "hidden"); return; }
      const xa = Math.min(x(a), x(b)), xb = Math.max(x(a), x(b));
      brush.setAttribute("x", xa); brush.setAttribute("width", xb - xa); brush.setAttribute("visibility", "visible");
    };
    if (hover !== null && hover >= lo && hover <= hi) mark(hover);
    body.replaceChildren(root);
    if (zoomable) drawNav(W);
  }

  // ---- mini-wykres całego okresu z oknem do przesuwania
  const nav = el("div", { class: "chart-nav" });
  let navGeo = null;
  function drawNav(W) {
    const H = 30, top = 3, plotH = H - 2 * top;
    const vals = spec.series[0].values;
    const nums = vals.filter(isNum);
    if (!nums.length) return;
    let min = Math.min(...nums), max = Math.max(...nums);
    if (max === min) max = min + 1;
    const x = (i) => PAD + (i / Math.max(1, n - 1)) * (W - 2 * PAD);
    const y = (v) => top + (1 - (v - min) / (max - min)) * plotH;
    let d = "", pen = false;
    vals.forEach((v, i) => { if (!isNum(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" },
      svg("path", { d, fill: "none", stroke: color(spec.series[0].color), "stroke-width": 1, "stroke-opacity": 0.55 }),
      svg("rect", { class: "nav-shade", x: 0, y: 0, width: Math.max(0, x(lo)), height: H }),
      svg("rect", { class: "nav-shade", x: x(hi), y: 0, width: Math.max(0, W - x(hi)), height: H }),
      svg("rect", { class: "nav-window", x: x(lo), y: 0.5, width: Math.max(2, x(hi) - x(lo)), height: H - 1, rx: 2 }),
      svg("rect", { class: "nav-handle", x: x(lo) - 1.5, y: H / 2 - 6, width: 3, height: 12, rx: 1.5 }),
      svg("rect", { class: "nav-handle", x: x(hi) - 1.5, y: H / 2 - 6, width: 3, height: 12, rx: 1.5 }));
    navGeo = { root, W, x, at: (evt) => ((pointerX(evt, root, W) - PAD) / (W - 2 * PAD)) * (n - 1) };
    nav.replaceChildren(root);
  }

  // przeciąganie na mini-wykresie: brzeg okna zmienia zakres, środek przesuwa, stuknięcie obok centruje okno
  let navDrag = null;
  nav.addEventListener("pointerdown", (evt) => {
    if (!navGeo) return;
    evt.preventDefault();
    nav.setPointerCapture?.(evt.pointerId);
    const px = pointerX(evt, navGeo.root, navGeo.W);
    const grab = evt.pointerType === "mouse" ? 8 : 16;
    const xl = navGeo.x(lo), xr = navGeo.x(hi);
    let mode;
    if (Math.abs(px - xl) <= grab && Math.abs(px - xl) <= Math.abs(px - xr)) mode = "lo";
    else if (Math.abs(px - xr) <= grab) mode = "hi";
    else if (px > xl && px < xr) mode = "pan";
    else {
      const w = hi - lo, c = navGeo.at(evt);
      const a = Math.max(0, Math.min(n - 1 - w, c - w / 2));
      setRange(a, a + w, true);
      mode = "pan";
    }
    navDrag = { mode, start: navGeo.at(evt), lo, hi };
  });
  nav.addEventListener("pointermove", (evt) => {
    if (!navDrag || !navGeo) return;
    const d = navGeo.at(evt) - navDrag.start;
    if (navDrag.mode === "lo") setRange(Math.min(navDrag.lo + d, hi - MIN_SPAN + 1), hi, true);
    else if (navDrag.mode === "hi") setRange(lo, Math.max(navDrag.hi + d, lo + MIN_SPAN - 1), true);
    else {
      const w = navDrag.hi - navDrag.lo;
      const a = Math.max(0, Math.min(n - 1 - w, navDrag.lo + d));
      setRange(a, a + w, true);
    }
  });
  const endNav = () => { navDrag = null; };
  nav.addEventListener("pointerup", endNav);
  nav.addEventListener("pointercancel", endNav);

  // ---- gesty na samym wykresie
  const touches = new Map();
  let pinch = null, drag = null;
  const clampI = (v) => Math.max(lo, Math.min(hi, Math.round(v)));
  body.addEventListener("pointerdown", (evt) => {
    if (!geo) return;
    touches.set(evt.pointerId, evt);
    if (zoomable && touches.size === 2) {
      // dwa palce: przybliżanie i przesuwanie; odczyt pod palcem się chowa
      const [p1, p2] = [...touches.values()];
      pinch = { d: Math.abs(p1.clientX - p2.clientX) || 1, mid: (geo.index(p1) + geo.index(p2)) / 2, lo, hi };
      drag = null;
      geo.brush(null);
      geo.unmark();
      hover = null;
      return;
    }
    if (evt.pointerType === "mouse" && evt.button === 0 && zoomable) {
      drag = { from: clampI(geo.index(evt)), to: null };
      body.setPointerCapture?.(evt.pointerId);
    }
    hover = clampI(geo.index(evt));
    geo.mark(hover);
  });
  body.addEventListener("pointermove", (evt) => {
    if (!geo) return;
    if (touches.has(evt.pointerId)) touches.set(evt.pointerId, evt);
    if (pinch && touches.size === 2) {
      evt.preventDefault();
      const [p1, p2] = [...touches.values()];
      const d = Math.abs(p1.clientX - p2.clientX) || 1;
      const span = Math.max(MIN_SPAN - 1, Math.min(n - 1, (pinch.hi - pinch.lo) * (pinch.d / d)));
      // punkt pod środkiem palców zostaje pod nimi
      const r = geo.root.getBoundingClientRect();
      const t = ((p1.clientX + p2.clientX) / 2 - r.left) / r.width;
      const tPlot = ((t * geo.W) - PAD) / (geo.W - 2 * PAD);
      let a = pinch.mid - span * tPlot;
      a = Math.max(0, Math.min(n - 1 - span, a));
      setRange(a, a + span, true);
      return;
    }
    if (pinch) return;
    if (evt.pointerType === "mouse" || evt.pressure > 0) {
      hover = clampI(geo.index(evt));
      geo.mark(hover);
      if (drag) { drag.to = hover; geo.brush(drag.from, drag.to); }
    }
  });
  const release = (evt) => {
    touches.delete(evt.pointerId);
    if (pinch && touches.size < 2) { pinch = null; return; }
    if (drag && evt.type === "pointerup") {
      const { from, to } = drag;
      drag = null;
      geo?.brush(null);
      if (to !== null && Math.abs(to - from) >= MIN_SPAN - 1 && geo && Math.abs(geo.x(to) - geo.x(from)) > 12) {
        setRange(Math.min(from, to), Math.max(from, to), true);
      }
    }
  };
  body.addEventListener("pointerup", release);
  body.addEventListener("pointercancel", (evt) => { release(evt); drag = null; geo?.brush(null); });
  body.addEventListener("pointerleave", (evt) => {
    if (evt.pointerType === "mouse" && !drag) { hover = null; geo?.unmark(); }
    else if (evt.pointerType !== "mouse" && !pinch) { hover = null; geo?.unmark(); }
  });
  body.addEventListener("dblclick", () => { if (zoomable) setRange(0, n - 1, true); });
  // Ctrl/⌘ + kółko albo gest szczypania na gładziku (przeglądarka zgłasza go jako wheel z ctrlKey); poziome przewijanie przesuwa
  body.addEventListener("wheel", (evt) => {
    if (!zoomable || !geo) return;
    if (evt.ctrlKey || evt.metaKey) {
      evt.preventDefault();
      zoomBy(Math.exp(Math.max(-0.5, Math.min(0.5, evt.deltaY * 0.01))), geo.index(evt));
    } else if (Math.abs(evt.deltaX) > Math.abs(evt.deltaY) && (lo > 0 || hi < n - 1)) {
      evt.preventDefault();
      const w = hi - lo;
      const a = Math.max(0, Math.min(n - 1 - w, lo + (evt.deltaX / geo.W) * w));
      setRange(a, a + w, true);
    }
  }, { passive: false });

  wrap.append(lg.ul, readout, body);
  if (zoomable) wrap.append(nav, tools, summary, asPeriod);
  else wrap.append(summary);
  if (!many) lg.ul.hidden = true;
  recompute();
  syncTools();
  drawSummary();
  show(null);

  let lastW = 0;
  const onResize = () => { const w = widthOf(body); if (w !== lastW) { lastW = w; draw(); } };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(onResize).observe(body);
  requestAnimationFrame(onResize);
  return wrap;
}

// --------------------------------------------------------------------------- słupki poziome (HTML)

function hbarChart(spec) {
  const wrap = el("div");
  const grouped = Array.isArray(spec.series);
  const series = grouped ? spec.series : [{ name: "", values: spec.values, color: spec.color }];
  if (grouped) wrap.append(legend(series, true).ul);
  const all = series.flatMap((s) => s.values.filter(isNum));
  // oś zera stoi tam, gdzie wynika to z danych: ujemne w lewo, dodatnie w prawo, cała szerokość wykorzystana
  const maxPos = Math.max(0, ...all), maxNeg = Math.max(0, ...all.map((v) => -v));
  const total = maxPos + maxNeg || 1e-12;
  const hasNeg = maxNeg > 0;
  const zero = (maxNeg / total) * 100;
  const longest = Math.max(...spec.labels.map((l) => l.length), 3);
  const list = el("div", { class: "hbars", style: `--name-w:${Math.min(longest, 22) * 0.62 + 0.6}em` });
  spec.labels.forEach((name, i) => {
    const track = el("div", { class: "track" + (grouped ? " double" : "") });
    if (hasNeg) track.append(el("div", { class: "axis", style: `left:${zero}%` }));
    series.forEach((s) => {
      const v = s.values[i];
      if (!isNum(v)) return;
      const w = (Math.abs(v) / total) * 100;
      const fill = spec.signed && !grouped ? (v < 0 ? "var(--neg)" : "var(--pos)") : color(s.color);
      const pos = v < 0 ? `left:${zero - w}%` : `left:${zero}%`;
      track.append(el("div", { class: "hb" + (v < 0 ? " neg" : ""), style: `${pos};width:${w}%;background:${fill}` }));
    });
    const val = el("div", { class: "val" }, fmt(series[0].values[i], spec.fmt, spec.digits));
    if (grouped && series[1]) val.append(el("small", {}, fmt(series[1].values[i], spec.fmt, spec.digits)));
    list.append(el("div", { class: "hbar" }, el("div", { class: "name", title: name }, name), track, val));
  });
  wrap.append(list);
  return wrap;
}

// --------------------------------------------------------------------------- słupki pionowe zgrupowane

function barsChart(spec) {
  const wrap = el("div");
  const lg = legend(spec.series, true);
  const readout = el("p", { class: "readout" }, "Dotknij grupy, żeby zobaczyć wartości");
  const body = el("div", { class: "chart-body" });
  wrap.append(lg.ul, readout, body);
  const single = spec.series.length === 1;
  if (single) { lg.ul.hidden = true; readout.textContent = "Dotknij słupka, żeby zobaczyć wartość"; }
  const show = (g) => {
    spec.series.forEach((s, k) => { lg.values[k].textContent = g === null ? "" : fmt(s.values[g], spec.fmt, spec.digits); });
    if (single) readout.replaceChildren(`${spec.labels[g]}  `, el("b", {}, fmt(spec.series[0].values[g], spec.fmt, spec.digits)));
    else readout.textContent = spec.labels[g];
  };
  responsive(body, (W) => {
    const H = 220, top = 18, bottom = 24, plotH = H - top - bottom;
    const all = spec.series.flatMap((s) => s.values.filter(isNum));
    const t = niceTicks(Math.min(0, ...all), Math.max(0, ...all), 3);
    const y = (v) => top + (1 - (v - t.lo) / (t.hi - t.lo)) * plotH;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    for (const v of t.ticks) {
      root.append(svg("line", { class: v === 0 ? "zero" : "grid", x1: 0, x2: W, y1: y(v), y2: y(v) }));
      const label = svg("text", { class: "axis-text", x: PAD, y: y(v) - 4 });
      label.textContent = fmtAxis(v, spec.fmt);
      root.append(label);
    }
    const G = spec.labels.length, S = spec.series.length;
    const left = PAD + 34, groupW = (W - left - PAD) / G;
    const barW = Math.min(24, (groupW * 0.78 - (S - 1) * 2) / S);
    spec.labels.forEach((name, g) => {
      const x0 = left + g * groupW + (groupW - (S * barW + (S - 1) * 2)) / 2;
      spec.series.forEach((s, k) => {
        const v = s.values[g];
        if (!isNum(v)) return;
        const y0 = y(Math.max(v, 0)), h = Math.max(1, Math.abs(y(v) - y(0)));
        const fill = single && spec.signed !== false ? (v < 0 ? "var(--neg)" : "var(--pos)") : color(s.color);
        root.append(svg("rect", { x: x0 + k * (barW + 2), y: y0, width: barW, height: h, rx: Math.min(4, barW / 2), fill }));
      });
      const label = svg("text", { class: "axis-text", x: left + g * groupW + groupW / 2, y: H - 6, "text-anchor": "middle" });
      label.textContent = name;
      root.append(label);
      const hit = svg("rect", { x: left + g * groupW, y: 0, width: groupW, height: H, fill: "transparent" });
      hit.addEventListener("pointerdown", () => show(g));
      hit.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") show(g); });
      root.append(hit);
    });
    return root;
  });
  return wrap;
}

// --------------------------------------------------------------------------- mapa ciepła

function mix(a, b, t) {
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",")})`;
}
const C_NEG = [230, 103, 103], C_POS = [57, 135, 229], C_MID = [43, 55, 66], C_LOW = [16, 66, 129], C_HIGH = [205, 226, 251];

function heatmapChart(spec) {
  const wrap = el("div");
  const hint = "Dotknij pola, żeby zobaczyć wartość";
  const readout = el("p", { class: "readout" }, hint);
  const body = el("div", { class: "chart-body" });
  wrap.append(readout, body);
  const flat = spec.z.flat().filter(isNum);
  const maxAbs = Math.max(...flat.map(Math.abs), 1e-12), lo = Math.min(...flat), hi = Math.max(...flat);
  const fill = (v) => {
    if (!isNum(v)) return "transparent";
    if (spec.diverging) return v >= 0 ? mix(C_MID, C_POS, Math.min(1, v / maxAbs)) : mix(C_MID, C_NEG, Math.min(1, -v / maxAbs));
    return mix(C_LOW, C_HIGH, hi > lo ? (v - lo) / (hi - lo) : 0.5);
  };
  responsive(body, (W) => {
    const R = spec.rows.length, C = spec.cols.length;
    const labelW = Math.min(92, Math.max(34, Math.max(...spec.rows.map((r) => r.length)) * 6.6 + 8));
    const showCols = C <= 12 && Math.max(...spec.cols.map((c) => c.length)) <= 4;
    const top = showCols ? 18 : 4;
    const cw = (W - 2 * PAD - labelW) / C;
    const ch = Math.max(18, Math.min(34, cw));
    const H = top + R * ch + 4;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    let active = null;
    spec.rows.forEach((rname, r) => {
      const label = svg("text", { class: "axis-text", x: PAD, y: top + r * ch + ch / 2 + 4 });
      label.textContent = rname.length > 13 ? rname.slice(0, 12) + "…" : rname;
      root.append(label);
      spec.cols.forEach((cname, c) => {
        const v = spec.z[r][c];
        const rect = svg("rect", { x: PAD + labelW + c * cw + 1, y: top + r * ch + 1, width: Math.max(1, cw - 2), height: ch - 2, rx: 3, fill: fill(v) });
        if (isNum(v)) {
          rect.addEventListener("pointerdown", () => {
            if (active) active.removeAttribute("stroke");
            active = rect;
            rect.setAttribute("stroke", "var(--ink)"); rect.setAttribute("stroke-width", 1.5);
            readout.replaceChildren(`${rname} / ${cname}  `, el("b", {}, fmt(v, spec.fmt, spec.digits)));
          });
        }
        root.append(rect);
        if (spec.labels && cw >= 34 && isNum(v)) {
          const tx = svg("text", { class: "axis-text", x: PAD + labelW + c * cw + cw / 2, y: top + r * ch + ch / 2 + 4, "text-anchor": "middle", fill: "var(--ink)", "pointer-events": "none" });
          tx.textContent = fmt(v, spec.fmt, spec.digits);
          root.append(tx);
        }
      });
    });
    if (showCols) spec.cols.forEach((cname, c) => {
      const label = svg("text", { class: "axis-text", x: PAD + labelW + c * cw + cw / 2, y: 12, "text-anchor": "middle" });
      label.textContent = cname;
      root.append(label);
    });
    return root;
  });
  return wrap;
}

// --------------------------------------------------------------------------- histogram

function histChart(spec) {
  const wrap = el("div");
  const hint = "Dotknij słupka, żeby zobaczyć przedział";
  const readout = el("p", { class: "readout" }, hint);
  const body = el("div", { class: "chart-body" });
  wrap.append(readout, body);
  responsive(body, (W) => {
    const H = 200, top = 22, bottom = 24, plotH = H - top - bottom;
    const B = spec.counts.length, lo = spec.edges[0], hi = spec.edges[B];
    const maxC = Math.max(...spec.counts, 1);
    const x = (v) => PAD + ((v - lo) / (hi - lo)) * (W - 2 * PAD);
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    root.append(svg("line", { class: "zero", x1: 0, x2: W, y1: top + plotH, y2: top + plotH }));
    const cut = spec.markers.length ? spec.markers[0].x : -Infinity;
    spec.counts.forEach((c, i) => {
      const x0 = x(spec.edges[i]), x1 = x(spec.edges[i + 1]);
      const h = (c / maxC) * plotH;
      const bar = svg("rect", { x: x0 + 1, y: top + plotH - h, width: Math.max(1, x1 - x0 - 2), height: h, rx: 2,
        fill: spec.edges[i + 1] <= cut ? "var(--neg)" : "var(--s0)" });
      const hit = svg("rect", { x: x0, y: top, width: x1 - x0, height: plotH, fill: "transparent" });
      hit.addEventListener("pointerdown", () => readout.replaceChildren(
        `${fmt(spec.edges[i], spec.fmt, spec.digits)} do ${fmt(spec.edges[i + 1], spec.fmt, spec.digits)}  `, el("b", {}, `${c} dni`)));
      root.append(bar, hit);
    });
    for (const m of spec.markers) {
      root.append(svg("line", { x1: x(m.x), x2: x(m.x), y1: top - 6, y2: top + plotH, stroke: "var(--ink)", "stroke-width": 1 }));
      const label = svg("text", { class: "axis-text", x: x(m.x) + 5, y: top - 8, fill: "var(--ink-2)" });
      label.textContent = `${m.label}: ${fmt(m.x, spec.fmt, spec.digits)}`;
      root.append(label);
    }
    for (const [v, anchor] of [[lo, "start"], [0, "middle"], [hi, "end"]]) {
      if (v < lo || v > hi) continue;
      const label = svg("text", { class: "axis-text", x: x(v), y: H - 6, "text-anchor": anchor });
      label.textContent = fmtAxis(v, spec.fmt);
      root.append(label);
    }
    return root;
  });
  return wrap;
}

// --------------------------------------------------------------------------- wachlarz (percentyle)

function fanChart(spec) {
  const wrap = el("div");
  const keys = [{ name: "Mediana", color: 0 }, ...spec.lines.map((l) => ({ name: l.name, color: l.color ?? "neutral" }))];
  const lg = legend(keys);
  const readout = el("p", { class: "readout" });
  const body = el("div", { class: "chart-body" });
  wrap.append(lg.ul, readout, body);
  const n = spec.x.length;
  const show = (i) => {
    const j = i ?? n - 1;
    lg.values[0].textContent = fmt(spec.median[j], spec.fmt, spec.digits);
    spec.lines.forEach((l, k) => { lg.values[k + 1].textContent = fmt(l.values[j], spec.fmt, spec.digits); });
    const outer = spec.bands[0];
    readout.replaceChildren(`${spec.x[j]}  `, el("b", {}, `${fmt(outer.lo[j], spec.fmt, spec.digits)} – ${fmt(outer.hi[j], spec.fmt, spec.digits)}`), `  (${outer.label})`);
  };
  show(null);
  responsive(body, (W) => {
    const H = 240, top = 18, bottom = 24, plotH = H - top - bottom;
    const all = [...spec.bands.flatMap((b) => [...b.lo, ...b.hi]), ...spec.median, ...spec.lines.flatMap((l) => l.values)].filter(isNum);
    const t = niceTicks(Math.min(...all), Math.max(...all), 3);
    const x = (i) => PAD + (i / Math.max(1, n - 1)) * (W - 2 * PAD);
    const y = (v) => top + (1 - (v - t.lo) / (t.hi - t.lo)) * plotH;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    for (const v of t.ticks) {
      root.append(svg("line", { class: "grid", x1: 0, x2: W, y1: y(v), y2: y(v) }));
      const label = svg("text", { class: "axis-text", x: PAD, y: y(v) - 4 });
      label.textContent = fmtAxis(v, spec.fmt);
      root.append(label);
    }
    spec.bands.forEach((b, k) => {
      const up = b.hi.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
      const down = b.lo.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).reverse();
      root.append(svg("polygon", { points: [...up, ...down].join(" "), fill: "var(--s0)", "fill-opacity": 0.14 + 0.12 * k }));
    });
    const path = (vals) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    spec.lines.forEach((l) => root.append(svg("path", { d: path(l.values), fill: "none", stroke: color(l.color ?? "neutral"), "stroke-width": 1.5 })));
    root.append(svg("path", { d: path(spec.median), fill: "none", stroke: "var(--s0)", "stroke-width": 2, "stroke-linejoin": "round" }));
    for (const [i, anchor] of [[0, "start"], [Math.floor((n - 1) / 2), "middle"], [n - 1, "end"]]) {
      const label = svg("text", { class: "axis-text", x: x(i), y: H - 6, "text-anchor": anchor });
      label.textContent = spec.x[i];
      root.append(label);
    }
    const cross = svg("line", { class: "cross", y1: top, y2: top + plotH, visibility: "hidden" });
    root.append(cross);
    const move = (evt) => {
      const i = Math.max(0, Math.min(n - 1, Math.round(((pointerX(evt, root, W) - PAD) / (W - 2 * PAD)) * (n - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      show(i);
    };
    root.addEventListener("pointerdown", move);
    root.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse" || e.pressure > 0) move(e); });
    root.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); show(null); });
    return root;
  });
  return wrap;
}

// --------------------------------------------------------------------------- linie nad osią kategorii (krzywa rentowności)

function curveChart(spec) {
  const wrap = el("div");
  const lg = legend(spec.series);
  const readout = el("p", { class: "readout" });
  const body = el("div", { class: "chart-body" });
  wrap.append(lg.ul, readout, body);
  const n = spec.labels.length;
  const show = (i) => {
    const j = i ?? n - 1;
    spec.series.forEach((s, k) => { lg.values[k].textContent = fmt(s.values[j], spec.fmt, spec.digits); });
    readout.textContent = i === null ? "Dotknij wykresu, żeby odczytać wartości" : spec.labels[j];
  };
  show(null);
  responsive(body, (W) => {
    const H = 230, top = 18, bottom = 24, plotH = H - top - bottom;
    const all = spec.series.flatMap((s) => s.values.filter(isNum));
    const t = niceTicks(Math.min(...all), Math.max(...all), 4);
    const left = PAD + 30, right = PAD;
    const x = (i) => left + (i / Math.max(1, n - 1)) * (W - left - right);
    const y = (v) => top + (1 - (v - t.lo) / (t.hi - t.lo)) * plotH;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    for (const v of t.ticks) {
      root.append(svg("line", { class: "grid", x1: 0, x2: W, y1: y(v), y2: y(v) }));
      const label = svg("text", { class: "axis-text", x: PAD, y: y(v) - 4 });
      label.textContent = fmtAxis(v, spec.fmt);
      root.append(label);
    }
    const step = Math.max(1, Math.ceil(n / Math.floor((W - left - right) / 30)));
    spec.labels.forEach((name, i) => {
      if (i % step && i !== n - 1) return;
      const label = svg("text", { class: "axis-text", x: x(i), y: H - 6, "text-anchor": "middle" });
      label.textContent = name;
      root.append(label);
    });
    // najpierw serie porównawcze, na wierzchu pierwsza (bieżąca)
    [...spec.series].reverse().forEach((s, r) => {
      const first = r === spec.series.length - 1;
      let d = "", pen = false;
      s.values.forEach((v, i) => {
        if (!isNum(v)) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      root.append(svg("path", { d, fill: "none", stroke: color(s.color), "stroke-width": first ? 2.2 : 1.5,
        "stroke-dasharray": first ? null : "4 3", "stroke-linejoin": "round" }));
      s.values.forEach((v, i) => {
        if (isNum(v)) root.append(svg("circle", { cx: x(i), cy: y(v), r: first ? 3 : 2, fill: color(s.color) }));
      });
    });
    const cross = svg("line", { class: "cross", y1: top, y2: top + plotH, visibility: "hidden" });
    root.append(cross);
    const move = (evt) => {
      const i = Math.max(0, Math.min(n - 1, Math.round(((pointerX(evt, root, W) - left) / (W - left - right)) * (n - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      show(i);
    };
    root.addEventListener("pointerdown", move);
    root.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse" || e.pressure > 0) move(e); });
    root.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); show(null); });
    return root;
  });
  return wrap;
}

const RENDERERS = { line: lineChart, hbar: hbarChart, bars: barsChart, heatmap: heatmapChart, hist: histChart, fan: fanChart, curve: curveChart };

export function renderChart(spec) {
  const fn = RENDERERS[spec.type];
  if (!fn) return el("p", { class: "note warn" }, `Nieznany typ wykresu: ${spec.type}`);
  try {
    return fn(spec);
  } catch (err) {
    console.error("Błąd wykresu", spec.type, err);
    return el("p", { class: "note warn" }, "Nie udało się narysować wykresu.");
  }
}
