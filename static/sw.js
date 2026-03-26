// Service worker — PWA install + push notifications

self.addEventListener("fetch", (event) => {
    event.respondWith(fetch(event.request));
});

self.addEventListener("push", (event) => {
    if (!event.data) return;

    const data = event.data.json();
    const title = data.title || "Claude Dashboard";
    const options = {
        body: data.body || "A session needs attention",
        icon: "/static/icon-192.png",
        badge: "/static/icon-192.png",
        tag: data.tag || "claude-notification",
        renotify: true,
        data: data.data || {},
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
            for (const client of windowClients) {
                if (client.url.includes(self.location.origin)) {
                    return client.focus();
                }
            }
            return clients.openWindow("/");
        })
    );
});
