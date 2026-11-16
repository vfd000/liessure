// Minimal service worker: makes the PWA installable. No offline caching —
// every card should be fresh, and a stale feed is worse than an error.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
