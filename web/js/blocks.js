// Rysowanie bloków ekranu przysłanych przez serwer (server/blocks.py).
// Wszystkie teksty wstawiane przez textContent - dane z pliku użytkownika nigdy nie trafiają do innerHTML.

import { renderChart } from "./charts.js";
import { el, fmt, richText } from "./format.js";

let uid = 0;

function title(text) {
  return text ? el("h2", { class: "block-title" }, text) : null;
}

function statement(b) {
  return el("section", { class: "statement" }, el("p", {}, b.text), b.sub ? el("p", { class: "sub" }, b.sub) : null);
}

/** Kolor liczby: strata zawsze czerwona; zysk zielony tylko tam, gdzie liczba jest wynikiem (ze znakiem). */
function tone(value, kind, signed) {
  if (typeof value !== "number" || !Number.isFinite(value) || !["pln", "zl", "pct"].includes(kind)) return "";
  if (value < 0) return " neg";
  return signed && value > 0 ? " pos" : "";
}

function metrics(b) {
  const list = el("ul", { class: "ledger" });
  for (const m of b.items) {
    const id = `tip-${++uid}`;
    const value = el("span", { class: "value" + tone(m.value, m.fmt, m.sign) }, fmt(m.value, m.fmt, m.digits, m.sign));
    const label = el("span", { class: "label" }, el("span", {}, m.label), m.tip ? el("i", { class: "hint", "aria-hidden": "true" }, "?") : null);
    const note = m.note ? el("span", { class: "value-note" }, m.note) : null;
    if (!m.tip) {
      list.append(el("li", {}, el("div", { class: "row" }, label, value, note)));
      continue;
    }
    const tip = el("div", { class: "tip", id, role: "region" }, el("div", {}, el("p", {}, m.tip)));
    const row = el("button", { class: "row", type: "button", "aria-expanded": "false", "aria-controls": id }, label, value, note);
    row.addEventListener("click", () => {
      const open = row.getAttribute("aria-expanded") !== "true";
      row.setAttribute("aria-expanded", String(open));
      tip.classList.toggle("open", open);
    });
    list.append(el("li", {}, row, tip));
  }
  return el("section", {}, title(b.title), list);
}

function chart(b) {
  return el("figure", { class: "chart" }, title(b.title), renderChart(b.chart),
    b.caption ? el("figcaption", { class: "caption" }, b.caption) : null);
}

function table(b) {
  const numeric = (c) => c.fmt !== "text";
  const head = el("tr", {}, b.columns.map((c) => el("th", { class: numeric(c) ? null : "t", scope: "col" }, c.label)));
  const body = el("tbody");
  const draw = (rows) => body.replaceChildren(...rows.map((r) => el("tr", {}, b.columns.map((c, i) =>
    el("td", { class: (!numeric(c) && i > 0 ? "t" : "") + tone(r[c.key], c.fmt, c.signed) || null }, fmt(r[c.key], c.fmt, c.digits, c.signed))))));
  const limit = b.limit || 12;
  draw(b.rows.slice(0, limit));
  const wrap = el("div", { class: "table-wrap" }, el("table", { class: "data" }, el("thead", {}, head), body));
  const out = el("section", {}, title(b.title), b.rows.length ? wrap : el("p", { class: "caption" }, "Brak wierszy."));
  if (b.rows.length > limit) {
    const more = el("button", { class: "more-rows", type: "button" }, `Pokaż wszystkie (${b.rows.length})`);
    more.addEventListener("click", () => { draw(b.rows); more.remove(); });
    out.append(more);
  }
  if (b.caption) out.append(el("p", { class: "caption" }, b.caption));
  return out;
}

function note(b) {
  return el("p", { class: "note" + (b.tone && b.tone !== "info" ? " " + b.tone : "") }, b.text);
}

function bullets(b) {
  return el("section", {}, title(b.title), el("ul", { class: "bullets" }, b.items.map((t) => richText(t, el("li")))));
}

function section(b, ctx) {
  const body = el("div", { class: "section-body" }, b.blocks.map((x) => renderBlock(x, ctx)));
  const d = el("details", { class: "section" }, el("summary", {}, b.title), body);
  if (b.open) d.setAttribute("open", "");
  return d;
}

function download(b, ctx) {
  const btn = el("button", { class: "btn quiet", type: "button" }, b.label);
  btn.addEventListener("click", () => ctx.onDownload(b.path, b.filename, btn));
  return btn;
}

/** Jedna kontrolka; onChange(wartość) wołane przy każdej zmianie. */
export function controlInput(c, onChange) {
  const id = `ctl-${++uid}`;
  const wrap = el("div", { class: "control" });
  const tip = c.tip ? el("p", { class: "tip-text" }, c.tip) : null;
  if (c.type === "segmented") {
    const group = el("div", { class: "segmented", role: "group", "aria-label": c.label });
    const buttons = c.options.map((o) => {
      const btn = el("button", { type: "button", "aria-pressed": String(o.value === c.value) }, o.label);
      btn.addEventListener("click", () => {
        buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
        onChange(o.value);
      });
      return btn;
    });
    group.append(...buttons);
    wrap.append(el("span", { class: "control-label" }, c.label), group);
  } else if (c.type === "select") {
    const sel = el("select", { class: "field", id }, c.options.map((o) => {
      const opt = el("option", { value: o.value }, o.label);
      if (o.value === c.value) opt.selected = true;
      return opt;
    }));
    sel.addEventListener("change", () => onChange(sel.value));
    wrap.append(el("label", { for: id }, c.label), sel);
  } else if (c.type === "slider") {
    const show = (v) => fmt(v, c.fmt || "num", c.digits) + (c.unit || "");
    const out = el("output", { for: id }, show(c.value));
    const input = el("input", { type: "range", id, min: c.min, max: c.max, step: c.step ?? 1, value: c.value });
    input.addEventListener("input", () => { out.textContent = show(+input.value); });
    input.addEventListener("change", () => onChange(+input.value));
    wrap.append(el("label", { for: id }, c.label), el("div", { class: "slider-row" }, input, out));
  } else if (c.type === "toggle") {
    const input = el("input", { type: "checkbox", id });
    input.checked = !!c.value;
    input.addEventListener("change", () => onChange(input.checked));
    wrap.append(el("div", { class: "toggle" }, el("label", { for: id, style: "margin:0;font-size:15px;color:var(--ink)" }, c.label), input));
  } else {
    const numeric = c.type === "number";
    const input = el("input", { class: "field", id, type: "text", inputmode: numeric ? "decimal" : null, value: c.value ?? "",
      placeholder: c.placeholder, autocomplete: "off", autocapitalize: c.upper ? "characters" : "off", spellcheck: "false" });
    input.addEventListener("change", () => onChange(numeric ? input.value.replace(",", ".").trim() : input.value.trim()));
    wrap.append(el("label", { for: id }, c.label + (c.unit ? ` (${c.unit})` : "")), input);
  }
  if (tip) wrap.append(tip);   // Node.append(null) wstawiłoby napis "null"
  return wrap;
}

function controls(b, ctx) {
  const pending = {};
  const box = el("div", { class: "controls" + (b.grid ? " grid" : "") });
  for (const c of b.items) {
    box.append(controlInput(c, (v) => {
      pending[c.name] = v;
      if (!b.submit) ctx.onParams({ [c.name]: v });
    }));
  }
  if (b.submit) {
    const btn = el("button", { class: "btn", type: "button" }, b.submit);
    btn.addEventListener("click", () => {
      // pola tekstowe zgłaszają zmianę dopiero po utracie fokusu - zbierz je przed wysłaniem
      if (document.activeElement && box.contains(document.activeElement)) document.activeElement.blur();
      ctx.onParams({ ...pending }, true);
    });
    box.append(btn);
  }
  return box;
}

const KINDS = { statement, metrics, chart, table, note, bullets, section, download, controls };

export function renderBlock(b, ctx) {
  const fn = KINDS[b.kind];
  if (!fn) return el("p", { class: "note warn" }, `Nieznany blok: ${b.kind}`);
  return fn(b, ctx);
}

export function renderScreen(payload, ctx) {
  return el("div", { class: "screen" }, payload.blocks.map((b) => renderBlock(b, ctx)));
}
