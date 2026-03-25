// Minimal service worker — required for PWA install prompt.
// No offline caching needed since this is a local dashboard.

self.addEventListener("fetch", (event) => {
    event.respondWith(fetch(event.request));
});
