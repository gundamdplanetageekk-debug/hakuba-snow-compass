// Offline support: network first, last good copy when there is no signal (e.g. on the mountain).
const CACHE = 'hsc-v1';
const SHELL = ['/', '/index.html', '/engine.js', '/data/resorts.json', '/data/safety.json', '/data/shuttle.json', '/icon-192.png'];
const DATA_HOST = 'raw.githubusercontent.com';

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const same = url.origin === self.location.origin;
  if (!(same || url.hostname === DATA_HOST)) return;      // never touch partner / analytics requests
  if (same && url.pathname.startsWith('/go/')) return;   // outbound redirects stay live
  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(url.origin + url.pathname, copy)); }
      return res;
    }).catch(() => caches.match(req, { ignoreSearch: true }).then((r) => r || (req.mode === 'navigate' ? caches.match('/') : Response.error())))
  );
});
