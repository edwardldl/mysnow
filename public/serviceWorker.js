/*
 * Compatibility endpoint for browsers that still have the legacy
 * /serviceWorker.js registration. The current PWA worker is /sw.js.
 */
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.registration.unregister());
});
