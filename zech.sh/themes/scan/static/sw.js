/* AI.CHAT Service Worker */
const CACHE_NAME = "aichat-v1";
const PRECACHE = ["/", "/static/scan/css/aichat.css"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  // Network-first for API and SSE, cache-first for static assets
  const url = new URL(e.request.url);

  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/notifications/")) {
    return; // Let these pass through to network
  }

  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        if (resp.ok && e.request.method === "GET") {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(e.request, clone));
        }
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
