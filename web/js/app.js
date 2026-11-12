// Start aplikacji, nawigacja i ładowanie ekranów.

import * as api from "./api.js";
import { renderScreen } from "./blocks.js";
import { aiView, errorNote, resetChat, importView, loginView, mappingView, moreView, periodLabel, settingsView, viewSheet } from "./client.js";
import { el, fmtDate } from "./format.js";

const $ = (id) => document.getElementById(id);
const main = $("main"), tabsEl = $("tabs"), subnav = $("subnav"), barTitle = $("bar-title"), barMeta = $("bar-meta");

const ICONS = {
  home: "M4 11.5 12 5l8 6.5M6.5 10v9h11v-9",
  chart: "M4 19h16M7 16v-5M12 16V7M17 16v-8",
  shield: "M12 4l7 2.5v5c0 4.2-2.9 7.3-7 8.5-4.1-1.2-7-4.3-7-8.5v-5L12 4z",
  dice: "M5 5h14v14H5zM9 9h.01M15 15h.01M15 9h.01M9 15h.01M12 12h.01",
  more: "M6 12h.01M12 12h.01M18 12h.01",
  fund: "M4 19h16M6 19V10M10 19V6M14 19v-7M18 19V8M4 6l4 2 4-4 4 3 4-2",
  companies: "M4 20V9l6-4v15M10 20V11l10-4v13M3 20h18M13.5 13h3M13.5 16.5h3",
  sim: "M5 5h14v14H5zM9 9h.01M15 15h.01M12 12h.01M15 9h.01M9 15h.01",
  trader: "M4 17l5-5 3.5 3.5L20 8M15 8h5v5",
  tax: "M7 4h10v16l-2.5-1.5L12 20l-2.5-1.5L7 20zM10 9h4M10 13h4",
  ai: "M12 4v2.5M12 17.5V20M4 12h2.5M17.5 12H20M9 9h6v6H9z",
  import: "M12 4v10M8 10l4 4 4-4M5 19h14",
  settings: "M5 8h9M18 8h1M5 16h1M10 16h9M16 6v4M8 14v4",
};
const PERIOD_SHORT = { "": "Całość", ytd: "YTD", "12m": "12M", "24m": "24M", "36m": "3L" };
const CLIENT_SCREENS = new Set(["import", "mapping", "settings", "ai"]);
const screenParams = new Map();   // parametry kontrolek zapamiętane per ekran
let loadToken = 0;

/** Działy z serwera; ekrany oznaczone "needs" pokazują się tylko, gdy mają sens (np. CFD tylko przy pozycjach CFD). */
function sections() {
  const nav = api.state.health.nav;
  const s = api.state.session || {};
  const ok = (scr) => scr.needs !== "cfd" || (s.has_cfd && !s.exclude_cfd);
  const trim = (list) => list.map((sec) => ({ ...sec, screens: (sec.screens || []).filter(ok) }));
  const tabs = trim(nav.tabs), more = trim(nav.more);
  return { tabs, more, all: [...tabs, ...more] };
}

// adresy sprzed uproszczenia nawigacji (zapisane zakładki, skróty na ekranie głównym)
const MOVED_SECTIONS = { fund: ["perf", ""], settings: ["import", "settings"] };
const MOVED_SCREENS = { "fund-tearsheet": "perf-summary" };

function parseRoute() {
  const [sec, scr] = location.hash.replace(/^#\/?/, "").split("/");
  return { sec: sec || "", scr: scr || "" };
}

function go(sec, scr = "") {
  const target = `#/${sec}${scr ? "/" + scr : ""}`;
  if (location.hash === target) route(); else location.hash = target;
}

function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", ICONS[name] || ICONS.more);
  if (name === "dice" || name === "more") p.setAttribute("stroke-width", name === "more" ? 2.6 : 1.6);
  s.append(p);
  return s;
}

/** Telefon: 4 działy + "Więcej" na dole. Szeroki ekran: te same przyciski jako boczny panel, a działy
 *  z "Więcej" (klasa .extra) są w nim widoczne od razu. */
function drawTabs(activeId, sectionId = activeId) {
  const { tabs, more } = sections();
  const items = [...tabs.map((t) => ({ id: t.id, label: t.label, icon: t.icon })), { id: "more", label: "Więcej", icon: "more" },
    ...more.map((t) => ({ id: t.id, label: t.label, icon: t.id, extra: true }))];
  tabsEl.replaceChildren(...items.map((t) => {
    const current = t.extra ? t.id === sectionId : t.id === activeId;
    const b = el("button", { type: "button", class: t.extra ? "extra" : (t.id === "more" ? "more" : null), "aria-current": current ? "page" : null },
      icon(t.icon), el("span", {}, t.label));
    b.addEventListener("click", () => go(t.id));
    return b;
  }));
  tabsEl.hidden = false;
}

/** Pasek okresu analizy: zawsze widoczny nad treścią, jedno dotknięcie przelicza wszystkie ekrany. */
function drawViewbar() {
  const bar = $("viewbar");
  const s = api.state.session;
  if (!s || !s.since_choices) { bar.hidden = true; return; }
  const custom = !!s.until || (!!s.since && !(s.since in PERIOD_SHORT));
  const apply = async (view, btn) => {
    bar.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    btn.classList.add("busy");
    try { await api.setView(view); reloadAll(); } catch (err) { main.prepend(errorNote(err)); drawViewbar(); }
  };
  const chips = s.since_choices.map((o) => {
    const b = el("button", { type: "button", "aria-pressed": !custom && s.since === o.value ? "true" : "false", title: o.label },
      PERIOD_SHORT[o.value] ?? o.label);
    b.addEventListener("click", () => apply({ since: o.value, until: "" }, b));
    return b;
  });
  const range = el("button", { type: "button", class: "range", "aria-pressed": custom ? "true" : "false" }, custom ? periodLabel(s) : "Zakres dat…");
  range.addEventListener("click", () => viewSheet(reloadAll, { custom: true }));
  const extra = [];
  if (s.has_cfd) {
    const cfd = el("button", { type: "button", class: "opt", "aria-pressed": s.exclude_cfd ? "true" : "false" }, "Bez CFD");
    cfd.addEventListener("click", () => apply({ exclude_cfd: !s.exclude_cfd }, cfd));
    extra.push(cfd);
  }
  bar.replaceChildren(el("span", { class: "viewbar-label" }, "Okres"), ...chips, range, ...extra);
  bar.hidden = false;
}

function drawSubnav(section, activeScreen) {
  const list = section?.screens || [];
  if (list.length < 2) { subnav.hidden = true; subnav.replaceChildren(); return; }
  subnav.replaceChildren(...list.map((s) => {
    const b = el("button", { type: "button", "aria-current": s.id === activeScreen ? "page" : null }, s.label);
    b.addEventListener("click", () => go(section.id, s.id));
    return b;
  }));
  subnav.hidden = false;
  const cur = subnav.querySelector('[aria-current="page"]');
  if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest", inline: "center" });
}

function drawBar(titleText) {
  barTitle.textContent = titleText;
  document.title = `${titleText} – XTB Analytics`;
  const s = api.state.session;
  barMeta.replaceChildren();
  if (!s) return;
  drawViewbar();
  const accounts = s.accounts || [];
  const chosen = accounts.filter((a) => a.selected).length;
  const what = [accounts.length > 1 ? `${chosen} z ${accounts.length} rachunków` : null, s.base,
    s.exclude_cfd ? "bez CFD" : null, periodLabel(s) || null].filter(Boolean).join(" · ");
  const pick = el("button", { class: "view-btn", type: "button", "aria-label": `Widok portfela: ${what}` }, what);
  pick.addEventListener("click", () => viewSheet(reloadAll));
  barMeta.append(pick, el("div", { class: s.is_demo ? "demo" : null }, s.is_demo ? "dane przykładowe"
    : s.report_date && String(s.report_date).slice(0, 10) < String(s.as_of).slice(0, 10)
      ? `wycena ${fmtDate(s.as_of).slice(0, 5)} · skład z ${fmtDate(s.report_date).slice(0, 5)}` : `stan na ${fmtDate(s.as_of)}`));
}

function skeleton() {
  return el("div", { class: "screen skeleton", "aria-busy": "true" }, el("div"), el("div"), el("div"), el("div"), el("div"), el("div"));
}

function show(node) {
  main.replaceChildren(node);
  main.classList.remove("is-loading");
}

function showLogin() {
  tabsEl.hidden = true;
  subnav.hidden = true;
  const vb = document.getElementById("viewbar");
  if (vb) vb.hidden = true;
  drawBar("XTB Analytics");
  show(loginView(({ moved } = {}) => { if (moved) location.hash = "#/import"; start(); }));
}

function reloadAll() {
  screenParams.clear();
  resetChat();        // inne dane = nowa rozmowa
  route();
}

async function loadServerScreen(name) {
  const token = ++loadToken;
  const had = main.querySelector(".screen:not(.skeleton)") && main.dataset.screen === name;
  if (had) main.classList.add("is-loading"); else show(skeleton());   // przy zmianie parametrów trzymamy poprzedni widok
  main.dataset.screen = name;
  try {
    const payload = await api.screen(name, screenParams.get(name) || {});
    if (token !== loadToken) return;
    const y = had ? window.scrollY : 0;
    show(renderScreen(payload, {
      onParams: (patch) => {
        screenParams.set(name, { ...(screenParams.get(name) || {}), ...patch });
        loadServerScreen(name);
      },
      onDownload: async (path, filename, btn) => {
        btn.disabled = true;
        try { await api.download(path, filename); } catch (err) { btn.after(errorNote(err)); } finally { btn.disabled = false; }
      },
    }));
    drawBar(barTitle.textContent);
    window.scrollTo(0, y);
  } catch (err) {
    if (token !== loadToken) return;
    if (err.status === 401) return showLogin();
    if (err.code === "no_screen") {
      // ekran zniknął po wdrożeniu nowej wersji: przeładuj aplikację albo wróć na Przegląd
      if (await api.newerBuild()) return location.reload();
      return go("overview");
    }
    const retry = el("button", { class: "btn quiet", type: "button" }, "Spróbuj ponownie");
    retry.addEventListener("click", () => loadServerScreen(name));
    show(el("div", { class: "screen" }, errorNote(err), retry));
  }
}

function route() {
  if (!api.state.health || !api.state.session) return;
  const { tabs, more, all } = sections();
  let { sec, scr } = parseRoute();
  ++loadToken;
  if (MOVED_SECTIONS[sec]) { scr = scr || MOVED_SECTIONS[sec][1]; sec = MOVED_SECTIONS[sec][0]; }
  if (MOVED_SCREENS[scr]) scr = MOVED_SCREENS[scr];
  if (scr && !all.find((s) => s.id === sec && (s.screens || []).some((x) => x.id === scr))) {
    const home = all.find((s) => (s.screens || []).some((x) => x.id === scr));      // ekran przeniesiony do innego działu
    if (home) sec = home.id;
  }

  if (sec === "more") {
    drawTabs("more");
    drawSubnav(null);
    drawBar("Więcej");
    main.dataset.screen = "more";
    show(moreView(more, (id) => go(id)));
    window.scrollTo(0, 0);
    return;
  }
  let section = all.find((s) => s.id === sec);
  if (!section) { section = tabs[0]; sec = section.id; scr = ""; }
  const screens = section.screens || [{ id: section.id, label: section.label }];
  const screen = screens.find((s) => s.id === scr) || screens[0];
  const inMore = more.includes(section);
  drawTabs(inMore ? "more" : section.id, section.id);
  drawSubnav(section, screen.id);
  drawBar(section.label);

  if (CLIENT_SCREENS.has(screen.id)) {
    main.dataset.screen = screen.id;
    const view = screen.id === "import" ? importView(route, reloadAll)
      : screen.id === "mapping" ? mappingView(reloadAll)
        : screen.id === "ai" ? aiView() : settingsView(reloadAll, showLogin);
    show(view);
    drawBar(section.label);
    window.scrollTo(0, 0);
    return;
  }
  loadServerScreen(screen.id);
}

function bootMessage(text, sub = "") {
  show(el("div", { class: "boot" }, el("p", { class: "boot-line" }, el("span", { class: "pulse" }), text), sub ? el("p", { class: "boot-sub" }, sub) : null));
}

async function start() {
  bootMessage("Przeliczam portfel…", "Odtwarzam historię dzień po dniu i liczę ryzyko.");
  let err = null;
  // zerwane połączenie (np. telefon uśpił kartę podczas budzenia serwera) - spróbuj jeszcze dwa razy
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await api.createSession();
      err = null;
      break;
    } catch (e) {
      err = e;
      if (e.status === 401) return showLogin();
      if (!api.isTransient(e)) break;
      bootMessage("Ponawiam połączenie…", "Serwer albo sieć chwilowo nie odpowiada.");
      try { await api.waitForServer(() => {}); } catch { break; }
    }
  }
  if (!err) return route();
  const retry = el("button", { class: "btn quiet", type: "button" }, "Spróbuj ponownie");
  retry.addEventListener("click", start);
  if (api.state.mode === "upload" && !api.isTransient(err)) {
    // zapisanego eksportu nie da się przeliczyć - pokaż na razie dane przykładowe, ale nie zmieniaj zapisanego
    // trybu: pliki zostają w sejfie i przy następnym starcie aplikacja znów spróbuje je wczytać
    try { await api.previewDemo(); } catch { /* obsłużone niżej */ }
    if (api.state.session) {
      route();
      main.prepend(el("div", {}, errorNote(new Error(`Nie udało się przeliczyć Twojego portfela: ${err.message}\nNa razie pokazuję dane przykładowe. Twoje pliki są dalej w sejfie.`)), retry));
      return;
    }
  }
  show(el("div", { class: "screen" }, errorNote(err), retry));
}

async function boot() {
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* aplikacja działa także bez trybu offline */ });
  }
  if (!boot.wired) {
    window.addEventListener("hashchange", route);
    // aplikacja z ekranu głównego bywa otwarta tygodniami: po powrocie do niej sprawdź, czy nie ma nowej wersji
    document.addEventListener("visibilitychange", async () => {
      if (document.visibilityState === "visible" && api.state.health && await api.newerBuild()) location.reload();
    });
    boot.wired = true;
  }
  try {
    await api.waitForServer((sec) => {
      if (sec >= 3) bootMessage("Budzę serwer…", `Darmowa instancja zasypia po kwadransie bezczynności. Zwykle wstaje w niecałą minutę (${sec} s).`);
    });
  } catch (err) {
    const retry = el("button", { class: "btn quiet", type: "button" }, "Spróbuj ponownie");
    retry.addEventListener("click", () => { bootMessage("Łączę z serwerem…"); boot(); });
    show(el("div", { class: "screen" }, el("div", { style: "height:22vh" }), errorNote(err), retry));
    return;
  }
  let ok = false;
  try {
    ok = await api.restore();
  } catch (err) {
    if (err.code === "vault_key") return showLogin();
    const retry = el("button", { class: "btn quiet", type: "button" }, "Spróbuj ponownie");
    retry.addEventListener("click", () => { bootMessage("Łączę z serwerem…"); boot(); });
    show(el("div", { class: "screen" }, el("div", { style: "height:22vh" }), errorNote(err), retry));
    return;
  }
  if (!ok) return showLogin();
  start();
}

boot();
