// Komunikacja z serwerem: logowanie, sesja analizy, ekrany. Sesja żyje w pamięci serwera,
// więc gdy serwer ją zgubi (uśpienie darmowej instancji), odtwarzamy ją z plików zapisanych w telefonie.

import { files, prefs } from "./store.js";

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export const state = {
  health: null,
  token: prefs.get("token", ""),
  mode: prefs.get("mode", "demo"),        // "demo" | "upload"
  session: null,                           // metadane sesji z serwera
  settings: prefs.get("settings", null),   // {rf, bench}
};

async function request(path, { method = "GET", body = null, json = null, timeout = 120000 } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (json !== null) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  let resp;
  try {
    resp = await fetch(path, { method, headers, body, signal: ctl.signal, cache: "no-store" });
  } catch (err) {
    throw new ApiError(err.name === "AbortError" ? "Serwer nie odpowiedział na czas." : "Brak połączenia z serwerem.", 0, "network");
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try { data = await resp.json(); } catch { /* odpowiedź bez JSON */ }
  if (!resp.ok) throw new ApiError((data && data.error) || `Błąd serwera (${resp.status}).`, resp.status, (data && data.code) || "error");
  return data;
}

/** Czeka, aż serwer wstanie (darmowa instancja budzi się do ok. minuty). onWait(sekundy) do komunikatu. */
export async function waitForServer(onWait) {
  const started = Date.now();
  for (;;) {
    try {
      state.health = await request("/api/health", { timeout: 8000 });
      return state.health;
    } catch (err) {
      const waited = Math.round((Date.now() - started) / 1000);
      if (waited > 150) throw err;
      onWait(waited);
      await new Promise((r) => setTimeout(r, 2500));
    }
  }
}

export async function login(password) {
  const out = await request("/api/login", { method: "POST", json: { password } });
  state.token = out.token || "";
  prefs.set("token", state.token);
}

export function logout() {
  state.token = "";
  state.session = null;
  prefs.remove("token");
}

function overridesFor(mode) {
  return prefs.get(`overrides:${mode}`, []);
}

/** Tworzy sesję dla bieżącego trybu. W trybie "upload" bez plików w telefonie wraca do demo. */
export async function createSession() {
  const form = new FormData();
  let mode = state.mode;
  if (mode === "upload") {
    const stored = await files.load();
    if (!stored.length) mode = "demo";
    else stored.forEach((f) => form.append("files", new Blob([f.data], { type: f.type || "application/octet-stream" }), f.name));
  }
  if (mode === "demo") form.append("demo", "1");
  const ov = overridesFor(mode);
  if (ov.length) form.append("overrides", JSON.stringify(ov));
  state.session = await request("/api/session", { method: "POST", body: form, timeout: 180000 });
  state.mode = mode;
  prefs.set("mode", mode);
  return state.session;
}

export async function setMode(mode) {
  state.mode = mode;
  prefs.set("mode", mode);
  return createSession();
}

/** Zapisuje wybrane pliki w telefonie i tworzy z nich sesję. Przy błędzie formatu przywraca poprzedni stan. */
export async function uploadFiles(fileList) {
  const list = [];
  for (const f of fileList) list.push({ name: f.name, type: f.type, data: await f.arrayBuffer() });
  const previous = await files.load();
  const prevMode = state.mode;
  await files.save(list);
  state.mode = "upload";
  try {
    return await createSession();
  } catch (err) {
    await files.save(previous);
    state.mode = prevMode;
    throw err;
  }
}

export async function saveMapping(rows) {
  prefs.set(`overrides:${state.mode}`, rows);
  state.session = await request(`/api/session/${state.session.sid}/mapping`, { method: "PUT", json: { mapping: rows }, timeout: 180000 });
  return state.session;
}

export async function forgetDevice() {
  await files.clear();
  ["mode", "overrides:demo", "overrides:upload", "settings"].forEach((k) => prefs.remove(k));
  state.mode = "demo";
  state.settings = null;
  return createSession();
}

export function settings() {
  const d = state.health?.settings || { rf: 0.04, bench: "MSCIW" };
  return { rf: d.rf, bench: d.bench, ...(state.settings || {}) };
}

export function saveSettings(patch) {
  state.settings = { ...settings(), ...patch };
  prefs.set("settings", state.settings);
}

async function withSession(fn) {
  if (!state.session) await createSession();
  try {
    return await fn(state.session.sid);
  } catch (err) {
    if (err.code !== "session_expired") throw err;
    await createSession();            // serwer zgubił sesję - odtwórz z danych w telefonie i powtórz raz
    return fn(state.session.sid);
  }
}

export function screen(name, params = {}) {
  const s = settings();
  const all = { rf: s.rf, bench: s.bench, ...params };
  const hasBody = Object.values(params).some((v) => typeof v === "object" && v !== null);
  return withSession((sid) => {
    if (hasBody) return request(`/api/session/${sid}/screen/${name}`, { method: "POST", json: all });
    const qs = new URLSearchParams(Object.entries(all).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => [k, String(v)]));
    return request(`/api/session/${sid}/screen/${name}?${qs}`);
  });
}

export function get(path) {
  return withSession((sid) => request(`/api/session/${sid}/${path}`));
}

export function post(path, json) {
  return withSession((sid) => request(`/api/session/${sid}/${path}`, { method: "POST", json, timeout: 180000 }));
}

/** Pobiera plik (z nagłówkiem autoryzacji) i podaje go do zapisania. */
export async function download(path, filename) {
  const blob = await withSession(async (sid) => {
    const headers = state.token ? { Authorization: `Bearer ${state.token}` } : {};
    const resp = await fetch(`/api/session/${sid}/${path}`, { headers, cache: "no-store" });
    if (resp.status === 404) throw new ApiError("Sesja wygasła.", 404, "session_expired");
    if (!resp.ok) throw new ApiError(`Nie udało się pobrać pliku (${resp.status}).`, resp.status, "download");
    return resp.blob();
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
