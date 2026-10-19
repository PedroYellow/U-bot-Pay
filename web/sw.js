/* Ubotpay Service Worker — PWA offline + cache strategy.
 *
 * Versioning: bump CACHE_NAME to force a refresh of all clients.
 *
 * Strategy by request type:
 *   - HTML / JS / CSS: stale-while-revalidate (instant load + bg refresh)
 *   - Images / icons:  cache-first (immutable assets)
 *   - /health, /auth/chains, /bridge/chains: network-first (always fresh)
 *   - /bridge/* (quote, start, confirm, status): network-only (NEVER cache)
 *   - /agent/*: network-only
 *   - other /api/*: network-only
 *
 * Security: never cache responses that contain authentication headers.
 * In particular we skip caching anything sent with an Authorization header.
 */

const CACHE_NAME = "ubotpay-v1";
const PRECACHE = [
  "/",
  "/manifest.json",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
  "/favicon-32x32.png",
  "/ubot.css",
  "/i18n.js",
  "/analytics",
  "/agent",
];

self.addEventListener("install", (event) => {
  // Pre-cache the shell so the app loads offline on first launch.
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(PRECACHE).catch((e) => {
        // Don't fail install if any asset is missing; PWA will still work.
        console.warn("[sw] precache partial failure:", e);
      })
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Drop old caches when version bumps.
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin && !url.hostname.includes("ubotpay.com")) {
    return;
  }

  // NEVER cache anything that needs fresh auth or carries user data.
  const auth = req.headers.get("Authorization") || "";
  if (auth) return; // Bearer JWT — go to network only
  if (req.headers.get("x-api-key")) return;

  // NEVER cache transactional endpoints (quoting/bridging).
  if (
    url.pathname.startsWith("/bridge/quote") ||
    url.pathname.startsWith("/bridge/start") ||
    url.pathname.startsWith("/bridge/confirm") ||
    url.pathname.startsWith("/bridge/") && url.pathname.endsWith("/status") ||
    url.pathname === "/turn" ||
    url.pathname.startsWith("/agent/") ||
    url.pathname.startsWith("/auth/wallet/") ||
    url.pathname === "/auth/me" ||
    url.pathname === "/approve" ||
    url.pathname === "/reject"
  ) {
    return;
  }

  // Network-first for chain/health discovery (always fresh on first hit).
  if (
    url.pathname === "/health" ||
    url.pathname === "/auth/chains" ||
    url.pathname === "/bridge/chains" ||
    url.pathname === "/balance"
  ) {
    event.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(req, copy));
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Images / icons — cache-first.
  if (req.destination === "image") {
    event.respondWith(
      caches.match(req).then((cached) =>
        cached ||
        fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy));
          return res;
        })
      )
    );
    return;
  }

  // HTML / JS / CSS — stale-while-revalidate.
  if (
    req.destination === "document" ||
    req.destination === "script" ||
    req.destination === "style"
  ) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req).then((res) => {
          // Only cache successful, non-redirect responses
          if (res && res.status === 200 && res.type === "basic") {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy));
          }
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }
});

self.addEventListener("message", (event) => {
  // Allow the page to request immediate activation of the new version.
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});