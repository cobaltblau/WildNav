// WildNav service worker: makes the app work without a connection.
//  - app (index.html, icons, manifest):  network first, cached copy when offline
//  - libraries and fonts from the CDNs:  cache first (their URLs carry a version)
//  - data/ tiles (URLs carry ?v=<build>): cache first; old builds are removed on request
//  - data/index.json, protected.json:    network first
//  - background map images:              network first (4 s), cached copy when offline;
//                                         only images that were viewed, capped at MAP_MAX
//  - everything else (Overpass, search):  not touched
// Bump VERSION when this file's caching rules change.
const VERSION = 'v1';
const APP = `wn-app-${VERSION}`, LIB = 'wn-lib', DATA = 'wn-data', MAP = 'wn-map';
const MAP_MAX = 3000;
const APP_FILES = ['./', 'index.html', 'manifest.webmanifest', 'icons/apple-touch-icon.png', 'icons/icon-192.png'];
const LIB_HOSTS = ['cdn.jsdelivr.net', 'cdnjs.cloudflare.com'];
const MAP_HOSTS = ['tile.openstreetmap.org', 'tile.opentopomap.org', 'tiles.maps.eox.at'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(APP).then(c => c.addAll(APP_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith('wn-app-') && k !== APP) await caches.delete(k);
    await self.clients.claim();
  })());
});

async function cacheFirst(req, name) {
  const c = await caches.open(name), hit = await c.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) c.put(req, res.clone());           // opaque (no-CORS) responses are not cached: they eat quota
  return res;
}

async function networkFirst(req, name, timeoutMs) {
  const c = await caches.open(name);
  try {
    const net = fetch(req);
    const res = await (timeoutMs ? Promise.race([net, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), timeoutMs))]) : net);
    if (res.ok) { c.put(req, res.clone()); if (name === MAP) trimSoon(); }
    return res;
  } catch (err) {
    const hit = await c.match(req, { ignoreSearch: name === APP });
    if (hit) return hit;
    throw err;
  }
}

// keep the map image cache below MAP_MAX entries (oldest first)
let trimT = null;
function trimSoon() {
  if (trimT) return;
  trimT = setTimeout(async () => {
    trimT = null;
    const c = await caches.open(MAP), keys = await c.keys();
    for (let i = 0; i < keys.length - MAP_MAX; i++) await c.delete(keys[i]);
  }, 10000);
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const scope = new URL(self.registration.scope);
  if (url.origin === scope.origin && url.pathname.startsWith(scope.pathname)) {
    const path = url.pathname.slice(scope.pathname.length);
    if (path.startsWith('data/12/')) e.respondWith(cacheFirst(req, DATA));
    else if (path.startsWith('data/')) e.respondWith(networkFirst(req, DATA));
    else e.respondWith(networkFirst(req, APP));
  } else if (LIB_HOSTS.includes(url.hostname)) {
    e.respondWith(cacheFirst(req, LIB));
  } else if (MAP_HOSTS.some(h => url.hostname === h || url.hostname.endsWith('.' + h))) {
    e.respondWith(networkFirst(req, MAP, 4000));
  }
});

// The app tells us the current data build; tiles of older builds are removed.
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'data-version') e.waitUntil((async () => {
    const c = await caches.open(DATA), v = 'v=' + encodeURIComponent(e.data.version);
    for (const req of await c.keys()) if (req.url.includes('/data/12/') && !req.url.includes(v)) await c.delete(req);
  })());
});
