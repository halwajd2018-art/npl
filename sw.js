/* Cache only public static assets. Never cache reports, customer data, authentication, or uploads. */
const STATIC_CACHE = 'adf-static-v1';
const SAFE_ASSETS = ['/static/style.css', '/static/app.js', '/static/app-icon-192.png', '/static/app-icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(STATIC_CACHE).then(cache => cache.addAll(SAFE_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== STATIC_CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !SAFE_ASSETS.includes(url.pathname)) return;
  event.respondWith(fetch(request).then(response => {
    if (response.ok) { const copy=response.clone(); caches.open(STATIC_CACHE).then(cache=>cache.put(request,copy)); }
    return response;
  }).catch(() => caches.match(request)));
});
