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

const CACHE_NAME = "ubotpay-v7";
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
  if (url.hostname !== self.location.hostname) {
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
  // Audit 20 Oct round 5: /balance was in this list and therefore cached. It
  // returns the *signed-in user's* balance, which then sat in Cache Storage —
  // readable by anyone who opens devtools on a shared device, and surviving
  // logout (renderDisconnected clears localStorage but not the caches). Only
  // cache responses that carry no user data: 2xx bodies here are all static
  // discovery data, and a 401 is not worth storing either.
  if (
    url.pathname === "/health" ||
    url.pathname === "/auth/chains" ||
    url.pathname === "/bridge/chains"
  ) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Anything user-scoped goes straight to the network, never to the cache.
  if (url.pathname === "/balance" || url.pathname.startsWith("/history")) {
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

// ───────── Web Push handler ─────────
// Fired by the browser when the server sends a push message via VAPID.
// payload is JSON: { title, body, url, tag, icon }
self.addEventListener("push", (event) => {
  let data = { title: "Ubotpay", body: "You have a new update.", url: "/" };
  try {
    if (event.data) {
      const parsed = event.data.json();
      data = Object.assign(data, parsed);
    }
  } catch (e) {
    if (event.data) data.body = event.data.text();
  }
  const options = {
    body: data.body,
    icon: data.icon || "/icon-192.png",
    badge: "/icon-72.png",        // optional small badge
    tag: data.tag || undefined,
    data: { url: data.url || "/" },
    requireInteraction: false,
    renotify: true,
    vibrate: [200, 100, 200],
    actions: [
      { action: "open", title: "Open Ubotpay" },
      { action: "dismiss", title: "Dismiss" },
    ],
  };
  event.waitUntil(
    self.registration.showNotification(data.title, options).catch((e) =>
      console.error("[sw] showNotification failed:", e)
    )
  );
});

// Fired when the user clicks (or taps) the notification.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      // Try to focus an existing tab
      for (const w of wins) {
        if (w.url.includes(self.location.origin)) {
          w.navigate ? w.navigate(url) : (w.location.href = url);
          return w.focus();
        }
      }
      // No tab open → open a new one
      return self.clients.openWindow(url);
    })
  );
});


// ───────── Background Sync + Outbox ─────────
// IndexedDB queue for actions that need to happen server-side but
// couldn't complete right now (user offline, network error, etc.).
// The 'sync' event handler runs the queue when the browser decides
// the network is healthy again. The 'online' event is a fallback
// for browsers without Background Sync API (Safari, Firefox).

const DB_NAME = "ubotpay-outbox";
const DB_VERSION = 1;
const STORE = "outbox";

function openOutboxDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
        store.createIndex("created_at", "created_at", { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function addToOutbox(action) {
  // action: { type: "bridge.start", payload: {...}, created_at: Date.now() }
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const req = store.add(action);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function listOutbox() {
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const store = tx.objectStore(STORE);
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function removeFromOutbox(id) {
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const req = store.delete(id);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

async function processOutbox() {
  const items = await listOutbox();
  if (!items.length) return { processed: 0, remaining: 0 };
  let processed = 0;
  const failed = [];
  for (const item of items) {
    try {
      // Audit H8: validate URL is on our origin (no JWT leak)
      const itemUrl = new URL(item.url, self.location.origin);
      if (itemUrl.hostname !== self.location.hostname) {
        await removeFromOutbox(item.id);
        continue;
      }
      // Replay the action by calling the same endpoint. The body
      // includes a token from the original action payload (it was
      // stored at queue time).
      const res = await fetch(item.url, {
        method: item.method || "POST",
        headers: item.headers || { "Content-Type": "application/json" },
        body: item.body ? JSON.stringify(item.body) : undefined,
      });
      if (res.ok || res.status === 200 || res.status === 201) {
        await removeFromOutbox(item.id);
        processed++;
        // Notify the user that the queued payment went through
        try {
          if (item.notify_on_complete) {
            const data = await res.json().catch(() => ({}));
            const title = item.notify_on_complete.title || "✅ Payment sent";
            const body = (item.notify_on_complete.body || "Your queued payment was sent.")
              .replace("{amount}", (data.amount_usdc || item.body?.amount_usdc || ""))
              .replace("{to}", (item.body?.to_address || "").slice(0, 10) + "…");
            await self.registration.showNotification(title, {
              body,
              icon: "/icon-192.png",
              tag: "outbox-" + item.id,
              data: { url: item.notify_on_complete.url || "/analytics" },
              vibrate: [200, 100, 200],
            });
          }
        } catch (e) { /* ignore notify errors */ }
      } else {
        // Server returned an error (4xx, 5xx). Drop the action so we
        // don't loop forever; the user gets the failed status in the
        // app next time it opens.
        await removeFromOutbox(item.id);
      }
    } catch (e) {
      // Network still flaky. Leave the item in the queue.
      failed.push(item.id);
    }
  }
  return { processed, remaining: failed.length };
}

// Background Sync event — fires when the browser decides connectivity
// is back. Available in Chrome, Edge, Opera. Not in Safari or Firefox.
self.addEventListener("sync", (event) => {
  if (event.tag === "ubotpay-outbox-flush") {
    event.waitUntil(processOutbox());
  }
});

// Periodic Sync — fires on a regular interval when the user has
// granted background sync permission. Used as a backup for actions
// that have been queued for >24h.
self.addEventListener("periodicsync", (event) => {
  if (event.tag === "ubotpay-outbox-periodic") {
    event.waitUntil(processOutbox());
  }
});

// Online event — works on all browsers. Fires when the SW detects
// the device just regained connectivity. We use this as a guaranteed
// fallback for when Background Sync isn't available.
self.addEventListener("online", () => {
  processOutbox().catch((e) => console.warn("[sw] online flush failed", e));
});

// Message API: lets the page ask the SW to process the queue
// explicitly (e.g. user clicks "Retry" in the UI).
self.addEventListener("message", (event) => {
  if (event.data === "FLUSH_OUTBOX") {
    event.waitUntil(
      processOutbox().then((r) => {
        if (event.source && event.source.postMessage) {
          event.source.postMessage({ type: "OUTBOX_FLUSHED", ...r });
        }
      })
    );
    return;
  }
  // BroadcastChannel messages from the page (queue / query).
  // The page may not have a client reference (e.g. tab closed) but
  // BroadcastChannel still reaches the SW as long as it's active.
});

// BroadcastChannel: lets the page ask the SW to add an outbox entry
// or query the count. Works even when the SW has no direct client.
const outboxCh = (() => {
  try {
    const ch = new BroadcastChannel("ubotpay-outbox");
    ch.onmessage = async (event) => {
      const msg = event.data;
      if (!msg) return;
      if (msg.type === "ADD_TO_OUTBOX") {
        await addToOutbox(msg.action);
        const count = (await listOutbox()).length;
        ch.postMessage({ type: "OUTBOX_UPDATED", count });
      } else if (msg.type === "GET_OUTBOX_COUNT") {
        const count = (await listOutbox()).length;
        ch.postMessage({ type: "OUTBOX_UPDATED", count });
      }
    };
    return ch;
  } catch (e) { return null; }
})();

// Broadcast the queue size whenever it changes (from inside the SW).
const _origAdd = addToOutbox;
const _origRemove = removeFromOutbox;
async function notifyQueueChanged() {
  if (!outboxCh) return;
  const count = (await listOutbox()).length;
  outboxCh.postMessage({ type: "OUTBOX_UPDATED", count });
}
