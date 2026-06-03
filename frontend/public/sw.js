// Service worker for pdf-reader PWA
// Strategy:
//   - App shell (index.html, /assets/*, manifest, favicon): cache-first, refreshed on activate
//   - Document assets (audio/markdown/pdf/mp3/images/json): network-first, cached on success
//   - "PRECACHE_URLS" message from client: explicit bulk-cache for offline-save
const VERSION = "v5";
const SHELL_CACHE = `app-shell-${VERSION}`;
const DOC_CACHE = `docs-${VERSION}`;

const SHELL_URLS = [
  "/",
  "/index.html",
  "/manifest.json",
  "/favicon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      await cache.addAll(SHELL_URLS).catch(() => {});
      self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => !k.endsWith(VERSION)).map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

function isAppShell(url) {
  return (
    url.pathname === "/" ||
    url.pathname === "/index.html" ||
    url.pathname === "/manifest.json" ||
    url.pathname === "/favicon.svg" ||
    url.pathname.startsWith("/assets/")
  );
}

function isDocAsset(url) {
  return (
    url.pathname.startsWith("/api/projects/") &&
    (url.pathname.includes("/audio/") ||
      url.pathname.includes("/image/") ||
      url.pathname.endsWith("/markdown") ||
      url.pathname.endsWith("/audiobook.mp3") ||
      url.pathname.endsWith("/source.pdf") ||
      /\/docs\/[^/]+$/.test(url.pathname))
  );
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request, { ignoreSearch: false });
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) cache.put(request, res.clone());
  return res;
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(request);
    if (res.ok) cache.put(request, res.clone());
    return res;
  } catch (e) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw e;
  }
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);

  // SPA navigation: try network for index.html, fall back to cached shell offline
  if (event.request.mode === "navigate") {
    event.respondWith((async () => {
      try {
        const res = await fetch(event.request);
        if (res.ok) {
          const cache = await caches.open(SHELL_CACHE);
          cache.put("/index.html", res.clone());
        }
        return res;
      } catch {
        const cache = await caches.open(SHELL_CACHE);
        const cached = await cache.match("/index.html");
        if (cached) return cached;
        throw new Error("offline and no cached shell");
      }
    })());
    return;
  }

  // Static JS/CSS assets: cache-first (immutable, hash-named)
  if (url.pathname.startsWith("/assets/") || url.pathname === "/manifest.json" || url.pathname === "/favicon.svg") {
    event.respondWith(cacheFirst(event.request, SHELL_CACHE));
    return;
  }

  if (isDocAsset(url) || url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(event.request, DOC_CACHE));
    return;
  }
});

self.addEventListener("message", (event) => {
  const data = event.data;
  if (!data || data.type !== "PRECACHE_URLS") return;
  const urls = Array.isArray(data.urls) ? data.urls : [];
  const port = event.ports?.[0];
  event.waitUntil(
    (async () => {
      const cache = await caches.open(DOC_CACHE);
      let done = 0;
      let failed = 0;
      for (const u of urls) {
        try {
          const res = await fetch(u);
          if (res.ok) {
            await cache.put(u, res.clone());
            done++;
          } else {
            failed++;
          }
        } catch {
          failed++;
        }
        if (port) port.postMessage({ done, failed, total: urls.length });
      }
      if (port) port.postMessage({ done, failed, total: urls.length, finished: true });
    })()
  );
});
