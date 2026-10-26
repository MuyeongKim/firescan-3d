// 오프라인 저장(미리 저장)과 캐시 정리. sw.js 와 같은 캐시 이름을 쓴다.
// 개인정보 원칙: 휴대폰에 저장된 도면도 서버의 보관기간·삭제를 따라야 하므로
//  - 만료 시각이 지난 모델은 페이지를 열 때마다(오프라인이어도 기기 시계로) 지우고,
//  - 서버가 404 를 주면 sw.js 가 그 모델의 캐시를 지운다.
export const CACHE = 'fs3d-v2';   // sw.js 의 CACHE 와 같아야 한다

export const supported = () => typeof caches !== 'undefined' && self.isSecureContext;

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
const modelRe = (base, id) => new RegExp(`^${esc(base)}/(?:api/models|v|m|qr)/${id ? esc(id) : '([A-Za-z0-9_-]+)'}(?:[/.]|$)`);

export function urlsFor(base, meta) {
  const id = meta.id;
  const out = [`${base}/v/${id}`, `${base}/api/models/${id}`];
  for (const f of meta.floors || []) out.push(`${base}/m/${id}/${f.glb}`, `${base}/m/${id}/${f.preview}`);
  return out;
}

export async function saveModel(base, meta, onStep) {
  const c = await caches.open(CACHE);
  const urls = urlsFor(base, meta);
  let n = 0;
  for (const u of urls) {
    await c.add(new Request(u, { cache: 'reload' }));   // 서버에서 새로 받아 저장
    onStep?.(++n, urls.length);
  }
}

export async function isSaved(base, meta) {
  if (!supported()) return false;
  const c = await caches.open(CACHE);
  for (const u of urlsFor(base, meta)) if (!(await c.match(u))) return false;
  return true;
}

export async function removeModel(base, id) {
  if (!supported() || !id) return;
  const c = await caches.open(CACHE);
  const re = modelRe(base, id);
  for (const k of await c.keys()) if (re.test(new URL(k.url).pathname)) await c.delete(k);
}

// 저장해 둔 모델 가운데 만료 시각이 지난 것을 지운다(네트워크 없이 기기 시계로 판단)
export async function purgeExpired(base) {
  if (!supported()) return 0;
  const c = await caches.open(CACHE);
  const metaRe = new RegExp(`^${esc(base)}/api/models/([A-Za-z0-9_-]+)$`);
  let removed = 0;
  for (const k of await c.keys()) {
    const m = metaRe.exec(new URL(k.url).pathname);
    if (!m) continue;
    try {
      const meta = await (await c.match(k)).clone().json();
      if (meta.expires && new Date(meta.expires) < new Date()) { await removeModel(base, m[1]); removed++; }
    } catch { /* 읽을 수 없는 항목은 그대로 둔다 */ }
  }
  return removed;
}
