import { removeModel, purgeExpired } from './offline.js';
const BASE = document.querySelector('meta[name="base-path"]')?.content || '';
const $ = (s) => document.querySelector(s);
const RECENT_KEY = 'fs3d.recent';
const CODE_KEY = 'fs3d.code';
const TTL_KEY = 'fs3d.ttl';
purgeExpired(BASE).catch(() => {});   // 이 기기에 저장된 모델 중 보관기간이 지난 것은 열 때마다 지운다
const MAX_SIDE = 2600;          // 서버 처리 해상도(2000px)보다 약간 크게만 보내 업로드 시간 단축
const files = [];               // { file, label, url }

const ICON = {
  up: '<svg class="i" viewBox="0 0 24 24"><path d="m6 14 6-6 6 6"/></svg>',
  x: '<svg class="i" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
};

// ------------------------------------------------------------ 파일 추가
function addFiles(list) {
  for (const f of list) {
    if (!f || !f.size) continue;
    const isImg = f.type.startsWith('image/');
    files.push({ file: f, label: '', url: isImg ? URL.createObjectURL(f) : null });
  }
  render();
}

$('#files').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
$('#cam').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });

const drop = $('#drop');
['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => addFiles(e.dataTransfer.files));
window.addEventListener('paste', (e) => {
  const items = [...(e.clipboardData?.files || [])];
  if (items.length) { e.preventDefault(); addFiles(items); }
});

function ext(name) { return (name.split('.').pop() || '').toUpperCase().slice(0, 4); }
function kb(n) { return n > 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`; }

function render() {
  const ul = $('#list');
  ul.innerHTML = '';
  files.forEach((it, i) => {
    const li = document.createElement('li');
    li.innerHTML = `
      <div class="thumb"></div>
      <div class="fname"><b></b><small></small></div>
      <input aria-label="층 이름" maxlength="12">
      <div class="row-btns"><button class="mini up" aria-label="위로">${ICON.up}</button><button class="mini rm" aria-label="삭제">${ICON.x}</button></div>`;
    const th = li.querySelector('.thumb');
    if (it.url) th.style.backgroundImage = `url("${it.url}")`; else th.textContent = ext(it.file.name);
    li.querySelector('b').textContent = it.file.name || '붙여넣은 이미지';
    li.querySelector('small').textContent = kb(it.file.size);
    const inp = li.querySelector('input');
    inp.value = it.label;
    inp.placeholder = `${i + 1}F`;
    inp.addEventListener('input', () => { it.label = inp.value; });
    li.querySelector('.up').disabled = i === 0;
    li.querySelector('.up').onclick = () => { files.splice(i - 1, 0, files.splice(i, 1)[0]); render(); };
    li.querySelector('.rm').onclick = () => { if (it.url) URL.revokeObjectURL(it.url); files.splice(i, 1); render(); };
    ul.appendChild(li);
  });
  $('#go').disabled = files.length === 0;
}

// ------------------------------------------------------------ 업로드 전 축소(휴대폰 원본 사진 대비)
async function shrink(file) {
  if (!file.type.startsWith('image/') || file.type === 'image/svg+xml') return file;
  try {
    const bmp = await createImageBitmap(file);
    const s = Math.max(bmp.width, bmp.height);
    if (s <= MAX_SIDE && file.size < 2.5e6) { bmp.close?.(); return file; }
    const f = Math.min(1, MAX_SIDE / s);
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * f); c.height = Math.round(bmp.height * f);
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close?.();
    const blob = await new Promise((r) => c.toBlob(r, 'image/jpeg', 0.9));
    return blob ? new File([blob], (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' }) : file;
  } catch { return file; }
}

// ------------------------------------------------------------ 생성
function setProgress(text, frac) {
  const p = $('#progress');
  p.hidden = false;
  p.querySelector('.ptext').textContent = text;
  const bar = p.querySelector('.bar');
  bar.classList.toggle('indet', frac == null);
  bar.querySelector('i').style.width = frac == null ? '' : `${Math.round(frac * 100)}%`;
}

function showError(msg) { const e = $('#err'); e.textContent = msg; e.hidden = !msg; }

// 공개 주소로 운영할 때는 접근 코드를 아는 사람만 업로드 가능
const store = {
  get: (k) => { try { return localStorage.getItem(k) || ''; } catch { return ''; } },
  set: (k, v) => { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch { /* */ } },
};
$('#ttl').value = ['1', '7', '30'].includes(store.get(TTL_KEY)) ? store.get(TTL_KEY) : '7';

// 남은 보관 시간을 사람이 읽는 말로(만료 시각이 없으면 무기한)
function remain(iso) {
  if (!iso) return '무기한 보관';
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return '만료됨';
  const h = ms / 3600000;
  return h < 1 ? '1시간 안에 삭제' : h < 24 ? `${Math.floor(h)}시간 뒤 삭제` : `${Math.ceil(h / 24)}일 뒤 삭제`;
}
const when = (iso) => new Date(iso).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' });
let codeRequired = false;
fetch(BASE + '/api/config').then((r) => r.json()).then((c) => {
  codeRequired = !!c.upload_code_required;
  $('#code-field').hidden = !codeRequired;
  $('#code').value = store.get(CODE_KEY);
}).catch(() => {});

$('#go').addEventListener('click', async () => {
  showError('');
  const code = $('#code').value.trim();
  if (codeRequired && !code) {
    showError('업로드 접근 코드를 입력하세요.');
    $('#code').focus();
    return;
  }
  $('#go').disabled = true;
  setProgress('이미지 준비 중…', null);
  const t0 = performance.now();
  const fd = new FormData();
  for (const it of files) {
    fd.append('files', await shrink(it.file), it.file.name || 'image.jpg');
    fd.append('labels', it.label.trim());
  }
  fd.append('name', $('#name').value.trim());
  if ($('#width').value) fd.append('width_m', $('#width').value);
  fd.append('wall_height', $('#height').value || '2.8');
  fd.append('ttl_days', $('#ttl').value);
  store.set(TTL_KEY, $('#ttl').value);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', BASE + '/api/models');
  xhr.responseType = 'json';
  if (code) xhr.setRequestHeader('X-Upload-Code', code);
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) setProgress(`업로드 ${Math.round((e.loaded / e.total) * 100)}%`, (e.loaded / e.total) * 0.5);
  };
  xhr.upload.onload = () => setProgress('3D 생성 중…', null);
  xhr.onerror = () => { $('#progress').hidden = true; showError('서버에 연결할 수 없습니다. 네트워크를 확인하세요.'); $('#go').disabled = false; };
  xhr.onload = () => {
    $('#progress').hidden = true;
    $('#go').disabled = false;
    if (xhr.status === 401) {
      store.set(CODE_KEY, '');
      $('#code-field').hidden = false;
      $('#code').select();
    }
    if (xhr.status !== 201) {
      showError(xhr.response?.detail || `생성 실패 (${xhr.status})`);
      return;
    }
    if (code) store.set(CODE_KEY, code);
    showResult(xhr.response, performance.now() - t0);
  };
  xhr.send(fd);
});

// ------------------------------------------------------------ 결과
const SCALE = { user: '입력값', dxf: 'CAD 단위', door: '문 폭 추정', wall: '벽 두께 추정', assumed: '미확인' };

// 자동 인식에서 사람이 꼭 봐야 할 점을 모델 정보로 계산한다(서버 경고와 별도)
function reviewWarnings(m) {
  const out = [];
  for (const f of m.floors) {
    const tag = m.floors.length > 1 ? `${f.label}: ` : '';
    if (f.scale_source === 'assumed') out.push(`${tag}축척을 확인하지 못해 건물 긴 변을 20m로 가정했습니다. 거리 값이 크게 틀릴 수 있습니다. 건물 가로 길이를 입력해 다시 올리는 것이 안전합니다.`);
    else if (f.scale_source === 'door' || f.scale_source === 'wall') out.push(`${tag}축척을 ${SCALE[f.scale_source]}으로 정했습니다. 거리·호스 길이는 참고용입니다.`);
    if (!f.doors) out.push(`${tag}문이 하나도 인식되지 않았습니다.`);
    if (!(f.entrances || []).length) out.push(`${tag}외부 출입문(진입구)을 찾지 못했습니다. 진입 위치를 따로 확인하세요.`);
    if (!f.windows && f.mode === 'double') out.push(`${tag}이 유형의 도면(2중선 CAD)은 창을 구분하지 못할 수 있습니다.`);
  }
  return [...out, ...(m.warnings || [])];
}

let current = null;

function showResult(m, ms) {
  current = m;
  const r = $('#result');
  r.hidden = false;
  $('#r-time').textContent = `${(ms / 1000).toFixed(1)}초`;
  $('#r-dot').className = 'dot warn';
  $('#r-head').textContent = '인식 결과를 확인해 주세요';
  $('#r-review').hidden = false;
  $('#r-sharebox').hidden = true;
  $('#r-rverr').hidden = true;
  $('#r-note').value = '';
  r.querySelectorAll('[data-chk]').forEach((c) => { c.checked = false; });
  $('#r-confirm').disabled = true;
  $('#r-prevs').innerHTML = m.floors.map((f) =>
    `<figure><img alt="${esc(f.label)} 인식 결과" src="${BASE}/m/${m.id}/${f.preview}"><figcaption><b>${esc(f.label)}</b> ${f.width_m}×${f.depth_m}m · 문 ${f.doors} · 창 ${f.windows}</figcaption></figure>`).join('');
  $('#r-floors').innerHTML = m.floors.map((f) =>
    `<li><b>${esc(f.label)}</b>축척 ${SCALE[f.scale_source] || ''}</li>`).join('');
  $('#r-warn').innerHTML = reviewWarnings(m).map((w) => `<li>⚠ ${esc(w)}</li>`).join('');
  $('#r-peek').href = m.local_url;
  $('#r-url').value = m.url;
  $('#r-open').href = m.local_url;
  $('#r-qr').src = m.qr;
  $('#r-share').onclick = () => share(m.url, m.name);
  $('#r-copy').onclick = () => copy(m.url, $('#r-copy'));
  r.scrollIntoView({ behavior: 'smooth', block: 'start' });
  saveRecent({ id: m.id, name: m.name, url: m.url, created: m.created, floors: m.floors.length, confirmed: false, expires: m.expires,
    preview: `${BASE}/m/${m.id}/${m.floors[0].preview}`, token: m.delete_token });
}

// 세 항목을 모두 체크해야 '확인 완료'가 눌린다
$('#result').addEventListener('change', (e) => {
  if (!e.target.matches('[data-chk]')) return;
  $('#r-confirm').disabled = ![...document.querySelectorAll('#result [data-chk]')].every((c) => c.checked);
});

$('#r-confirm').addEventListener('click', async () => {
  if (!current) return;
  const btn = $('#r-confirm');
  btn.disabled = true;
  const checks = Object.fromEntries([...document.querySelectorAll('#result [data-chk]')].map((c) => [c.dataset.chk, c.checked]));
  let res = null;
  try {
    res = await fetch(`${BASE}/api/models/${current.id}/confirm`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Delete-Token': current.delete_token || '' },
      body: JSON.stringify({ checks, note: $('#r-note').value }) });
  } catch { /* 네트워크 오류 */ }
  if (!res || !res.ok) {
    const err = res ? (await res.json().catch(() => ({}))).detail : '';
    $('#r-rverr').textContent = err || '확인을 기록하지 못했습니다. 연결을 확인하고 다시 눌러 주세요.';
    $('#r-rverr').hidden = false;
    btn.disabled = false;
    return;
  }
  const { confirmed } = await res.json();
  current.confirmed = confirmed;
  $('#r-dot').className = 'dot';
  $('#r-head').textContent = '확인 완료 · 공유할 수 있습니다';
  $('#r-review').hidden = true;
  $('#r-stamp').textContent = `인식 결과 확인됨 · ${new Date(confirmed.at).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })}${confirmed.note ? ' · ' + confirmed.note : ''} · ${current.expires ? when(current.expires) + ' 자동 삭제' : '자동 삭제 없음'}`;
  $('#r-sharebox').hidden = false;
  saveRecent({ id: current.id, name: current.name, url: current.url, created: current.created, floors: current.floors.length,
    confirmed: true, expires: current.expires, preview: `${BASE}/m/${current.id}/${current.floors[0].preview}`, token: current.delete_token });
});

$('#r-reject').addEventListener('click', async () => {
  if (!current || !confirm('인식 결과가 틀렸습니까? 이 모델을 서버에서 삭제하고 처음으로 돌아갑니다.')) return;
  const r = await fetch(`${BASE}/api/models/${current.id}`, { method: 'DELETE', headers: { 'X-Delete-Token': current.delete_token || '' } }).catch(() => null);
  if (!r || !(r.ok || r.status === 404)) { alert('삭제하지 못했습니다. 잠시 후 다시 시도하세요.'); return; }
  removeModel(BASE, current.id);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(loadRecent().filter((x) => x.id !== current.id))); } catch { /* */ }
  current = null;
  renderRecent();
  $('#r-new').click();
});

$('#r-new').addEventListener('click', () => {
  files.splice(0).forEach((it) => it.url && URL.revokeObjectURL(it.url));
  render();
  $('#result').hidden = true;
  $('#name').value = '';
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

async function share(url, name) {
  const data = { title: `${name || '건물'} 내부구조 3D`, text: `${name || '건물'} 내부구조 3D`, url };
  if (navigator.share) {
    try { await navigator.share(data); return; } catch (e) { if (e.name === 'AbortError') return; }
  }
  copy(url, $('#r-share'));
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); } catch {
    const i = $('#r-url'); i.select(); document.execCommand('copy');
  }
  const old = btn.innerHTML;
  btn.textContent = '복사됨 ✓';
  setTimeout(() => { btn.innerHTML = old; }, 1400);
}

function esc(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

// ------------------------------------------------------------ 최근 목록(이 기기)
function loadRecent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; } }
function saveRecent(item) {
  const list = [item, ...loadRecent().filter((x) => x.id !== item.id)].slice(0, 20);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* 저장 불가(사생활 모드) */ }
  renderRecent();
}
function renderRecent() {
  const list = loadRecent().filter((x) => !x.expires || new Date(x.expires) > Date.now());
  const sec = $('#recent');
  sec.hidden = list.length === 0;
  const ul = sec.querySelector('ul');
  ul.innerHTML = '';
  for (const it of list) {
    const li = document.createElement('li');
    const d = new Date(it.created);
    li.innerHTML = `<img alt="" loading="lazy"><div style="min-width:0"><a target="_blank" rel="noopener"></a><small></small></div>
      <button class="mini" aria-label="서버에서 삭제">${ICON.x}</button>`;
    li.querySelector('img').src = it.preview;
    li.querySelector('img').onerror = (e) => { e.target.style.visibility = 'hidden'; };
    const a = li.querySelector('a');
    a.href = `${BASE}/v/${it.id}`;
    a.textContent = it.name || '이름 없는 건물';
    li.querySelector('small').textContent = `${d.toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })} · ${it.floors}개 층${it.confirmed ? ' · 확인됨' : ' · ⚠ 인식 확인 전'} · ${remain(it.expires)}`;
    li.querySelector('button').onclick = async () => {
      if (!confirm(`'${it.name || '이름 없는 건물'}' 링크를 서버에서 삭제할까요? 공유한 링크도 열리지 않게 됩니다.`)) return;
      const r = await fetch(`${BASE}/api/models/${it.id}`, { method: 'DELETE', headers: { 'X-Delete-Token': it.token || '' } }).catch(() => null);
      if (r && (r.ok || r.status === 404)) {
        removeModel(BASE, it.id);
        try { localStorage.setItem(RECENT_KEY, JSON.stringify(loadRecent().filter((x) => x.id !== it.id))); } catch { /* */ }
        renderRecent();
      } else alert('삭제하지 못했습니다');
    };
    ul.appendChild(li);
  }
}
renderRecent();


// ------------------------------------------------------------ 일괄 삭제 / 서버 보관 모델 관리
$('#rc-all').addEventListener('click', async () => {
  const list = loadRecent();
  if (!list.length || !confirm(`이 기기에서 만든 링크 ${list.length}개를 모두 서버에서 삭제할까요? 공유한 링크도 모두 열리지 않게 됩니다.`)) return;
  let failed = 0;
  const keep = [];
  for (const it of list) {
    const r = await fetch(`${BASE}/api/models/${it.id}`, { method: 'DELETE', headers: { 'X-Delete-Token': it.token || '' } }).catch(() => null);
    if (!(r && (r.ok || r.status === 404))) { failed++; keep.push(it); } else removeModel(BASE, it.id);
  }
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(keep)); } catch { /* */ }
  renderRecent();
  if (failed) alert(`${failed}개는 삭제하지 못했습니다. 연결을 확인하고 다시 시도하세요.`);
});

function srvErr(msg) { const e = $('#srv-err'); e.textContent = msg || ''; e.hidden = !msg; }
function srvCode() {
  const code = ($('#code').value.trim() || store.get(CODE_KEY));
  if (!code) {
    $('#code-field').hidden = false;
    $('#code').focus();
    srvErr('위의 "업로드 접근 코드" 칸에 코드를 입력한 뒤 다시 누르세요.');
    return '';
  }
  return code;
}
async function srvCall(method, path, code) {
  const r = await fetch(`${BASE}${path}`, { method, headers: { 'X-Upload-Code': code } }).catch(() => null);
  if (!r) { srvErr('서버에 연결하지 못했습니다.'); return null; }
  if (r.status === 401) { store.set(CODE_KEY, ''); srvErr('업로드 접근 코드가 올바르지 않습니다.'); return null; }
  if (r.status === 429) { srvErr('코드를 여러 번 잘못 입력해 잠시 제한되었습니다. 10분 뒤 다시 시도하세요.'); return null; }
  if (!r.ok) { srvErr(`실패했습니다 (${r.status})`); return null; }
  return r;
}
let srvModels = [];
async function loadServer() {
  srvErr('');
  const code = srvCode();
  if (!code) return;
  const r = await srvCall('GET', '/api/models', code);
  if (!r) return;
  store.set(CODE_KEY, code);
  srvModels = (await r.json()).models;
  const ul = $('#srv-list');
  ul.innerHTML = '';
  $('#srv-all').hidden = srvModels.length === 0;
  if (!srvModels.length) { ul.innerHTML = '<li class="empty">서버에 보관 중인 모델이 없습니다.</li>'; return; }
  for (const m of srvModels) {
    const li = document.createElement('li');
    li.innerHTML = '<div style="min-width:0"><b></b><small></small></div><button class="mini" aria-label="삭제"></button>';
    li.querySelector('b').textContent = m.name || '이름 없는 건물';
    li.querySelector('small').textContent = `${when(m.created)} · ${m.floors}개 층 · ${m.confirmed ? '확인됨' : '⚠ 확인 전'} · ${remain(m.expires)}`;
    const b = li.querySelector('button');
    b.innerHTML = ICON.x;
    b.onclick = async () => {
      if (!confirm(`'${m.name || '이름 없는 건물'}' 모델을 서버에서 삭제할까요? 공유한 링크도 열리지 않게 됩니다.`)) return;
      if (await srvCall('DELETE', `/api/models/${m.id}`, code)) {
        removeModel(BASE, m.id);
        try { localStorage.setItem(RECENT_KEY, JSON.stringify(loadRecent().filter((x) => x.id !== m.id))); } catch { /* */ }
        renderRecent(); loadServer();
      }
    };
    ul.appendChild(li);
  }
}
$('#srv-load').addEventListener('click', loadServer);
$('#srv-all').addEventListener('click', async () => {
  const code = srvCode();
  if (!code || !srvModels.length) return;
  if (!confirm(`서버에 보관 중인 모델 ${srvModels.length}개를 모두 삭제할까요? 다른 사람이 올린 것도 포함됩니다. 되돌릴 수 없습니다.`)) return;
  let failed = 0;
  for (const m of srvModels) { if (!(await srvCall('DELETE', `/api/models/${m.id}`, code))) failed++; else removeModel(BASE, m.id); }
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([])); } catch { /* */ }
  renderRecent(); loadServer();
  if (failed) srvErr(`${failed}개는 삭제하지 못했습니다.`);
});

// 업로드·삭제·접근 실패 기록(상황실이 누가 언제 했는지 확인). 접근 코드 값·건물명·메모는 서버가 기록하지 않는다.
const EVENT = {
  upload: '업로드', delete: '삭제', confirm: '인식 확인', annotations: '표시 편집', list: '목록 조회', expire: '자동 만료',
  code_fail: '⚠ 코드 오입력', code_locked: '⚠ 코드 입력 제한', delete_denied: '⚠ 삭제 거부',
};
$('#audit-load').addEventListener('click', async () => {
  srvErr('');
  const code = srvCode();
  if (!code) return;
  const r = await srvCall('GET', '/api/audit?limit=100', code);
  if (!r) return;
  store.set(CODE_KEY, code);
  const { events, keep_days: keep } = await r.json();
  const note = $('#audit-note');
  note.textContent = `최근 ${events.length}건 · 접속 IP와 모델 번호만 남고 코드·건물명·메모는 기록하지 않습니다 · ${keep > 0 ? keep + '일 뒤 자동 삭제' : '자동 삭제 없음'}`;
  note.hidden = false;
  const ul = $('#audit-list');
  ul.innerHTML = '';
  if (!events.length) { ul.innerHTML = '<li class="empty">기록이 없습니다.</li>'; return; }
  for (const ev of events) {
    const li = document.createElement('li');
    li.innerHTML = '<div style="min-width:0"><b></b><small></small></div>';
    li.querySelector('b').textContent = `${EVENT[ev.ev] || ev.ev}${ev.id ? ' · ' + ev.id : ''}`;
    const bits = [when(ev.t)];
    if (ev.ip) bits.push(ev.ip);
    if (ev.floors != null) bits.push(`${ev.floors}개 층 ${ev.kb}KB`);
    if (ev.ttl != null) bits.push(`${ev.ttl}일 보관`);
    if (ev.by) bits.push(ev.by === 'code' ? '코드로 삭제' : '토큰으로 삭제');
    if (ev.count != null) bits.push(`표시 ${ev.count}개`);
    li.querySelector('small').textContent = bits.join(' · ');
    ul.appendChild(li);
  }
});
