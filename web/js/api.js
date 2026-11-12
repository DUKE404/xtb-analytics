// Komunikacja z serwerem: logowanie, sesja analizy, ekrany. Sesja żyje w pamięci serwera,
// więc gdy serwer ją zgubi (uśpienie darmowej instancji), odtwarzamy ją z plików z zaszyfrowanego sejfu konta.

import { setCurrency } from "./format.js";
import { bindRemote, dropLegacyFiles, files, prefs, resetMemory, syncFromVault, takeLegacyFiles } from "./store.js";
import { deriveKeys, fetchItems, keyring, open, randomHex, seal } from "./vault.js";

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
  user: null,                              // {username, is_admin, ...} po zalogowaniu
};

function reloadPrefs() {
  state.mode = prefs.get("mode", "demo");
  state.settings = prefs.get("settings", null);
}

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

/** Czy na serwerze jest nowsza wersja aplikacji niż ta otwarta w telefonie (porównanie odcisku `build`). */
export async function newerBuild() {
  const before = state.health?.build;
  try {
    const h = await request("/api/health", { timeout: 8000 });
    if (!before || h.build === before) { state.health = h; return false; }
    return true;
  } catch {
    return false;
  }
}

// --------------------------------------------------------------------------- konto

bindRemote(request);

export function authStatus() {
  return request("/api/auth/status", { timeout: 15000 });
}

/** Czy to urządzenie ma token i klucz szyfrowania (czyli można pominąć ekran logowania). */
export async function restore() {
  if (!state.token || !(await keyring.load())) return false;
  try {
    state.user = await request("/api/auth/me", { timeout: 20000 });
  } catch (err) {
    if (err.status === 401) { await dropLocal(); return false; }
    throw err;
  }
  await syncFromVault();
  reloadPrefs();
  return true;
}

async function adoptLogin(out, username, encKey) {
  prefs.wipeLocal();                       // ustawienia poprzedniego konta na tym urządzeniu - precz
  resetMemory();
  state.token = out.token;
  state.session = null;
  prefs.set("token", state.token);
  await keyring.set(username, encKey);
  state.user = await request("/api/auth/me");
  await syncFromVault();
  reloadPrefs();
  return migrateLegacy();
}

/** Pliki z wersji sprzed kont (niezaszyfrowane w tym telefonie) trafiają do sejfu, jeśli sejf jest pusty. */
async function migrateLegacy() {
  const legacy = await takeLegacyFiles();
  if (!legacy.length) return 0;
  const current = await files.load();
  if (!current.length) {
    await files.save(legacy.map((f) => ({ name: f.name, type: f.type, data: f.data })));
    prefs.set("mode", "upload");
    state.mode = "upload";
  }
  dropLegacyFiles();
  return current.length ? 0 : legacy.length;
}

export async function login(username, password) {
  const name = String(username).trim().toLowerCase();
  const p = await request(`/api/auth/params?u=${encodeURIComponent(name)}`);
  const keys = await deriveKeys(password, p.salt, p.iterations);
  const out = await request("/api/auth/login", { method: "POST", json: { username: name, auth_key: keys.authKey } });
  return adoptLogin(out, name, keys.encKey);
}

/** Nowe konto: kod zaproszenia albo (dla pierwszego konta) kod konfiguracji. */
export async function register(username, password, code) {
  const name = String(username).trim().toLowerCase();
  const status = await authStatus();
  const salt = randomHex(16);
  const keys = await deriveKeys(password, salt, status.kdf_iterations);
  const out = await request("/api/auth/register", { method: "POST",
    json: { username: name, auth_key: keys.authKey, salt, invite: String(code || "").trim() } });
  return adoptLogin(out, name, keys.encKey);
}

async function dropLocal() {
  state.token = "";
  state.session = null;
  state.user = null;
  resetMemory();
  prefs.wipeLocal();
  await keyring.clear();
  reloadPrefs();
}

export async function logout() {
  await prefs.flush();
  try { await request("/api/auth/logout", { method: "POST", json: {}, timeout: 10000 }); } catch { /* i tak czyścimy urządzenie */ }
  await dropLocal();
}

export async function logoutOthers() {
  return request("/api/auth/logout-others", { method: "POST", json: {} });
}

export async function me() {
  state.user = await request("/api/auth/me");
  return state.user;
}

/** Zmiana hasła: nowe klucze, więc cały sejf jest szyfrowany od nowa w telefonie i wysyłany w jednym żądaniu. */
export async function changePassword(oldPassword, newPassword) {
  await prefs.flush();
  const name = keyring.username;
  const p = await request(`/api/auth/params?u=${encodeURIComponent(name)}`);
  const oldKeys = await deriveKeys(oldPassword, p.salt, p.iterations);
  const salt = randomHex(16);
  const newKeys = await deriveKeys(newPassword, salt, p.iterations);
  const items = [];
  for (const it of await fetchItems(request)) {
    let opened;
    try { opened = await open(oldKeys.encKey, it.key, it); } catch {
      throw new ApiError("Obecne hasło jest nieprawidłowe.", 403, "bad_credentials");
    }
    items.push({ key: it.key, ...(await seal(newKeys.encKey, it.key, opened.meta, opened.data)) });
  }
  await request("/api/auth/password", { method: "POST", timeout: 180000,
    json: { auth_key: oldKeys.authKey, new_auth_key: newKeys.authKey, new_salt: salt, items } });
  await keyring.set(name, newKeys.encKey);
  return me();
}

export async function deleteAccount(password) {
  const name = keyring.username;
  const p = await request(`/api/auth/params?u=${encodeURIComponent(name)}`);
  const keys = await deriveKeys(password, p.salt, p.iterations);
  await request("/api/auth/account", { method: "DELETE", json: { auth_key: keys.authKey } });
  await dropLocal();
}

export const admin = {
  list: () => request("/api/admin/invites"),
  invite: (note) => request("/api/admin/invites", { method: "POST", json: { note } }),
  revoke: (id) => request(`/api/admin/invites/${encodeURIComponent(id)}`, { method: "DELETE" }),
  removeUser: (id) => request(`/api/admin/users/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

function overridesFor(mode) {
  return prefs.get(`overrides:${mode}`, []);
}

/** Przyjmuje metadane sesji z serwera: waluta wyświetlania steruje formatowaniem kwot w całej aplikacji. */
function adopt(session) {
  state.session = session;
  setCurrency(session.base);
  return session;
}

/** Z plików w telefonie zostają tylko te, których serwer faktycznie użył (starszy eksport tego samego rachunku znika). */
async function pruneFiles(session) {
  if (state.mode !== "upload" || !session.files_used?.length) return;
  const stored = await files.load();
  const keep = stored.filter((f) => session.files_used.includes(f.name));
  if (keep.length && keep.length < stored.length) await files.save(keep);
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
  form.append("base", prefs.get("base", "PLN"));
  const view = prefs.get("view", {});
  if (view.exclude_cfd) form.append("exclude_cfd", "1");
  if (view.since) form.append("since", view.since);
  if (view.until) form.append("until", view.until);
  const off = mode === "upload" ? prefs.get("accounts:off", []) : [];
  if (off.length) form.append("exclude", JSON.stringify(off));
  adopt(await request("/api/session", { method: "POST", body: form, timeout: 180000 }));
  state.mode = mode;
  prefs.set("mode", mode);
  await pruneFiles(state.session);
  return state.session;
}

/** Zmiana waluty wyświetlania i/lub zaznaczonych rachunków. accounts = numery rachunków do wliczenia. */
export async function setView(view) {
  const out = await withSession((sid) => request(`/api/session/${sid}/view`, { method: "PUT", json: view, timeout: 180000 }));
  adopt(out);
  prefs.set("base", out.base);
  prefs.set("view", { exclude_cfd: out.exclude_cfd, since: out.since, until: out.until });
  if (state.mode === "upload") prefs.set("accounts:off", out.accounts.filter((a) => !a.selected).map((a) => a.id));
  return out;
}

/** Usuwa z sejfu pliki jednego rachunku i przelicza resztę. */
export async function removeAccount(id) {
  const acc = (state.session?.accounts || []).find((a) => a.id === id);
  if (!acc) return state.session;
  const stored = await files.load();
  const keep = stored.filter((f) => !acc.files.includes(f.name));
  if (!keep.length) return setMode("demo").then(async (s) => { await files.clear(); return s; });
  await files.save(keep);
  prefs.set("accounts:off", prefs.get("accounts:off", []).filter((x) => x !== id));
  return createSession();
}

export async function setMode(mode) {
  state.mode = mode;
  prefs.set("mode", mode);
  return createSession();
}

/** Dopisuje wybrane pliki do zapisanych w sejfie (ten sam plik zastępuje starszy) i tworzy z nich sesję.
 *  Dzięki temu drugi rachunek (np. USD obok PLN) można dograć osobno. Przy błędzie przywraca poprzedni stan. */
export async function uploadFiles(fileList) {
  const list = [];
  for (const f of fileList) list.push({ name: f.name, type: f.type, data: await f.arrayBuffer() });
  const previous = await files.load();
  const prevMode = state.mode;
  const names = new Set(list.map((f) => f.name));
  const kept = prevMode === "upload" ? previous.filter((f) => !names.has(f.name)) : [];
  await files.save([...kept, ...list]);
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
  return adopt(await request(`/api/session/${state.session.sid}/mapping`, { method: "PUT", json: { mapping: rows }, timeout: 180000 }));
}

/** Usuwa wgrane eksporty i ustawienia z sejfu konta (konto zostaje). */
export async function forgetDevice() {
  await files.clear();
  ["mode", "overrides:demo", "overrides:upload", "settings", "base", "accounts:off", "view"].forEach((k) => prefs.remove(k));
  await prefs.flush();
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
