const CACHE = 'rjsheetal-shell-v10';
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', '/app.js', '/redesign.css', '/rebuild.css', '/persona.css', '/rj-lazy.js', '/spotify-personal.js'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key !== CACHE).map(key => caches.delete(key))
  )));
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== 'GET' || url.pathname === '/api/stream') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
