// Cache minimal : l'interface reste lisible hors ligne, les données restent fraîches.
const CACHE = "veille-v1";
const COQUILLE = ["./", "./index.html", "./manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(COQUILLE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((c) => c !== CACHE).map((c) => caches.delete(c))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  // Données : le réseau d'abord, le cache seulement en secours.
  if (url.pathname.endsWith("feed.json")) {
    e.respondWith(
      fetch(e.request)
        .then((r) => { const copie = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copie)); return r; })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Interface : le cache d'abord.
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
