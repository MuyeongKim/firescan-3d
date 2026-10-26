// 뷰어 시작 파일: WebGL 을 쓸 수 있으면 3D 화면(viewer3d.js)을 불러오고, 쓸 수 없으면 평면 미리보기 화면을 보여 준다.
// (viewer3d.js 는 맨 위에서 WebGL 렌더러를 만들기 때문에 WebGL 이 없는 기기에서는 모듈이 통째로 실패해 로딩 화면만 남았다.)
import { removeModel, purgeExpired } from './offline.js';

const BASE = document.querySelector('meta[name="base-path"]')?.content || '';
const MID = document.body.dataset.model;
const $ = (s) => document.querySelector(s);

function webglOk() {
  try {
    const c = document.createElement('canvas');
    return !!(self.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl')));
  } catch { return false; }
}

const KIND = { room: '방 이름', stairs: '계단', exit: '비상구', hydrant: '소화전', extinguisher: '소화기', firedoor: '방화문', hazard: '위험' };
const COLOR = { room: '#e8edf2', stairs: '#b08cff', exit: '#22c55e', hydrant: '#ff5c5c', extinguisher: '#ff8a4c', firedoor: '#ffb020', hazard: '#ff3b3b' };
const when = (iso) => new Date(iso).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' });

export async function showFallback(why) {
  document.body.classList.add('no-webgl');
  const box = $('#fallback');
  box.hidden = false;
  if (why) $('#fb-why').textContent = why;
  purgeExpired(BASE).catch(() => {});
  let meta;
  try {
    const res = await fetch(`${BASE}/api/models/${MID}`);
    if (!res.ok) throw new Error(res.status === 404 ? 'gone' : 'http');
    meta = await res.json();
  } catch (e) {
    if (e.message === 'gone') removeModel(BASE, MID);
    $('#fb-title').textContent = e.message === 'gone' ? '링크가 만료되었거나 삭제되었습니다.' : '네트워크에 연결할 수 없습니다. 신호가 잡히는 곳에서 다시 열어주세요.';
    return;
  }
  $('#fb-title').textContent = meta.name || '건물 내부구조';
  const confirmed = meta.confirmed && meta.confirmed.at
    ? `✓ 인식 결과 확인됨 · ${when(meta.confirmed.at)}${meta.confirmed.note ? ' · ' + meta.confirmed.note : ''}`
    : '⚠ 자동 인식 결과 · 사람이 아직 확인하지 않았습니다. 실제 구조와 다를 수 있습니다';
  const exp = meta.expires ? ` · ${when(meta.expires)} 자동 삭제` : '';
  const state = document.createElement('p');
  state.textContent = confirmed + exp;
  $('#fb-title').after(state);

  const nav = $('#fb-floors');
  const show = (i) => {
    const f = meta.floors[i];
    $('#fb-img').src = `${BASE}/m/${MID}/${f.preview}`;
    $('#fb-cap').textContent = `${f.label} · ${f.width_m}×${f.depth_m}m · 문 ${f.doors} · 창 ${f.windows}`;
    [...nav.children].forEach((b, k) => b.classList.toggle('on', k === i));
  };
  meta.floors.forEach((f, i) => {
    const b = document.createElement('button');
    b.textContent = f.label;
    b.onclick = () => show(i);
    nav.appendChild(b);
  });
  nav.hidden = meta.floors.length < 2;
  show(0);

  // 방 이름·소방시설 표시는 3D 위가 아니라 층별 목록으로 보여 준다(글자는 textContent 로만 출력)
  const items = meta.annotations || [];
  if (items.length) {
    const wrap = $('#fb-annots');
    meta.floors.forEach((f, i) => {
      const mine = items.filter((a) => a.floor === i);
      if (!mine.length) return;
      const h = document.createElement('h3');
      h.textContent = `${f.label} 표시`;
      const ul = document.createElement('ul');
      for (const a of mine) {
        const li = document.createElement('li');
        li.style.setProperty('--kc', COLOR[a.kind] || '#e8edf2');
        li.textContent = a.kind === 'room' ? a.text : `${KIND[a.kind] || a.kind}${a.text ? ' · ' + a.text : ''}`;
        ul.appendChild(li);
      }
      wrap.append(h, ul);
    });
  }
}

async function boot() {
  if (!webglOk()) { await showFallback(); return; }
  try {
    await import('./viewer3d.js');
  } catch (e) {
    // WebGL 컨텍스트를 만들지 못했거나 3D 모듈을 불러오지 못한 경우
    console.error(e);
    await showFallback('3D 화면을 시작하지 못했습니다(브라우저의 WebGL 사용이 제한되었거나 메모리가 부족할 수 있습니다). 아래 평면 미리보기로 확인하세요.');
  }
}
boot();
