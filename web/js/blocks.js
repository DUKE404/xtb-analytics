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

/** Klucz sortowania komórki: liczba, data "dd.mm.rrrr" albo "dd.mm" w tekście, liczba na początku tekstu, tekst. */
export function sortKey(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  if (!s || s === "–") return null;
  const d = s.match(/(\d{2})\.(\d{2})\.(\d{4})/);
  if (d) return Number(`${d[3]}${d[2]}${d[1]}`);
  const n = s.match(/^[+−-]?\d[\d\s\u00a0]*(,\d+)?/);
  if (n) return parseFloat(n[0].replace(/[\s\u00a0]/g, "").replace("−", "-").replace(",", "."));
  return s.toLocaleLowerCase("pl");
}

function compare(a, b) {
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;        // puste zawsze na końcu
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "number") return -1;
  if (typeof b === "number") return 1;
  return a.localeCompare(b, "pl");
}

/** Tabela jako CSV dla Excela po polsku: średnik, przecinek dziesiętny, procenty jako liczby. */
export function tableCsv(b) {
  const cell = (v, c) => {
    if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "";
    if (typeof v === "number") {
      const n = c.fmt === "pct" ? Math.round(v * 1e6) / 1e4 : ["pln", "zl"].includes(c.fmt) ? Math.round(v * 100) / 100 : Math.round(v * 1e6) / 1e6;
      return String(n).replace(".", ",");
    }
    const s = String(v).replace(/\u00a0/g, " ");
    return /[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = b.columns.map((c) => cell(c.label + (c.fmt === "pct" ? " (%)" : ""), {}));
  return [head, ...b.rows.map((r) => b.columns.map((c) => cell(r[c.key], c)))].map((r) => r.join(";")).join("\r\n");
}

function saveCsv(b) {
  const name = (b.title || "tabela").toLocaleLowerCase("pl").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/ł/g, "l")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "tabela";
  const url = URL.createObjectURL(new Blob(["\ufeff" + tableCsv(b)], { type: "text/csv;charset=utf-8" }));
  const a = el("a", { href: url, download: `${name}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

/** Tabela: sortowanie po kliknięciu nagłówka (malejąco → rosnąco → kolejność wyjściowa) i eksport do CSV. */
function table(b) {
  const numeric = (c) => c.fmt !== "text";
  const body = el("tbody");
  const limit = b.limit || 12;
  let all = b.rows.length <= limit;
  let sort = null;                       // { key, dir } albo null = kolejność z serwera
  const ordered = () => {
    if (!sort) return b.rows;
    const keyed = b.rows.map((r, i) => [sortKey(r[sort.key]), i, r]);
    keyed.sort((x, y) => (x[0] === null || y[0] === null ? compare(x[0], y[0]) : sort.dir * compare(x[0], y[0])) || x[1] - y[1]);
    return keyed.map((k) => k[2]);
  };
  const draw = () => {
    const rows = all ? ordered() : ordered().slice(0, limit);
    body.replaceChildren(...rows.map((r) => el("tr", {}, b.columns.map((c, i) =>
      el("td", { class: (!numeric(c) && i > 0 ? "t" : "") + tone(r[c.key], c.fmt, c.signed) || null }, fmt(r[c.key], c.fmt, c.digits, c.signed))))));
  };
  const heads = b.columns.map((c) => {
    const btn = el("button", { type: "button", class: "th-sort" }, c.label);
    const th = el("th", { class: numeric(c) ? null : "t", scope: "col", "aria-sort": "none" }, btn);
    btn.addEventListener("click", () => {
      const first = numeric(c) ? -1 : 1;                 // liczby od największej, tekst od A
      sort = !sort || sort.key !== c.key ? { key: c.key, dir: first } : sort.dir === first ? { key: c.key, dir: -first } : null;
      heads.forEach((h) => h.setAttribute("aria-sort", "none"));
      if (sort) th.setAttribute("aria-sort", sort.dir > 0 ? "ascending" : "descending");
      draw();
    });
    return th;
  });
  draw();
  const wrap = el("div", { class: "table-wrap" }, el("table", { class: "data" }, el("thead", {}, el("tr", {}, heads)), body));
  const out = el("section", {}, title(b.title), b.rows.length ? wrap : el("p", { class: "caption" }, "Brak wierszy."));
  const tools = el("div", { class: "table-tools" });
  if (!all) {
    const more = el("button", { class: "more-rows", type: "button" }, `Pokaż wszystkie (${b.rows.length})`);
    more.addEventListener("click", () => { all = true; draw(); more.remove(); });
    tools.append(more);
  }
  if (b.rows.length > 1) {
    const csv = el("button", { class: "csv-btn", type: "button", title: "Zapisz tabelę jako plik CSV (otworzy się w Excelu)" }, "Pobierz CSV");
    csv.addEventListener("click", () => saveCsv(b));
    tools.append(csv);
  }
  if (tools.children.length) out.append(tools);
  if (b.caption) out.append(el("p", { class: "caption" }, b.caption));
  return out;
}

function note(b) {
  return el("p", { class: "note" + (b.tone && b.tone !== "info" ? " " + b.tone : "") }, b.text);
}

/** Kiedy: "dziś 08:15", "wczoraj 22:10" albo "09.11, 14:30" - w czasie telefonu. */
function when(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const hm = d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(d)) / 86400000);
  if (diff === 0) return `dziś ${hm}`;
  if (diff === 1) return `wczoraj ${hm}`;
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}, ${hm}`;
}

/** Nagłówki wiadomości. Tytuł jest odnośnikiem do wydawcy (nowa karta); treść wstawiana wyłącznie jako tekst. */
function feed(b) {
  const items = b.items.map((it) => {
    const safe = typeof it.url === "string" && /^https?:\/\//i.test(it.url);
    const head = safe ? el("a", { class: "feed-title", href: it.url, target: "_blank", rel: "noopener noreferrer" }, it.title)
      : el("span", { class: "feed-title" }, it.title);
    const meta = [it.source, it.time ? when(it.time) : null].filter(Boolean).join(" · ");
    return el("li", {}, it.tag ? el("span", { class: "feed-tag" }, it.tag) : null, head, meta ? el("small", {}, meta) : null);
  });
  const out = el("section", {}, title(b.title), items.length ? el("ul", { class: "feed" }, items) : el("p", { class: "caption" }, "Brak wiadomości."));
  if (b.caption) out.append(el("p", { class: "caption" }, b.caption));
  return out;
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

const KINDS = { statement, metrics, chart, table, note, bullets, feed, section, download, controls };

export function renderBlock(b, ctx) {
  const fn = KINDS[b.kind];
  if (!fn) return el("p", { class: "note warn" }, `Nieznany blok: ${b.kind}`);
  return fn(b, ctx);
}

export function renderScreen(payload, ctx) {
  return el("div", { class: "screen" }, payload.blocks.map((b) => renderBlock(b, ctx)));
}
