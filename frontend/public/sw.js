// SLQ Hub service worker: makes the app installable and starts it quickly.
// The app shell is cached; API calls always go to the network.
const SHELL = "slq-hub-shell-v1";
const ASSETS = "slq-hub-assets-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(["/", "/manifest.webmanifest", "/icons/icon.svg", "/icons/icon-192.png"]))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== ASSETS).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const p = url.pathname;
  if (p.startsWith("/hub/") || p.startsWith("/api/") || p.startsWith("/v1/") || p.startsWith("/k/")) return;

  if (p.startsWith("/assets/") || p.startsWith("/artifact-runtime/")) {
    // /assets/ names carry a content hash: cache first. The artifact runtime
    // is served from the cache and refreshed in the background.
    event.respondWith(
      caches.open(ASSETS).then(async (c) => {
        const hit = await c.match(req);
        if (hit && p.startsWith("/assets/")) return hit;
        const refresh = fetch(req).then((res) => {
          if (res.ok) c.put(req, res.clone());
          return res;
        });
        if (hit) {
          event.waitUntil(refresh.catch(() => undefined));
          return hit;
        }
        return refresh;
      }),
    );
    return;
  }

  if (req.mode === "navigate") {
    // The page itself: the network when it answers, the cached shell when not.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL).then((c) => c.put("/", copy));
          }
          return res;
        })
        .catch(() => caches.match("/")),
    );
  }
});
