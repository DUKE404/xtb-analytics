// Szyfrowanie w telefonie. Hasło nie opuszcza urządzenia:
//   klucz główny = PBKDF2-SHA256(hasło, sól konta, N iteracji)
//   klucz logowania = HKDF(klucz główny, "xtba-auth-v1")  -> wysyłany na serwer (serwer trzyma scrypt z niego)
//   klucz szyfrowania = HKDF(klucz główny, "xtba-enc-v1") -> AES-GCM 256, nigdy nie opuszcza telefonu
// Z klucza logowania nie da się odtworzyć klucza szyfrowania, więc serwer i baza widzą tylko szyfrogramy.
// Klucz szyfrowania jest zapisany w IndexedDB jako klucz nieeksportowalny: można go używać, ale nie odczytać.

const te = new TextEncoder();
const td = new TextDecoder();

export function toHex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function fromHex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
  return out;
}

export function randomHex(bytes = 16) {
  return toHex(crypto.getRandomValues(new Uint8Array(bytes)));
}

function toB64(bytes) {
  const u = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s);
}

function fromB64(b64) {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

/** Klucz logowania (hex) i klucz szyfrowania (CryptoKey) z hasła. */
export async function deriveKeys(password, saltHex, iterations) {
  if (!crypto?.subtle) throw new Error("Ta przeglądarka nie obsługuje szyfrowania (wymagane HTTPS).");
  const salt = fromHex(saltHex);
  const base = await crypto.subtle.importKey("raw", te.encode(String(password).normalize("NFKC")), "PBKDF2", false, ["deriveBits"]);
  const master = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, base, 256);
  const hk = await crypto.subtle.importKey("raw", master, "HKDF", false, ["deriveBits", "deriveKey"]);
  const auth = await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info: te.encode("xtba-auth-v1") }, hk, 256);
  const encKey = await crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt, info: te.encode("xtba-enc-v1") }, hk,
    { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  return { authKey: toHex(auth), encKey };
}

/** Szyfruje {meta (JSON), dane binarne}. Identyfikator elementu jest dodatkowymi danymi uwierzytelnianymi:
 *  szyfrogramu nie da się przenieść pod inny klucz w sejfie bez wykrycia. */
export async function seal(key, itemKey, meta, data = new Uint8Array(0)) {
  const m = te.encode(JSON.stringify(meta));
  const body = new Uint8Array(4 + m.length + data.byteLength);
  new DataView(body.buffer).setUint32(0, m.length);
  body.set(m, 4);
  body.set(new Uint8Array(data), 4 + m.length);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: te.encode(itemKey) }, key, body);
  return { iv: toB64(iv), data: toB64(ct) };
}

export async function open(key, itemKey, item) {
  const plain = new Uint8Array(await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: fromB64(item.iv), additionalData: te.encode(itemKey) }, key, fromB64(item.data)));
  const n = new DataView(plain.buffer).getUint32(0);
  const meta = JSON.parse(td.decode(plain.subarray(4, 4 + n)));
  return { meta, data: plain.slice(4 + n).buffer };
}

// --------------------------------------------------------------------------- klucz zapamiętany w tym urządzeniu

const KDB = "xtb-analytics-auth", KSTORE = "k";

function kdb() {
  return new Promise((resolve, reject) => {
    if (!("indexedDB" in self)) return reject(new Error("Brak IndexedDB"));
    const req = indexedDB.open(KDB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(KSTORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function kreq(mode, fn) {
  return kdb().then((db) => new Promise((resolve, reject) => {
    const t = db.transaction(KSTORE, mode);
    const r = fn(t.objectStore(KSTORE));
    t.oncomplete = () => { db.close(); resolve(r && "result" in r ? r.result : undefined); };
    t.onerror = () => { db.close(); reject(t.error); };
  }));
}

let current = null;   // { username, key }

export const keyring = {
  get ready() { return !!current; },
  get username() { return current?.username || ""; },
  get key() { return current?.key || null; },
  async set(username, key) {
    current = { username, key };
    try { await kreq("readwrite", (s) => s.put({ username, key }, "current")); } catch { /* zostaje w pamięci do zamknięcia karty */ }
  },
  async load() {
    if (current) return true;
    try {
      const v = await kreq("readonly", (s) => s.get("current"));
      if (v && v.key) current = v;
    } catch { /* nic */ }
    return !!current;
  },
  async clear() {
    current = null;
    try { await kreq("readwrite", (s) => s.delete("current")); } catch { /* nic */ }
  },
};

// --------------------------------------------------------------------------- sejf na serwerze

export const PREFS_KEY = "prefs";

/** Elementy sejfu: pliki ("f_...") i ustawienia ("prefs"). `request` to funkcja z api.js (z tokenem). */
export async function fetchItems(request) {
  return (await request("/api/vault", { timeout: 120000 })).items || [];
}

export async function putItem(request, key, meta, data) {
  const sealed = await seal(keyring.key, key, meta, data);
  await request(`/api/vault/${encodeURIComponent(key)}`, { method: "PUT", json: sealed, timeout: 180000 });
}

export async function deleteItem(request, key) {
  await request(`/api/vault/${encodeURIComponent(key)}`, { method: "DELETE" });
}

export function newFileKey() {
  return "f_" + randomHex(12);
}
