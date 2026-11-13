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

const MIN_PASSWORD = 10;

function field(label, attrs) {
  const input = el("input", { class: "field", ...attrs });
  return { input, row: el("label", { class: "login-field" }, el("span", {}, label), input) };
}

/** Logowanie i zakładanie konta. Hasło nie opuszcza telefonu - z niego powstają klucze (vault.js). */
export function loginView(onDone) {
  const root = el("div", { class: "login" });
  const render = async (mode) => {
    let status;
    try { status = await api.authStatus(); } catch (err) { root.replaceChildren(errorNote(err)); return; }
    const first = !status.has_users;
    mode = first ? "register" : mode;
    const user = field("Nazwa użytkownika", { type: "text", autocomplete: "username", autocapitalize: "none", spellcheck: "false",
      inputmode: "email", maxlength: "32", required: "" });
    const pass = field("Hasło", { type: "password", autocomplete: mode === "login" ? "current-password" : "new-password", required: "" });
    const parts = [user.row, pass.row];
    let repeat = null, code = null;
    if (mode === "register") {
      repeat = field("Powtórz hasło", { type: "password", autocomplete: "new-password", required: "" });
      parts.push(repeat.row);
      if (!first || status.setup_code_required) {
        code = field(first ? "Kod konfiguracji" : "Kod zaproszenia", { type: "text", autocomplete: "off", autocapitalize: "characters",
          spellcheck: "false", required: "", placeholder: first ? "hasło aplikacji z Render (APP_PASSWORD)" : "np. ABCD-EFGH-JKLM" });
        parts.push(code.row);
      }
    }
    const msg = el("div", { class: "login-msg", role: "alert" });
    const btn = el("button", { class: "btn", type: "submit" }, mode === "login" ? "Zaloguj się" : "Załóż konto");
    const head = first
      ? [el("h2", {}, "Załóż pierwsze konto"), el("p", {}, "To konto będzie kontem administratora: z niego wygenerujesz kody zaproszeń dla innych osób.")]
      : mode === "login" ? [el("h2", {}, "Zaloguj się")]
        : [el("h2", {}, "Nowe konto"), el("p", {}, "Kod zaproszenia dostaniesz od administratora tej instalacji. Jest jednorazowy i ważny 7 dni.")];
    const notes = [];
    if (mode === "register") {
      notes.push(el("p", { class: "note warn" }, "Hasła nie da się odzyskać. Pliki z xStation są szyfrowane kluczem z Twojego hasła, "
        + "więc nikt - także administrator - nie przywróci do nich dostępu. Po utracie hasła załóż konto od nowa i wgraj eksporty ponownie."));
    }
    if (!status.persistent) {
      notes.push(el("p", { class: "note warn" }, `Baza kont jest tymczasowa (${status.storage}): po restarcie serwera konta i zapisane portfele znikną. `
        + "Administrator powinien ustawić DATABASE_URL (darmowa baza Neon) - instrukcja w README."));
    }
    const form = el("form", { class: "login-form", novalidate: "" }, ...head, ...parts, btn, msg, ...notes);
    if (!first) {
      const swap = el("button", { class: "link-btn", type: "button" }, mode === "login" ? "Mam kod zaproszenia - załóż konto" : "Mam już konto - zaloguj się");
      swap.addEventListener("click", () => render(mode === "login" ? "register" : "login"));
      form.append(swap);
    }
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.replaceChildren();
      const fail = (text) => msg.replaceChildren(el("p", { class: "note warn" }, text));
      const name = user.input.value.trim().toLowerCase();
      if (!/^[a-z0-9][a-z0-9._-]{2,31}$/.test(name)) return fail("Nazwa użytkownika: 3-32 znaki, małe litery, cyfry, kropka, myślnik lub podkreślnik.");
      if (mode === "register") {
        if (pass.input.value.length < MIN_PASSWORD) return fail(`Hasło musi mieć co najmniej ${MIN_PASSWORD} znaków. Najlepiej kilka słów.`);
        if (pass.input.value !== repeat.input.value) return fail("Hasła się różnią.");
        if (code && !code.input.value.trim()) return fail(first ? "Podaj kod konfiguracji." : "Podaj kod zaproszenia.");
      }
      btn.disabled = true;
      btn.textContent = mode === "login" ? "Loguję…" : "Zakładam konto…";
      try {
        const moved = mode === "login" ? await api.login(name, pass.input.value)
          : await api.register(name, pass.input.value, code ? code.input.value : "");
        pass.input.value = "";
        if (repeat) repeat.input.value = "";
        onDone({ moved });
      } catch (err) {
        msg.replaceChildren(errorNote(err));
        btn.disabled = false;
        btn.textContent = mode === "login" ? "Zaloguj się" : "Załóż konto";
        pass.input.select();
      }
    });
    root.replaceChildren(form);
    // kursor w polu nazwy od razu, bez opóźnienia: opóźnione focus() zabierało fokus z pola hasła
    // (menedżer haseł albo szybkie pisanie wpisywały wtedy hasło do pola nazwy)
    if (root.isConnected) user.input.focus();
  };
  render("login");
  return root;
}

// --------------------------------------------------------------------------- menu "Więcej"

export function moreView(items, go, icon = null) {
  // pozycje pogrupowane jak w ustawieniach telefonu: Rynek / Analizy / Konto
  const screen = el("div", { class: "screen" });
  let list = null, last = null;
  for (const it of items) {
    if (!list || (it.group || "") !== last) {
      last = it.group || "";
      if (last) screen.append(el("h2", { class: "menu-group" }, last));
      list = el("ul", { class: "menu" });
      screen.append(list);
    }
    const btn = el("button", { type: "button" },
      icon ? el("span", { class: "ic", "aria-hidden": "true" }, icon(it.id)) : null,
      el("span", {}, el("span", { class: "t" }, it.label), it.hint ? el("span", { class: "h" }, it.hint) : null),
      el("i", { class: "chev", "aria-hidden": "true" }));
    btn.addEventListener("click", () => go(it.id));
    list.append(el("li", {}, btn));
  }
  return screen;
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
      : "Pliki są zaszyfrowane w sejfie Twojego konta - po zalogowaniu na innym urządzeniu zobaczysz ten sam portfel. Do obliczeń serwer dostaje je tylko do pamięci.")));

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
      const rm = el("button", { class: "link-btn", type: "button", "aria-label": `Usuń rachunek ${a.id} z sejfu` }, "Usuń");
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

function bytes(n) {
  if (!n) return "0 kB";
  return n >= 1048576 ? `${(n / 1048576).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(n / 1024))} kB`;
}

function passwordSheet(title, fields, action, doneText) {
  openSheet(title, (close) => {
    const inputs = fields.map(([label, auto]) => field(label, { type: "password", autocomplete: auto }));
    const msg = el("div");
    const btn = el("button", { class: "btn", type: "button" }, doneText);
    btn.addEventListener("click", async () => {
      msg.replaceChildren();
      btn.disabled = true;
      try { await action(inputs.map((f) => f.input.value), close); } catch (err) { msg.replaceChildren(errorNote(err)); btn.disabled = false; }
    });
    setTimeout(() => { if (!document.activeElement || document.activeElement === document.body) inputs[0].input.focus(); }, 50);
    return el("div", { class: "controls" }, ...inputs.map((f) => f.row), btn, msg);
  });
}

function accountSection(reloadAll, showLogin) {
  const box = el("section", { class: "account" }, el("h2", { class: "block-title" }, "Konto"));
  const status = el("div");
  const fill = async () => {
    let u;
    try { u = await api.me(); } catch (err) { box.append(errorNote(err)); return; }
    const when = (t) => (t ? new Date(t * 1000).toLocaleDateString("pl-PL") : "–");
    const rows = [
      ["Użytkownik", u.username + (u.is_admin ? " (administrator)" : "")],
      ["Zalogowane urządzenia", String(u.devices)],
      ["Sejf", `${u.vault_items} ${u.vault_items === 1 ? "plik" : [2, 3, 4].includes(u.vault_items % 10) && ![12, 13, 14].includes(u.vault_items % 100) ? "pliki" : "plików"} · ${bytes(u.vault_bytes)} z ${bytes(u.vault_limit)}`],
      ["Baza", `${u.storage}${u.persistent ? "" : " - tymczasowa!"}`],
      ["Konto od", when(u.created)],
    ];
    box.append(el("ul", { class: "ledger" }, rows.map(([k, v]) => el("li", {}, el("div", { class: "row" },
      el("span", { class: "label" }, k), el("span", { class: "value small" }, v))))));
    if (!u.persistent) box.append(el("p", { class: "note warn" }, "Baza jest tymczasowa: po restarcie serwera konta i sejfy znikną. Ustaw DATABASE_URL (Neon) - instrukcja w README."));
    box.append(el("p", { class: "caption" }, "Wgrane eksporty i ustawienia są zaszyfrowane w telefonie kluczem z Twojego hasła. "
      + "Serwer i baza przechowują tylko szyfrogramy. Do obliczeń telefon wysyła odszyfrowane pliki na czas sesji - serwer trzyma je wtedy wyłącznie w pamięci."));

    const pw = el("button", { class: "btn quiet", type: "button" }, "Zmień hasło");
    pw.addEventListener("click", () => passwordSheet("Zmiana hasła", [["Obecne hasło", "current-password"], ["Nowe hasło", "new-password"],
      ["Powtórz nowe hasło", "new-password"]], async ([oldP, newP, rep], close) => {
      if (newP.length < MIN_PASSWORD) throw new Error(`Nowe hasło musi mieć co najmniej ${MIN_PASSWORD} znaków.`);
      if (newP !== rep) throw new Error("Nowe hasła się różnią.");
      await api.changePassword(oldP, newP);
      close();
      status.replaceChildren(el("p", { class: "note" }, "Hasło zmienione. Sejf jest zaszyfrowany nowym kluczem, a inne urządzenia zostały wylogowane."));
    }, "Zmień hasło i zaszyfruj sejf od nowa"));
    const others = el("button", { class: "btn quiet", type: "button" }, "Wyloguj inne urządzenia");
    others.addEventListener("click", async () => {
      try { const r = await api.logoutOthers(); status.replaceChildren(el("p", { class: "note" }, `Wylogowano urządzenia: ${r.ended}.`)); }
      catch (err) { status.replaceChildren(errorNote(err)); }
    });
    const out = el("button", { class: "btn quiet", type: "button" }, "Wyloguj");
    out.addEventListener("click", async () => { out.disabled = true; await api.logout(); showLogin(); });
    const del = el("button", { class: "btn danger", type: "button" }, "Usuń konto");
    del.addEventListener("click", () => passwordSheet("Usuń konto", [["Hasło", "current-password"]], async ([p]) => {
      await api.deleteAccount(p);
      showLogin();
    }, "Usuń konto i cały sejf na zawsze"));
    box.append(el("div", { class: "btn-row" }, pw, others, out), status, el("div", { class: "btn-row" }, del));
    if (u.is_admin) box.after(adminSection());
  };
  fill();
  return box;
}

function adminSection() {
  const box = el("section", { class: "account" }, el("h2", { class: "block-title" }, "Zaproszenia i konta"),
    el("p", { class: "caption" }, "Nowe konto można założyć tylko jednorazowym kodem zaproszenia, ważnym 7 dni. Kod widać tylko raz - przekaż go osobiście lub w wiadomości."));
  const list = el("div");
  const status = el("div");
  const note = field("Dla kogo (opcjonalnie)", { type: "text", maxlength: "80", autocomplete: "off" });
  const make = el("button", { class: "btn", type: "button" }, "Wygeneruj kod zaproszenia");
  make.addEventListener("click", async () => {
    make.disabled = true;
    try {
      const inv = await api.admin.invite(note.input.value);
      note.input.value = "";
      const code = el("code", { class: "invite-code" }, inv.code);
      const copy = el("button", { class: "link-btn", type: "button" }, "Kopiuj");
      copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(inv.code); copy.textContent = "Skopiowano"; } catch { copy.textContent = "Zaznacz i skopiuj ręcznie"; } });
      status.replaceChildren(el("div", { class: "invite" }, el("span", {}, "Kod zaproszenia:"), code, copy));
      await refresh();
    } catch (err) { status.replaceChildren(errorNote(err)); }
    make.disabled = false;
  });
  const refresh = async () => {
    let data;
    try { data = await api.admin.list(); } catch (err) { list.replaceChildren(errorNote(err)); return; }
    const now = Date.now() / 1000;
    const when = (t) => new Date(t * 1000).toLocaleDateString("pl-PL");
    const pending = data.invites.filter((i) => !i.used_by && i.expires > now);
    const rows = [];
    for (const i of pending) {
      const rm = el("button", { class: "link-btn", type: "button" }, "Unieważnij");
      rm.addEventListener("click", async () => { await api.admin.revoke(i.id); refresh(); });
      rows.push(el("li", {}, el("div", { class: "row" }, el("span", { class: "label stack" }, `Zaproszenie${i.note ? ` - ${i.note}` : ""}`,
        el("small", {}, `ważne do ${when(i.expires)}`)), rm)));
    }
    for (const u of data.users) {
      const cells = [el("span", { class: "label stack" }, u.username + (u.is_admin ? " (administrator)" : ""),
        el("small", {}, `konto od ${when(u.created)} · sejf ${bytes(u.vault_bytes)}`))];
      if (!u.is_admin) {
        const rm = el("button", { class: "link-btn", type: "button" }, "Usuń");
        let armed = false;
        rm.addEventListener("click", async () => {
          if (!armed) { armed = true; rm.textContent = "Na pewno?"; return; }
          await api.admin.removeUser(u.id);
          refresh();
        });
        cells.push(rm);
      }
      rows.push(el("li", {}, el("div", { class: "row" }, ...cells)));
    }
    list.replaceChildren(el("ul", { class: "ledger" }, rows));
  };
  refresh();
  box.append(el("div", { class: "controls" }, note.row, make), status, list);
  return box;
}

export function settingsView(reloadAll, showLogin) {
  const cur = api.settings();
  const health = api.state.health;
  const root = el("div", { class: "screen" });
  const status = el("div");
  root.append(accountSection(reloadAll, showLogin));
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
      el("li", {}, "Eksporty z XTB są zaszyfrowane w sejfie Twojego konta kluczem, który zna tylko Twój telefon. Odszyfrowane trafiają na serwer wyłącznie na czas obliczeń i tylko do pamięci."),
      el("li", {}, `Wersja ${health.version}. Dział AI: ${health.ai_enabled ? "włączony" : "wyłączony (brak klucza API na serwerze)"}.`))));
  root.append(el("div", {}, install, about));

  const forget = el("button", { class: "btn danger", type: "button" }, "Usuń wgrane eksporty i ustawienia");
  let armed = false;
  forget.addEventListener("click", async () => {
    if (!armed) { armed = true; forget.textContent = "Dotknij ponownie, żeby potwierdzić"; return; }
    forget.disabled = true;
    try { await api.forgetDevice(); reloadAll(); } catch (err) { status.replaceChildren(errorNote(err)); forget.disabled = false; }
  });
  root.append(el("div", { class: "btn-row" }, forget), status);
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
