// Pamięć aplikacji. Po zalogowaniu pliki eksportu i ustawienia są w zaszyfrowanym sejfie konta na serwerze
// (szyfruje je telefon - patrz vault.js), więc po zalogowaniu na innym urządzeniu widać ten sam portfel.
// localStorage trzyma tylko kopię ustawień do szybkiego startu i token logowania; odszyfrowane pliki żyją w pamięci.

import { PREFS_KEY, deleteItem, fetchItems, keyring, newFileKey, open, putItem } from "./vault.js";

const PREFIX = "xtba:";
// ustawienia zapisywane w sejfie konta (reszta, np. token, dotyczy tylko tego urządzenia)
const SYNCED = ["mode", "overrides:demo", "overrides:upload", "settings", "base", "accounts:off", "view"];

let remote = null;          // funkcja request(path, opts) z api.js
let pushTimer = null;
let pushing = Promise.resolve();

export function bindRemote(request) {
  remote = request;
}

function rawGet(key, fallback) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch { return fallback; }
}

function rawSet(key, value) {
  try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* tryb prywatny / brak miejsca */ }
}

// znacznik czasu ostatniej zmiany ustawień w tym urządzeniu i w sejfie: wygrywa nowsza wersja
// (zmiana tuż przed zamknięciem karty mogła nie zdążyć trafić do sejfu)
const STAMP = "_prefs_stamp";
const touch = () => rawSet(STAMP, Date.now());

function schedulePush() {
  if (!remote || !keyring.ready) return;
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => { pushing = pushPrefs().catch(() => { /* następna zmiana spróbuje ponownie */ }); }, 600);
}

async function pushPrefs() {
  const out = {};
  for (const k of SYNCED) {
    const v = rawGet(k, undefined);
    if (v !== undefined) out[k] = v;
  }
  await putItem(remote, PREFS_KEY, { kind: "prefs", v: 1, stamp: rawGet(STAMP, 0), prefs: out });
}

export const prefs = {
  get: rawGet,
  set(key, value) {
    rawSet(key, value);
    if (SYNCED.includes(key)) { touch(); schedulePush(); }
  },
  remove(key) {
    try { localStorage.removeItem(PREFIX + key); } catch { /* nic */ }
    if (SYNCED.includes(key)) { touch(); schedulePush(); }
  },
  /** Czeka, aż ostatnia zmiana ustawień trafi do sejfu (np. przed wylogowaniem). */
  async flush() {
    if (pushTimer) { clearTimeout(pushTimer); pushTimer = null; pushing = pushPrefs(); }
    await pushing.catch(() => {});
  },
  /** Wszystko z tego urządzenia: ustawienia, token, kopia sejfu w pamięci. */
  wipeLocal() {
    clearTimeout(pushTimer);
    try {
      Object.keys(localStorage).filter((k) => k.startsWith(PREFIX)).forEach((k) => localStorage.removeItem(k));
    } catch { /* nic */ }
  },
};

// --------------------------------------------------------------------------- pliki eksportu

let memory = [];            // [{name, type, data: ArrayBuffer, key}] - odszyfrowane, tylko w pamięci karty
let loaded = false;

/** Pobiera i odszyfrowuje sejf; ustawienia z sejfu zastępują lokalną kopię. Wywoływane po zalogowaniu i przy starcie. */
export async function syncFromVault() {
  const items = await fetchItems(remote);
  const out = [];
  let gotPrefs = false;
  for (const it of items) {
    let opened;
    try {
      opened = await open(keyring.key, it.key, it);
    } catch {
      throw Object.assign(new Error("Nie udało się odszyfrować sejfu tym hasłem. Zaloguj się ponownie."), { code: "vault_key" });
    }
    if (it.key === PREFS_KEY) {
      gotPrefs = true;
      if ((opened.meta.stamp || 0) >= rawGet(STAMP, 0)) {
        const p = opened.meta.prefs || {};
        SYNCED.forEach((k) => (k in p ? rawSet(k, p[k]) : localStorage.removeItem(PREFIX + k)));
        rawSet(STAMP, opened.meta.stamp || 0);
      } else {
        schedulePush();                       // lokalna kopia jest nowsza - wyślij ją
      }
    } else if (opened.meta.kind === "file") {
      out.push({ name: opened.meta.name, type: opened.meta.type || "", data: opened.data, key: it.key });
    }
  }
  memory = out.sort((a, b) => a.name.localeCompare(b.name));
  loaded = true;
  if (!gotPrefs && SYNCED.some((k) => rawGet(k, undefined) !== undefined)) schedulePush();
  return { files: memory.length, prefs: gotPrefs };
}

export function resetMemory() {
  memory = [];
  loaded = false;
}

export const files = {
  /** Zapisuje dokładnie tę listę plików: nowe są szyfrowane i wysyłane, brakujące usuwane z sejfu. */
  async save(list) {
    if (!remote || !keyring.ready) { memory = list; return; }
    const have = new Map(memory.map((f) => [f.name, f]));
    const next = [];
    for (const f of list) {
      const old = have.get(f.name);
      if (old && old.data === f.data) { next.push(old); continue; }       // ten sam plik - bez ponownego wysyłania
      const key = old?.key || newFileKey();
      await putItem(remote, key, { kind: "file", name: f.name, type: f.type || "" }, f.data);
      next.push({ name: f.name, type: f.type || "", data: f.data, key });
    }
    const keep = new Set(next.map((f) => f.key));
    for (const f of memory) if (!keep.has(f.key)) await deleteItem(remote, f.key);
    memory = next;
  },
  async load() {
    if (!loaded && remote && keyring.ready) await syncFromVault();
    return memory;
  },
  async clear() {
    await files.save([]);
  },
};

// --------------------------------------------------------------------------- pliki z dawnej wersji (bez kont)

const LEGACY_DB = "xtb-analytics", LEGACY_STORE = "files";

/** Pliki zapisane przez wersję sprzed kont (niezaszyfrowane, tylko w tym telefonie). Po przeniesieniu do sejfu
 *  są usuwane z urządzenia. */
export async function takeLegacyFiles() {
  if (!("indexedDB" in self) || !indexedDB.databases) return [];
  try {
    const dbs = await indexedDB.databases();
    if (!dbs.some((d) => d.name === LEGACY_DB)) return [];
  } catch { return []; }
  const db = await new Promise((resolve, reject) => {
    const req = indexedDB.open(LEGACY_DB);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  if (!db.objectStoreNames.contains(LEGACY_STORE)) { db.close(); return []; }
  const list = await new Promise((resolve) => {
    const r = db.transaction(LEGACY_STORE, "readonly").objectStore(LEGACY_STORE).getAll();
    r.onsuccess = () => resolve(r.result || []);
    r.onerror = () => resolve([]);
  });
  db.close();
  return list;
}

export function dropLegacyFiles() {
  try { indexedDB.deleteDatabase(LEGACY_DB); } catch { /* nic */ }
}
