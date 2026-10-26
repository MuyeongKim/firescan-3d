const $ = (s) => document.querySelector(s);
const RECENT_KEY = 'fs3d.recent';
const CODE_KEY = 'fs3d.code';
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
let codeRequired = false;
fetch('/api/config').then((r) => r.json()).then((c) => {
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

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/models');
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

function showResult(m, ms) {
  const r = $('#result');
  r.hidden = false;
  $('#r-time').textContent = `${(ms / 1000).toFixed(1)}초`;
  $('#r-prev').src = `/m/${m.id}/${m.floors[0].preview}`;
  $('#r-url').value = m.url;
  $('#r-open').href = m.local_url;
  $('#r-qr').src = m.qr;
  $('#r-floors').innerHTML = m.floors.map((f) =>
    `<li><b>${esc(f.label)}</b>${f.width_m}×${f.depth_m}m · 문 ${f.doors} · 창 ${f.windows} · 축척 ${SCALE[f.scale_source] || ''}</li>`).join('');
  $('#r-warn').innerHTML = (m.warnings || []).map((w) => `<li>⚠ ${esc(w)}</li>`).join('');
  $('#r-share').onclick = () => share(m.url, m.name);
  $('#r-copy').onclick = () => copy(m.url, $('#r-copy'));
  r.scrollIntoView({ behavior: 'smooth', block: 'start' });
  saveRecent({ id: m.id, name: m.name, url: m.url, created: m.created, floors: m.floors.length,
    preview: `/m/${m.id}/${m.floors[0].preview}`, token: m.delete_token });
}

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
  const list = loadRecent();
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
    a.href = `/v/${it.id}`;
    a.textContent = it.name || '이름 없는 건물';
    li.querySelector('small').textContent = `${d.toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })} · ${it.floors}개 층`;
    li.querySelector('button').onclick = async () => {
      if (!confirm(`'${it.name || '이름 없는 건물'}' 링크를 서버에서 삭제할까요? 공유한 링크도 열리지 않게 됩니다.`)) return;
      const r = await fetch(`/api/models/${it.id}`, { method: 'DELETE', headers: { 'X-Delete-Token': it.token || '' } }).catch(() => null);
      if (r && (r.ok || r.status === 404)) {
        try { localStorage.setItem(RECENT_KEY, JSON.stringify(loadRecent().filter((x) => x.id !== it.id))); } catch { /* */ }
        renderRecent();
      } else alert('삭제하지 못했습니다');
    };
    ul.appendChild(li);
  }
}
renderRecent();
