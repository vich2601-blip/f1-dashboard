const CACHE_NAME = 'f1-dash-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/static/css/style.css',
  '/static/js/main.js',
  '/static/manifest.json',
  'https://fonts.googleapis.com/css2?family=Titillium+Web:wght@300;400;600;700;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap'
];

// Install Service Worker and cache core files
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('[Service Worker] Pre-caching static assets');
        return cache.addAll(ASSETS_TO_CACHE);
      })
      .then(() => self.skipWaiting())
  );
});

// Activate SW and prune old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== CACHE_NAME) {
            console.log('[Service Worker] Pruning old cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Intercept requests and fallback offline
self.addEventListener('fetch', event => {
  // Only intercept GET requests
  if (event.request.method !== 'GET') return;

  const url = new RegExp(self.location.origin);
  const isLocalRequest = url.test(event.request.url);

  event.respondWith(
    caches.match(event.request)
      .then(cachedResponse => {
        if (cachedResponse) {
          // Fetch update in background for local static assets (Stale-While-Revalidate)
          if (isLocalRequest && event.request.url.includes('/static/')) {
            fetch(event.request)
              .then(networkResponse => {
                if (networkResponse.status === 200) {
                  caches.open(CACHE_NAME).then(cache => cache.put(event.request, networkResponse));
                }
              })
              .catch(() => {/* Ignore offlines */});
          }
          return cachedResponse;
        }

        // Try network for non-cached URLs
        return fetch(event.request)
          .then(networkResponse => {
            // Cache successful pages/json dynamic responses on-the-fly
            if (networkResponse && networkResponse.status === 200 && isLocalRequest) {
              const responseToCache = networkResponse.clone();
              caches.open(CACHE_NAME).then(cache => {
                // Avoid caching live polling /api/live/ responses
                if (!event.request.url.includes('/api/live/')) {
                  cache.put(event.request, responseToCache);
                }
              });
            }
            return networkResponse;
          })
          .catch(() => {
            // Offline fallback
            console.log('[Service Worker] Network request failed. Serving offline fallback.');
            // Return cached index page as generic fallback
            return caches.match('/');
          });
      })
  );
});
