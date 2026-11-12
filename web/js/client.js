// Ekrany rysowane w całości po stronie telefonu: logowanie, menu "Więcej", import, tickery, ustawienia.

import * as api from "./api.js";
import { controlInput } from "./blocks.js";
import { el, fmt, fmtDate, richText } from "./format.js";
import { prefs } from "./store.js";

// --------------------------------------------------------------------------- arkusz od dołu

export function openSheet(titleText, build) {
  const backdrop = document.getElementById("sheet-backdrop");
  const sheet = el("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": titleText });
  const close = () => {
    sheet.remove();
    backdrop.hidden = true;
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  sheet.append(el("h2", {}, titleText), build(close));
  backdrop.hidden = false;
  backdrop.onclick = close;
  document.addEventListener("keydown", onKey);
  document.body.append(sheet);
  const first = sheet.querySelector("input, select, button");
  if (first) first.focus({ preventScroll: true });
  return close;
}

function errorNote(err) {
  const lines = String(err.message || err).split("\n").map((s) => s.replace(/^- /, "").trim()).filter(Boolean);
  if (lines.length <= 1) return el("p", { class: "note error", role: "alert" }, lines[0] || "Coś poszło nie tak.");
  return el("div", { class: "note error", role: "alert" }, lines[0], el("ul", { style: "margin:8px 0 0;padding-left:18px" }, lines.slice(1).map((l) => el("li", {}, l))));
}

// --------------------------------------------------------------------------- logowanie

export function loginView(onDone) {
  const input = el("input", { class: "field", type: "password", autocomplete: "current-password", "aria-label": "Hasło", placeholder: "Hasło" });
  const msg = el("div");
  const btn = el("button", { class: "btn", type: "submit" }, "Otwórz aplikację");
  const form = el("form", { class: "login" },
    el("h2", {}, "Podaj hasło"),
    el("p", {}, "Ta instalacja jest zabezpieczona hasłem ustawionym przez właściciela (zmienna APP_PASSWORD na serwerze)."),
    input, btn, msg);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    btn.disabled = true;
    msg.replaceChildren();
    try {
      await api.login(input.value);
      onDone();
    } catch (err) {
      msg.replaceChildren(errorNote(err));
      btn.disabled = false;
      input.select();
    }
  });
  setTimeout(() => input.focus(), 50);
  return form;
}

// --------------------------------------------------------------------------- menu "Więcej"

export function moreView(items, go) {
  const list = el("ul", { class: "menu" });
  for (const it of items) {
    const btn = el("button", { type: "button" }, el("span", {}, el("span", { class: "t" }, it.label), it.hint ? el("span", { class: "h" }, it.hint) : null),
      el("i", { class: "chev", "aria-hidden": "true" }));
    btn.addEventListener("click", () => go(it.id));
    list.append(el("li", {}, btn));
  }
  return el("div", { class: "screen" }, list);
}

// --------------------------------------------------------------------------- import

const EXPORT_STEPS = [
  ["Zaloguj się do xStation 5", " na komputerze (w przeglądarce albo w aplikacji)."],
  ["Otwórz Historię rachunku", " (Account history) - zakładka nad wykresem, obok Edukacji, Kalendarza i Wiadomości."],
  ["Wejdź w Operacje gotówkowe", " i kliknij Eksportuj po prawej stronie."],
  ["Ustaw pełny zakres dat", " - od otwarcia rachunku do dziś - i format XLSX albo CSV."],
  ["Powtórz dla zamkniętych i otwartych pozycji", ", jeśli xStation zapisuje każdy raport w osobnym pliku. Nowy eksport ma wszystko w jednym pliku .xlsx."],
  ["Masz kilka rachunków?", " Przełącz rachunek w xStation (np. z PLN na USD) i wyeksportuj każdy osobno."],
  ["Wgraj wszystkie pliki", " - tutaj, z komputera albo z telefonu (Pliki / Pobrane). Aplikacja sama rozpozna rachunki i tabele."],
];

// --------------------------------------------------------------------------- rachunki i waluta wyświetlania

function accountLine(a) {
  const n = (x, one, few, many) => `${x} ${x === 1 ? one : (x % 10 >= 2 && x % 10 <= 4 && (x % 100 < 12 || x % 100 > 14)) ? few : many}`;
  return `${n(a.open, "otwarta pozycja", "otwarte pozycje", "otwartych pozycji")}, ${n(a.closed, "zamknięta", "zamknięte", "zamkniętych")}`;
}

/** Krótki opis wybranego okresu analizy, np. "12 mies." albo "01.06.2025-31.03.2026". */
export function periodLabel(s) {
  const short = { ytd: "od początku roku", "12m": "12 mies.", "24m": "24 mies.", "36m": "3 lata" };
  const d = (iso) => fmtDate(iso);
  if (s.until) return `${s.since_date ? d(s.since_date) : "start"}-${d(s.until)}`;
  if (!s.since) return "";
  return short[s.since] || `od ${d(s.since_date || s.since)}`;
}

/** Arkusz z paska tytułu: rachunki, waluta, CFD i okres analizy - jeden widok dla wszystkich ekranów. */
export function viewSheet(reloadAll, opts = {}) {
  const s = api.state.session;
  openSheet("Widok portfela", (close) => {
    let base = s.base;
    let excludeCfd = !!s.exclude_cfd;
    const isPreset = (v) => s.since_choices.some((o) => o.value === v);
    let period = opts.custom || s.until || (s.since && !isPreset(s.since)) ? "custom" : (s.since || "");
    const iso = (v) => (v ? String(v).slice(0, 10) : "");
    const picked = new Set(s.accounts.filter((a) => a.selected).map((a) => a.id));
    const status = el("div");
    const controls = el("div", { class: "controls" });
    if (s.accounts.length > 1) {
      controls.append(el("span", { class: "control-label" }, "Rachunki wliczane do portfela"));
      s.accounts.forEach((a) => controls.append(controlInput(
        { name: `acc-${a.id}`, type: "toggle", label: `${a.id} · ${a.currency}`, value: a.selected, tip: accountLine(a) },
        (on) => { if (on) picked.add(a.id); else picked.delete(a.id); })));
    }
    controls.append(controlInput({ name: "base", type: "segmented", label: "Waluta wyświetlania", value: base,
      options: s.base_choices.map((c) => ({ value: c, label: c })),
      tip: "Wszystkie kwoty, stopy zwrotu i ryzyko są przeliczane na tę walutę po kursach NBP. Zakładka Podatki zostaje w złotych." },
    (v) => { base = v; }));
    if (s.has_cfd) {
      controls.append(controlInput({ name: "exclude_cfd", type: "toggle", label: "Pomiń CFD (tylko akcje i ETF-y)", value: excludeCfd,
        tip: "Pozycje CFD znikają z analiz, a ich wynik jest traktowany jak wpłata lub wypłata - nie zmienia stóp zwrotu ani ryzyka. Podatki dalej liczą wszystko." },
      (on) => { excludeCfd = on; }));
    }
    const from = el("input", { class: "field", type: "date", id: "view-from", value: iso(s.since_date) || iso(s.first_date), min: iso(s.first_date), max: iso(s.last_date) });
    const to = el("input", { class: "field", type: "date", id: "view-to", value: s.until || iso(s.last_date), min: iso(s.first_date), max: iso(s.last_date) });
    const range = el("div", { class: "date-range", hidden: period !== "custom" ? "" : null },
      el("label", { for: "view-from" }, "Od", from), el("label", { for: "view-to" }, "Do", to));
    controls.append(controlInput({ name: "period", type: "select", label: "Okres analizy", value: period,
      options: [...s.since_choices, { value: "custom", label: "Własny zakres dat" }],
      tip: "Dotyczy wszystkich ekranów. Stan portfela z dnia przed początkiem okresu jest kapitałem startowym, więc wcześniejsza historia (np. z czasów małego rachunku) nie wpływa na stopy zwrotu ani obsunięcia." },
    (v) => { period = v; range.hidden = v !== "custom"; }), range);
    const apply = el("button", { class: "btn", type: "button" }, "Zastosuj");
    apply.addEventListener("click", async () => {
      if (!picked.size) { status.replaceChildren(el("p", { class: "note warn" }, "Zaznacz przynajmniej jeden rachunek.")); return; }
      const custom = period === "custom";
      if (custom && from.value && to.value && from.value >= to.value) {
        status.replaceChildren(el("p", { class: "note warn" }, "Data „Od” musi być wcześniejsza niż „Do”."));
        return;
      }
      apply.disabled = true;
      apply.textContent = "Przeliczam…";
      try {
        await api.setView({ base, accounts: [...picked], exclude_cfd: excludeCfd,
          since: custom ? (from.value > iso(s.first_date) ? from.value : "") : period,
          until: custom && to.value < iso(s.last_date) ? to.value : "" });
        close();
        reloadAll();
      } catch (err) {
        status.replaceChildren(errorNote(err));
        apply.disabled = false;
        apply.textContent = "Zastosuj";
      }
    });
    const body = el("div", {}, controls, apply, status);
    if (s.accounts.length === 1 && !s.is_demo) {
      body.append(el("p", { class: "caption", style: "margin-top:14px" },
        "Masz drugi rachunek (np. w USD)? Wgraj jego eksport w „Więcej → Dane i ustawienia” - pojawi się tutaj do zaznaczenia."));
    }
    return body;
  });
}

export function importView(rerender, reloadAll) {
  const s = api.state.session;
  const isDemo = s.is_demo;
  const root = el("div", { class: "screen" });
  const status = el("div");

  root.append(el("section", { class: "statement" },
    el("p", {}, isDemo ? "Oglądasz dane przykładowe." : "Analizujesz własny eksport z XTB."),
    el("p", { class: "sub" }, isDemo
      ? "To zmyślony portfel i syntetyczne notowania, żeby było widać, co aplikacja potrafi. Wgraj swój eksport z xStation 5, żeby zobaczyć własne liczby."
      : "Plik jest zapisany w tym telefonie. Na serwer trafia tylko na czas obliczeń i nie jest tam zapisywany na dysk.")));

  const input = el("input", { type: "file", accept: ".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv", multiple: true });
  const drop = el("label", { class: "drop" }, el("strong", {}, isDemo ? "Wgraj eksport z xStation 5" : "Dograj rachunek albo nowszy eksport"),
    isDemo ? "Plik .xlsx z xStation. Masz rachunek w PLN i w USD? Zaznacz oba pliki naraz."
      : "Nowy plik dodaje się do już wgranych. Nowszy eksport tego samego rachunku zastępuje starszy.", input);
  input.addEventListener("change", async () => {
    if (!input.files.length) return;
    status.replaceChildren(el("p", { class: "note" }, el("span", { class: "pulse" }), "Czytam plik i pobieram notowania. Przy pierwszym razie może to potrwać do minuty."));
    try {
      await api.uploadFiles(input.files);
      reloadAll();
    } catch (err) {
      status.replaceChildren(errorNote(err));
      input.value = "";
    }
  });
  root.append(drop, status);

  if (!isDemo) {
    const demoBtn = el("button", { class: "btn quiet", type: "button" }, "Wróć do danych przykładowych");
    demoBtn.addEventListener("click", async () => {
      demoBtn.disabled = true;
      try { await api.setMode("demo"); reloadAll(); } catch (err) { status.replaceChildren(errorNote(err)); demoBtn.disabled = false; }
    });
    root.append(demoBtn);
  }

  if (!isDemo && s.accounts.length) {
    const list = el("ul", { class: "ledger" }, s.accounts.map((a) => {
      const rm = el("button", { class: "link-btn", type: "button", "aria-label": `Usuń rachunek ${a.id} z telefonu` }, "Usuń");
      let armed = false;
      rm.addEventListener("click", async () => {
        if (!armed) { armed = true; rm.textContent = "Na pewno?"; return; }
        rm.disabled = true;
        try { await api.removeAccount(a.id); reloadAll(); } catch (err) { status.replaceChildren(errorNote(err)); rm.disabled = false; }
      });
      return el("li", {}, el("div", { class: "row" },
        el("span", { class: "label stack" }, `${a.id} · ${a.currency}`, el("small", {}, `${a.selected ? "" : "odznaczony · "}${accountLine(a)}`)), rm));
    }));
    const pick = el("button", { class: "btn quiet", type: "button" }, s.accounts.length > 1 ? "Wybierz rachunki i walutę" : "Zmień walutę wyświetlania");
    pick.addEventListener("click", () => viewSheet(reloadAll));
    root.append(el("section", {}, el("h2", { class: "block-title" }, s.accounts.length > 1 ? "Twoje rachunki" : "Twój rachunek"), list, pick));
  }

  const sum = s.summary;
  const rows = [["Zamknięte pozycje", sum["zamknięte pozycje"]], ["Otwarte pozycje", sum["otwarte pozycje"]],
    ["Operacje gotówkowe", sum["operacje gotówkowe"]], ["Instrumenty", sum["instrumenty"]]];
  const ledger = el("ul", { class: "ledger" }, rows.map(([k, v]) => el("li", {}, el("div", { class: "row" },
    el("span", { class: "label" }, k), el("span", { class: "value" }, fmt(v, "int"))))));
  ledger.append(el("li", {}, el("div", { class: "row" }, el("span", { class: "label" }, "Stan na"), el("span", { class: "value" }, fmtDate(s.as_of)))),
    el("li", {}, el("div", { class: "row" }, el("span", { class: "label" }, "Notowania"), el("span", { class: "value", style: "font-size:14px;font-weight:500;white-space:normal" }, s.source))));
  root.append(el("section", {}, el("h2", { class: "block-title" }, "Co wczytano"), ledger));

  if (s.warnings.length) {
    root.append(el("section", {}, el("h2", { class: "block-title" }, "Uwagi do danych"),
      el("ul", { class: "bullets" }, s.warnings.map((w) => el("li", {}, w)))));
  }
  if (s.unmapped.length) {
    root.append(el("p", { class: "note warn" }, `Brak tickera dla: ${s.unmapped.join(", ")}. Uzupełnij go w zakładce Tickery, inaczej pozycje będą wyceniane po cenie zakupu.`));
  }

  const how = el("details", { class: "section" }, el("summary", {}, "Jak wyeksportować pliki z xStation 5"),
    el("div", { class: "section-body" }, el("ol", { class: "steps" }, EXPORT_STEPS.map(([b, rest]) => el("li", {}, el("strong", {}, b), rest))),
      el("p", { class: "caption" }, "Im pełniejsza historia operacji gotówkowych, tym dokładniejsze stopy zwrotu i podatki.")));
  if (isDemo) how.setAttribute("open", "");
  const found = el("details", { class: "section" }, el("summary", {}, "Rozpoznane tabele"),
    el("div", { class: "section-body" }, el("ul", { class: "bullets" }, s.notes.map((n) => el("li", {}, n)))));
  root.append(el("div", {}, how, found));
  return root;
}

// --------------------------------------------------------------------------- tickery (mapowanie)

const MAP_FIELDS = [
  { name: "name", type: "text", label: "Nazwa" },
  { name: "yf_ticker", type: "text", label: "Ticker Yahoo Finance", tip: "Np. PKN.WA, AAPL, ^GSPC. Kilku kandydatów oddziel znakiem |." },
  { name: "stooq_ticker", type: "text", label: "Ticker Stooq (źródło zapasowe)", tip: "Np. pkn, aapl.us, ^spx." },
  { name: "asset_class", type: "select", label: "Klasa", options: [{ value: "STOCK", label: "Akcje" }, { value: "ETF", label: "ETF" }, { value: "CFD", label: "CFD" }] },
  { name: "currency", type: "text", label: "Waluta notowań", upper: true, tip: "Dla CFD: waluta, w której liczony jest wynik." },
  { name: "sector", type: "text", label: "Sektor" },
  { name: "country", type: "text", label: "Kraj" },
  { name: "multiplier", type: "number", label: "Mnożnik CFD", tip: "Wartość 1 punktu na 1 lot, np. US500 = 50. Dla akcji 1." },
  { name: "margin_rate", type: "number", label: "Depozyt CFD (ułamek nominału)", tip: "0.05 = 5%. Dla akcji 1." },
  { name: "price_factor", type: "number", label: "Mnożnik ceny ze źródła", tip: "0.01, gdy źródło podaje pensy zamiast funtów." },
  { name: "spread_bps", type: "number", label: "Szacowany spread (punkty bazowe)", tip: "1 pb = 0,01%. Używane w rozbiciu kosztów." },
];

export function mappingView(reloadAll) {
  const s = api.state.session;
  const root = el("div", { class: "screen" });
  const status = el("div");
  root.append(el("section", { class: "statement" },
    el("p", {}, "Symbole z XTB są tłumaczone na tickery, po których pobieram notowania."),
    el("p", { class: "sub" }, "Jeśli wycena pozycji się nie zgadza albo brakuje sektora, dotknij wiersza i popraw.")), status);

  const save = async (rows, btn) => {
    if (btn) btn.disabled = true;
    status.replaceChildren(el("p", { class: "note" }, el("span", { class: "pulse" }), "Przeliczam portfel…"));
    try {
      await api.saveMapping(rows);
      reloadAll();
    } catch (err) {
      status.replaceChildren(errorNote(err));
      if (btn) btn.disabled = false;
    }
  };

  const list = el("ul", { class: "ledger" });
  for (const row of s.mapping) {
    const bad = s.unmapped.includes(row.symbol);
    const btn = el("button", { class: "map-row" + (bad ? " bad" : ""), type: "button" },
      el("span", { class: "sym" }, row.symbol), el("span", { class: "tk" }, bad ? "brak tickera" : (row.yf_ticker || row.stooq_ticker)),
      el("span", { class: "nm" }, `${row.name}, ${row.sector}, ${row.country}`));
    btn.addEventListener("click", () => openSheet(row.symbol, (close) => {
      const draft = { ...row };
      const box = el("div", { class: "controls" }, MAP_FIELDS.map((f) => controlInput({ ...f, value: row[f.name] }, (v) => { draft[f.name] = v; })));
      const ok = el("button", { class: "btn", type: "button" }, "Zapisz i przelicz");
      ok.addEventListener("click", () => {
        if (document.activeElement) document.activeElement.blur();
        const others = prefs.get(`overrides:${api.state.mode}`, []).filter((r) => r.symbol !== row.symbol);
        close();
        save([...others, draft], null);
      });
      const cancel = el("button", { class: "btn quiet", type: "button" }, "Anuluj");
      cancel.addEventListener("click", close);
      return el("div", {}, box, el("div", { class: "btn-row" }, ok, cancel));
    }));
    list.append(el("li", {}, btn));
  }
  root.append(list);
  const reset = el("button", { class: "btn quiet", type: "button" }, "Przywróć automatyczne tickery");
  reset.addEventListener("click", () => save([], reset));
  root.append(reset);
  return root;
}

// --------------------------------------------------------------------------- ustawienia

export function settingsView(reloadAll, showLogin) {
  const cur = api.settings();
  const health = api.state.health;
  const root = el("div", { class: "screen" });
  const status = el("div");
  root.append(el("div", { class: "controls", style: "margin-top:14px" },
    controlInput({ name: "bench", type: "select", label: "Benchmark do bety i alfy", value: cur.bench, options: health.settings.bench_choices,
      tip: "Indeks, do którego porównuję portfel przy liczeniu bety, alfy i tracking error." }, (v) => api.saveSettings({ bench: v })),
    controlInput({ name: "rf", type: "number", label: "Stopa wolna od ryzyka", unit: "% rocznie", value: String(+(cur.rf * 100).toFixed(2)).replace(".", ","),
      tip: "Oprocentowanie bezpiecznej lokaty lub obligacji skarbowych. Używane w Sharpe, Sortino i alfie." },
    (v) => { const x = parseFloat(v); if (Number.isFinite(x) && x >= 0 && x <= 25) api.saveSettings({ rf: x / 100 }); })));

  const install = el("details", { class: "section" }, el("summary", {}, "Dodaj aplikację do ekranu głównego"),
    el("div", { class: "section-body" }, el("ul", { class: "bullets" },
      el("li", {}, el("strong", {}, "iPhone (Safari): "), "dotknij Udostępnij, potem „Do ekranu początkowego”."),
      el("li", {}, el("strong", {}, "Android (Chrome): "), "otwórz menu z trzema kropkami i wybierz „Zainstaluj aplikację” lub „Dodaj do ekranu głównego”.")),
      el("p", { class: "caption" }, "Po dodaniu aplikacja otwiera się na pełnym ekranie, bez paska przeglądarki.")));
  const about = el("details", { class: "section" }, el("summary", {}, "O aplikacji"),
    el("div", { class: "section-body" }, el("ul", { class: "bullets" },
      el("li", {}, "Aplikacja jest wyłącznie analityczna. Nie łączy się z kontem XTB i nie daje rekomendacji inwestycyjnych."),
      el("li", {}, "Eksport z XTB jest zapisany w tym telefonie. Na serwerze żyje tylko w pamięci, na czas obliczeń."),
      el("li", {}, `Wersja ${health.version}. Dział AI: ${health.ai_enabled ? "włączony" : "wyłączony (brak klucza API na serwerze)"}.`))));
  root.append(el("div", {}, install, about));

  const forget = el("button", { class: "btn danger", type: "button" }, "Usuń moje dane z tego telefonu");
  let armed = false;
  forget.addEventListener("click", async () => {
    if (!armed) { armed = true; forget.textContent = "Dotknij ponownie, żeby potwierdzić"; return; }
    forget.disabled = true;
    try { await api.forgetDevice(); reloadAll(); } catch (err) { status.replaceChildren(errorNote(err)); forget.disabled = false; }
  });
  const buttons = el("div", { class: "btn-row" }, forget);
  if (health.auth_required) {
    const out = el("button", { class: "btn quiet", type: "button" }, "Wyloguj");
    out.addEventListener("click", () => { api.logout(); showLogin(); });
    buttons.append(out);
  }
  root.append(buttons, status);
  return root;
}

// --------------------------------------------------------------------------- AI: memo ryzyka i czat

/** Prosty, bezpieczny markdown: nagłówki "## ", wypunktowania "- ", **pogrubienia**. Bez innerHTML. */
function markdownLite(text) {
  const box = el("div", { class: "memo" });
  let list = null;
  for (const raw of String(text).split("\n")) {
    const line = raw.trim();
    if (!line) { list = null; continue; }
    const h = line.match(/^#{1,4}\s+(.*)$/);
    const li = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (h) { list = null; box.append(el("h3", {}, h[1].replace(/\*\*/g, ""))); }
    else if (li) {
      if (!list) { list = el("ul", { class: "bullets" }); box.append(list); }
      list.append(richText(li[1], el("li")));
    } else { list = null; box.append(richText(line, el("p"))); }
  }
  return box;
}

const chatHistory = [];   // rozmowa żyje do odświeżenia strony albo zmiany danych

export function aiView() {
  const cfg = () => api.settings();
  const root = el("div", { class: "screen" });
  root.append(el("section", { class: "statement" },
    el("p", {}, "Memo ryzyka pisze model Claude na podstawie Twoich metryk."),
    el("p", { class: "sub" }, "Do modelu trafiają tylko liczby zbiorcze i nazwy najważniejszych pozycji. Bez imienia i nazwiska, numeru rachunku i listy transakcji.")));

  const memoOut = el("div");
  const memoBtn = el("button", { class: "btn", type: "button" }, "Napisz memo ryzyka");
  memoBtn.addEventListener("click", async () => {
    memoBtn.disabled = true;
    memoOut.replaceChildren(el("p", { class: "note" }, el("span", { class: "pulse" }), "Model pisze memo. Zwykle trwa to kilkanaście sekund."));
    try {
      const out = await api.post("ai/memo", { rf: cfg().rf, bench: cfg().bench });
      memoOut.replaceChildren(markdownLite(out.text), el("p", { class: "caption" }, `Model: ${out.model}. Memo opisuje liczby, nie jest rekomendacją inwestycyjną.`));
      memoBtn.textContent = "Napisz memo jeszcze raz";
    } catch (err) {
      memoOut.replaceChildren(errorNote(err));
    } finally {
      memoBtn.disabled = false;
    }
  });
  root.append(memoBtn, memoOut);

  const pre = el("pre", { class: "json" }, "Ładuję…");
  const sent = el("details", { class: "section" }, el("summary", {}, "Co dokładnie zostanie wysłane"), el("div", { class: "section-body" }, pre));
  sent.addEventListener("toggle", async () => {
    if (!sent.open || sent.dataset.loaded) return;
    try {
      const out = await api.get(`ai/payload?rf=${cfg().rf}&bench=${cfg().bench}`);
      pre.textContent = JSON.stringify(out.payload, null, 2);
      sent.dataset.loaded = "1";
    } catch (err) { pre.textContent = err.message; }
  });
  root.append(sent);

  const log = el("div", { class: "chat-log", "aria-live": "polite" });
  const draw = () => log.replaceChildren(...chatHistory.map((m) =>
    el("div", { class: `msg ${m.role}` }, m.role === "assistant" ? markdownLite(m.content) : m.content)));
  draw();
  const input = el("input", { class: "field", type: "text", placeholder: "np. Co najbardziej zwiększa moje ryzyko?", "aria-label": "Pytanie o portfel",
    maxlength: "2000", autocomplete: "off" });
  const send = el("button", { class: "btn", type: "submit" }, "Zapytaj");
  const status = el("div");
  const form = el("form", { class: "chat-form" }, input, send);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    chatHistory.push({ role: "user", content: q });
    input.value = "";
    draw();
    send.disabled = true;
    status.replaceChildren(el("p", { class: "caption" }, el("span", { class: "pulse" }), "Model odpowiada…"));
    try {
      const out = await api.post("ai/chat", { messages: chatHistory, rf: cfg().rf, bench: cfg().bench });
      chatHistory.push({ role: "assistant", content: out.text });
      status.replaceChildren();
    } catch (err) {
      chatHistory.pop();
      input.value = q;
      status.replaceChildren(errorNote(err));
    } finally {
      send.disabled = false;
      draw();
    }
  });
  root.append(el("section", {}, el("h2", { class: "block-title" }, "Zapytaj o swój portfel"),
    el("p", { class: "caption", style: "margin:0 0 12px" }, "Odpowiedzi opierają się wyłącznie na metrykach policzonych w aplikacji."), log, status, form));
  return root;
}

export function resetChat() {
  chatHistory.length = 0;
}

export { errorNote };
