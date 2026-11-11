// Wykresy SVG pisane pod telefon: pełna szerokość, odczyt pod palcem, bez zewnętrznych bibliotek.
// Każda funkcja dostaje specyfikację z serwera (server/blocks.py) i zwraca element DOM.

import { el, fmt, fmtAxis, fmtDate, isNum, niceTicks } from "./format.js";

const NS = "http://www.w3.org/2000/svg";
const PAD = 20; // margines boczny = --gutter; wykres idzie od krawędzi do krawędzi

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

function lineChart(spec) {
  const wrap = el("div");
  const many = spec.series.length > 1;
  const lg = legend(spec.series);
  const readout = el("p", { class: "readout" });
  const body = el("div", { class: "chart-body" });
  wrap.append(lg.ul, readout, body);
  if (!many) lg.ul.hidden = true;

  const n = spec.x.length;
  const last = (s) => { for (let i = s.values.length - 1; i >= 0; i--) if (isNum(s.values[i])) return i; return -1; };
  const show = (i) => {
    const date = i === null ? fmtDate(spec.x[n - 1]) : fmtDate(spec.x[i]);
    spec.series.forEach((s, k) => {
      const j = i === null ? last(s) : i;
      lg.values[k].textContent = j >= 0 ? fmt(s.values[j], spec.fmt, spec.digits) : "–";
    });
    readout.replaceChildren(...(many ? [date] : [date + "  ", el("b", {}, lg.values[0].textContent)]));
  };
  show(null);

  responsive(body, (W) => {
    const H = Math.round(Math.min(260, Math.max(190, W * 0.52)));
    const top = 18, bottom = 24, plotH = H - top - bottom;
    const all = spec.series.flatMap((s) => s.values.filter(isNum));
    let min = Math.min(...all), max = Math.max(...all);
    if (spec.zero) { min = Math.min(min, 0); max = Math.max(max, 0); }
    const t = niceTicks(min, max, 3);
    const x = (i) => PAD + (i / Math.max(1, n - 1)) * (W - 2 * PAD);
    const y = (v) => top + (1 - (v - t.lo) / (t.hi - t.lo)) * plotH;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": spec.series.map((s) => s.name).join(", ") });

    for (const v of t.ticks) {
      root.append(svg("line", { class: v === 0 ? "zero" : "grid", x1: 0, x2: W, y1: y(v), y2: y(v) }));
      const label = svg("text", { class: "axis-text", x: PAD, y: y(v) - 4 });
      label.textContent = fmtAxis(v, spec.fmt);
      root.append(label);
    }
    for (const [i, anchor] of [[0, "start"], [Math.floor((n - 1) / 2), "middle"], [n - 1, "end"]]) {
      const label = svg("text", { class: "axis-text", x: x(i), y: H - 6, "text-anchor": anchor });
      label.textContent = fmtDate(spec.x[i], "axis");
      root.append(label);
    }
    const half = (W - 2 * PAD) / Math.max(1, n - 1) / 2;
    for (const [a, b] of spec.spans || []) {       // zacieniowane okresy (np. nerwowy rynek)
      root.append(svg("rect", { x: x(a) - half, y: top, width: x(b) - x(a) + 2 * half, height: plotH, fill: "var(--ink)", "fill-opacity": 0.09 }));
    }
    spec.series.forEach((s, k) => {
      let d = "", pen = false, first = null, lastI = null;
      s.values.forEach((v, i) => {
        if (!isNum(v)) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
        if (first === null) first = i;
        lastI = i;
      });
      if (spec.area && k === 0 && first !== null) {
        const base = y(Math.max(t.lo, Math.min(t.hi, 0)));
        root.append(svg("path", { d: `${d}L${x(lastI).toFixed(1)},${base}L${x(first).toFixed(1)},${base}Z`, fill: color(s.color), "fill-opacity": 0.12 }));
      }
      root.append(svg("path", { d, fill: "none", stroke: color(s.color), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    });

    const cross = svg("line", { class: "cross", y1: top, y2: top + plotH, visibility: "hidden" });
    const dots = spec.series.map((s) => svg("circle", { r: 4, fill: color(s.color), stroke: "var(--page)", "stroke-width": 2, visibility: "hidden" }));
    root.append(cross, ...dots);
    const move = (evt) => {
      const i = Math.max(0, Math.min(n - 1, Math.round(((pointerX(evt, root, W) - PAD) / (W - 2 * PAD)) * (n - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      spec.series.forEach((s, k) => {
        if (isNum(s.values[i])) { dots[k].setAttribute("cx", x(i)); dots[k].setAttribute("cy", y(s.values[i])); dots[k].setAttribute("visibility", "visible"); }
        else dots[k].setAttribute("visibility", "hidden");
      });
      show(i);
    };
    const leave = () => { cross.setAttribute("visibility", "hidden"); dots.forEach((c) => c.setAttribute("visibility", "hidden")); show(null); };
    root.addEventListener("pointerdown", move);
    root.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse" || e.pressure > 0) move(e); });
    root.addEventListener("pointerleave", leave);
    root.addEventListener("pointercancel", leave);
    return root;
  });
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

const RENDERERS = { line: lineChart, hbar: hbarChart, bars: barsChart, heatmap: heatmapChart, hist: histChart, fan: fanChart };

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
