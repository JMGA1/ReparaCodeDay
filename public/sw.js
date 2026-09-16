const CACHE = 'repara-shell-v11.8';
const SHELL = ['/', '/index.html', '/style.css', '/app.js', '/favicon.svg', '/icon-192.png', '/icon-512.png', '/manifest.webmanifest', '/vendor/leaflet/leaflet.js', '/vendor/leaflet/leaflet.css'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))));
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => (k.startsWith('ciudad-visible-shell-') || k.startsWith('repara-shell-')) && k !== CACHE).map(k => caches.delete(k))))));
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  // No cachear API, fotos de vecinos ni mapas externos. No se encolan envíos.
  if (e.request.method !== 'GET' || u.origin !== self.location.origin || !SHELL.includes(u.pathname)) return;
  e.respondWith(fetch(e.request).then(response => {
    if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return response;
  }).catch(() => caches.match(e.request)));
});
