// Service worker: installable PWA, and something to read when the network
// drops. The feed is always fetched fresh when it can be; these caches only
// answer when the network doesn't. The cards themselves are kept by app.js
// (localStorage); this keeps the page that renders them and their images.
const SHELL = "liessure-shell-v2";
// v2: v1 could hold another person's photos (before photo cards were the owner's only); activate drops it.
const IMAGES = "liessure-img-v2";
const MAX_IMAGES = 120;
const SHELL_FILES = ["/", "/app.js", "/style.css", "/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== SHELL && k !== IMAGES) await caches.delete(k);
    await self.clients.claim();
  })());
});

// Network first, falling back to what was last cached. Only plain 200s are
// kept: a redirect to the SSO page or a gatekeeper challenge must not be
// replayed as the app later.
async function networkFirst(req, cacheName, key) {
  try {
    const res = await fetch(req);
    if (res.ok && res.type === "basic" && !res.redirected) {
      const copy = res.clone();
      caches.open(cacheName).then((c) => c.put(key || req, copy)).catch(() => {});
    }
    return res;
  } catch (err) {
    const hit = await caches.match(key || req);
    if (hit) return hit;
    throw err;
  }
}

// Household images (/img proxy): they don't change, so cache first, and keep
// only the most recent MAX_IMAGES.
async function image(req) {
  const cache = await caches.open(IMAGES);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) {
    await cache.put(req, res.clone()).catch(() => {});
    const keys = await cache.keys();
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_IMAGES))) await cache.delete(k);
  }
  return res;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return; // embeds, Wikipedia images: browser default
  if (url.pathname === "/img") return e.respondWith(image(req));
  if (req.mode === "navigate") return e.respondWith(networkFirst(req, SHELL, "/"));
  if (url.pathname === "/api/sources") return e.respondWith(networkFirst(req, SHELL));
  if (SHELL_FILES.includes(url.pathname)) return e.respondWith(networkFirst(req, SHELL));
  // /api/feed, /api/event, /stream/audio (Range requests): straight to the network.
});
