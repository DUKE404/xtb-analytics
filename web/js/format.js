// Formatowanie liczb i dat po polsku.

const NBSP = " ";
function nf(d) {
  const base = { minimumFractionDigits: d, maximumFractionDigits: d };
  try {
    // "always": polski format domyślnie nie grupuje liczb czterocyfrowych (1234), a w tabeli chcemy 1 234
    return new Intl.NumberFormat("pl-PL", { ...base, useGrouping: "always" });
  } catch {
    return new Intl.NumberFormat("pl-PL", base);
  }
}
const cache = new Map();

// Waluta wyświetlania: format "pln" z serwera oznacza "kwotę w walucie wybranej przez użytkownika",
// format "zl" - zawsze złote (podatki).
const SIGNS = { PLN: "zł", USD: "$", EUR: "€" };
let sign = SIGNS.PLN;
export function setCurrency(code) { sign = SIGNS[code] || SIGNS.PLN; }
export function currencySign() { return sign; }

function fixed(x, digits) {
  if (!cache.has(digits)) cache.set(digits, nf(digits));
  // Intl daje zwykły minus z łącznikiem; typograficzny minus czyta się lepiej w kolumnie liczb
  return cache.get(digits).format(x).replace("-", "−");
}

export function isNum(x) {
  return typeof x === "number" && Number.isFinite(x);
}

/** value + nazwa formatu z serwera -> tekst. sign=true dodaje plus przy wartościach dodatnich. */
export function fmt(value, kind = "num", digits = null, plus = false) {
  if (kind === "text") return value === null || value === undefined || value === "" ? "–" : String(value);
  if (!isNum(value)) return "–";
  let out;
  switch (kind) {
    case "pln": out = fixed(value, digits ?? 0) + NBSP + sign; break;
    case "zl": out = fixed(value, digits ?? 0) + NBSP + SIGNS.PLN; break;
    case "pct": out = fixed(value * 100, digits ?? 1) + "%"; break;
    case "int": out = fixed(value, 0); break;
    case "days": { const n = Math.round(value); out = fixed(n, 0) + NBSP + (n === 1 ? "dzień" : "dni"); break; }
    case "x": out = fixed(value, digits ?? 2) + "×"; break;
    default: out = fixed(value, digits ?? 2);
  }
  return plus && value > 0 ? "+" + out : out;
}

/** Krótki zapis na osie wykresów: 150 tys., 1,2 mln. */
export function fmtAxis(value, kind = "num", digits = null) {
  if (!isNum(value)) return "";
  if (kind === "pct") {
    const p = value * 100;
    return fixed(p, digits ?? (Math.abs(p - Math.round(p)) < 1e-9 ? 0 : 1)) + "%";
  }
  const a = Math.abs(value);
  let s;
  if (a >= 1e6) s = fixed(value / 1e6, a >= 1e7 ? 0 : 1) + NBSP + "mln";
  else if (a >= 1e4) s = fixed(value / 1e3, 0) + NBSP + "tys.";
  else if (a >= 100 || Number.isInteger(value)) s = fixed(value, 0);
  else s = fixed(value, digits ?? (a < 1 ? 2 : 1));
  return kind === "pln" ? s + NBSP + sign : kind === "zl" ? s + NBSP + SIGNS.PLN : s;
}

const MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];

export function fmtDate(iso, style = "full") {
  if (!iso || iso.length < 10) return iso || "";
  const [y, m, d] = [iso.slice(0, 4), +iso.slice(5, 7), iso.slice(8, 10)];
  if (style === "axis") return `${MONTHS[m - 1]} ${y.slice(2)}`;
  return `${d}.${String(m).padStart(2, "0")}.${y}`;
}

/** "ładne" podziałki osi: ok. `count` wartości obejmujących [min, max]. */
export function niceTicks(min, max, count = 4) {
  if (!(max > min)) { const p = Math.abs(min) * 0.1 || 1; min -= p; max += p; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step * 1e-9; v += step) ticks.push(+v.toPrecision(12));
  return { lo, hi, step, ticks };
}

/** Bezpieczne **pogrubienie**: tekst z serwera -> węzły DOM (nigdy innerHTML). */
export function richText(text, parent) {
  String(text ?? "").split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      const b = document.createElement("strong");
      b.textContent = part.slice(2, -2);
      parent.append(b);
    } else if (part) parent.append(document.createTextNode(part));
  });
  return parent;
}

/** Skrót do tworzenia elementów: el("div", {class: "x"}, "tekst", dziecko). */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}
