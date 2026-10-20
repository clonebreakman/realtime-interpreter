/*
 * 实时同传 · 中→英 — Service Worker
 *
 * 只缓存 web/ 静态资源；不拦截 /api/* 与 WebSocket。
 * 页面导航使用 network-first（离线时回退到缓存的 index.html），
 * 其余同源 GET 使用 cache-first + 后台更新。
 *
 * 语法检查：node --check web/sw.js
 */

const CACHE_NAME = 'rti-web-v2';
const STATIC_ASSETS = [
  './',
  './index.html',
  './studio.html',
  './duo.html',
  './style.css',
  './studio.css',
  './duo.css',
  './app.js',
  './studio.js',
  './duo.js',
  './pcm-worklet.js',
  './manifest.webmanifest',
  './icon-192.png',
  './icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
      .catch((err) => {
        console.warn('[rti-sw] 预缓存失败', err);
        return self.skipWaiting();
      })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.map((key) => {
            if (key !== CACHE_NAME) return caches.delete(key);
            return Promise.resolve(false);
          })
        )
      )
      .then(() => self.clients.claim())
  );
});

function isApiRequest(url) {
  return url.pathname.startsWith('/api/') || url.pathname.endsWith('/ws');
}

function putInCache(request, response) {
  if (!response || !response.ok || response.type !== 'basic') return response;
  const copy = response.clone();
  caches
    .open(CACHE_NAME)
    .then((cache) => cache.put(request, copy))
    .catch(() => {});
  return response;
}

function cacheFirst(request) {
  return caches.match(request).then((cached) => {
    if (cached) return cached;
    return fetch(request).then((response) => putInCache(request, response));
  });
}

function networkFirst(request) {
  return fetch(request)
    .then((response) => putInCache(request, response))
    .catch(() =>
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return caches.match('./index.html');
      })
    );
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  let url;
  try {
    url = new URL(request.url);
  } catch (err) {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (isApiRequest(url)) return;
  event.respondWith(request.mode === 'navigate' ? networkFirst(request) : cacheFirst(request));
});
