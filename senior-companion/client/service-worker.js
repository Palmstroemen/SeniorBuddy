// Minimaler Service Worker: cached nur die statische Oberflaeche
// (HTML/CSS/JS), nicht die Chat-Inhalte selbst - die kommen live
// vom lokalen Server. Das reicht, damit die Seite als "App" installiert
// werden kann und beim naechsten Start sofort erscheint.
const CACHE_NAME = "senior-companion-shell-v1";
const SHELL_FILES = [
  "/",
  "/index.html",
  "/css/style.css",
  "/js/app.js",
  "/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
});

self.addEventListener("fetch", (event) => {
  // Nur GET-Requests auf die Shell-Dateien aus dem Cache bedienen;
  // API-/WebSocket-Aufrufe immer live ans Netzwerk.
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) return;

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
