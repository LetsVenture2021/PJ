const CACHE = "pj-static-v2";
const STATIC = [
  "/webrtc_client.html",
  "/manifest.webmanifest",
  "/assets/pj_web_utils.js",
  "/web/session_controller.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC)));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("pj-static-") && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isStatic =
    event.request.method === "GET" &&
    url.origin === self.location.origin &&
    STATIC.includes(url.pathname) &&
    !event.request.headers.has("authorization");
  if (!isStatic) return; // API, uploads, transcripts, artifacts and auth are never cached.
  // Network-first so shipped fixes reach clients immediately; the cache is
  // only a fallback for offline use. Cache-first previously pinned stale,
  // broken clients even after server-side repairs shipped.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() =>
        caches
          .match(event.request, { ignoreSearch: true })
          .then((cached) => cached || Response.error()),
      ),
  );
});
