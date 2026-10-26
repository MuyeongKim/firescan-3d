const BASE = '{{BASE}}';
// 한 번 열어본 모델은 현장(지하·철골 구조물 등 신호 불량)에서도 다시 열 수 있도록 캐시한다.
const CACHE = 'fs3d-v2';
const SHELL = [
  BASE + '/static/app.css',
  BASE + '/static/viewer.js',
  BASE + '/static/icon.svg',
  BASE + '/static/vendor/three/three.module.min.js',
  BASE + '/static/vendor/three/three.core.min.js',
  BASE + '/static/vendor/three/addons/controls/OrbitControls.js',
  BASE + '/static/vendor/three/addons/loaders/GLTFLoader.js',
  BASE + '/static/vendor/three/addons/utils/BufferGeometryUtils.js',
  BASE + '/static/vendor/three/addons/utils/SkeletonUtils.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function cacheFirst(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) (await caches.open(CACHE)).put(req, res.clone());
  return res;
}

async function networkFirst(req) {
  let res;
  try {
    res = await fetch(req);
  } catch (err) {
    const hit = await caches.match(req);
    if (hit) return hit;
    throw err;
  }
  if (res.ok) (await caches.open(CACHE)).put(req, res.clone());
  else if (res.status >= 500) {
    // 서버 PC가 꺼져 터널이 오류 페이지를 줄 때도 저장해 둔 화면을 보여준다
    const hit = await caches.match(req);
    if (hit) return hit;
  }
  return res;
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const u = new URL(req.url);
  if (u.origin !== location.origin) return;
  if (u.pathname.startsWith(BASE + '/m/') || u.pathname.startsWith(BASE + '/static/vendor/')) e.respondWith(cacheFirst(req));
  else if (u.pathname.startsWith(BASE + '/v/') || u.pathname.startsWith(BASE + '/api/models/') || u.pathname.startsWith(BASE + '/static/')) e.respondWith(networkFirst(req));
});
