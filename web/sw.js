// Service worker: trzyma powłokę aplikacji w telefonie, żeby otwierała się natychmiast -
// także wtedy, gdy darmowy serwer dopiero się budzi. Dane z /api nigdy nie są cache'owane.

const VERSION = "v1";
const CACHE = `xtb-analytics-${VERSION}`;
const SHELL = [
  "/", "/css/app.css", "/js/app.js", "/js/api.js", "/js/blocks.js", "/js/charts.js", "/js/client.js",
  "/js/format.js", "/js/store.js", "/fonts/ledger-serif.woff", "/fonts/ledger-serif-italic.woff",
  "/manifest.webmanifest", "/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("xtb-analytics-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  // powłoka: najpierw kopia z telefonu (szybki start), w tle odświeżenie z sieci
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const key = req.mode === "navigate" ? "/" : req;
      const cached = await cache.match(key);
      const fresh = fetch(req).then((resp) => {
        if (resp && resp.ok) cache.put(key, resp.clone());
        return resp;
      }).catch(() => cached);
      return cached || fresh;
    }),
  );
});
