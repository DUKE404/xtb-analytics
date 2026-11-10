// Pamięć po stronie telefonu: ustawienia w localStorage, pliki eksportu w IndexedDB.
// Eksport z XTB zostaje na urządzeniu - na serwer trafia tylko na czas obliczeń.

const PREFIX = "xtba:";

export const prefs = {
  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem(PREFIX + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* tryb prywatny / brak miejsca */ }
  },
  remove(key) {
    try { localStorage.removeItem(PREFIX + key); } catch { /* nic */ }
  },
};

const DB = "xtb-analytics", STORE = "files";

function open() {
  return new Promise((resolve, reject) => {
    if (!("indexedDB" in self)) return reject(new Error("Brak IndexedDB"));
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "name" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const out = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(out && "result" in out ? out.result : undefined);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

let memory = [];   // zapas, gdy IndexedDB jest niedostępne (np. tryb prywatny)

export const files = {
  /** [{name, type, data: ArrayBuffer}] */
  async save(list) {
    memory = list;
    try {
      const db = await open();
      await tx(db, "readwrite", (s) => { s.clear(); list.forEach((f) => s.put(f)); });
      db.close();
    } catch { /* zostaje kopia w pamięci na czas tej sesji */ }
  },
  async load() {
    try {
      const db = await open();
      const out = await tx(db, "readonly", (s) => s.getAll());
      db.close();
      if (out && out.length) memory = out;
    } catch { /* nic */ }
    return memory;
  },
  async clear() {
    memory = [];
    try {
      const db = await open();
      await tx(db, "readwrite", (s) => s.clear());
      db.close();
    } catch { /* nic */ }
  },
};
