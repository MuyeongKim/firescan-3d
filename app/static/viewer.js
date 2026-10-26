const BASE = document.querySelector('meta[name="base-path"]')?.content || '';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { supported as offlineOk, saveModel, isSaved, removeModel, purgeExpired } from './offline.js';

const MID = document.body.dataset.model;
const $ = (s) => document.querySelector(s);
const V3 = THREE.Vector3;
const DEG = Math.PI / 180;
const EYE = 1.6;              // 1인칭 눈높이(m)
const RADIUS = 0.35;          // 벽과의 최소 거리(m)
const SCALE = {
  user: '축척 · 입력값', dxf: '축척 · CAD', door: '축척 · 문 폭 추정', wall: '축척 · 벽 두께 추정',
  assumed: '⚠ 축척 미확인 · 거리 참고용',
};
const HINT = {
  orbit: '한 손가락 회전 · 두 손가락 이동/확대',
  top: '드래그 이동 · 두 손가락 확대',
  walk: '드래그로 둘러보기 · 바닥을 탭하면 이동',
  measure: '두 지점을 차례로 탭하세요',
};

// ------------------------------------------------------------ 렌더러/장면
const canvas = $('#c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1014);
scene.add(new THREE.HemisphereLight(0xffffff, 0x2a3240, 2.0));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(6, 14, 9);
scene.add(sun);

const persp = new THREE.PerspectiveCamera(50, 1, 0.1, 2000);
const ortho = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 2000);
let camera = persp;

const orbit = new OrbitControls(persp, canvas);
Object.assign(orbit, {
  enableDamping: true, dampingFactor: 0.12, screenSpacePanning: false, maxPolarAngle: Math.PI * 0.495,
  minDistance: 1.5, maxDistance: 600, zoomToCursor: true,
});
orbit.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };

const planCtl = new OrbitControls(ortho, canvas);
Object.assign(planCtl, {
  enableDamping: true, dampingFactor: 0.15, enableRotate: false, screenSpacePanning: true,
  minPolarAngle: 0, maxPolarAngle: 0, zoomToCursor: true, minZoom: 0.25, maxZoom: 60,
});
planCtl.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_PAN };
planCtl.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
planCtl.enabled = false;

const edgeMat = new THREE.LineBasicMaterial({ color: 0x15191e, transparent: true, opacity: 0.55 });

const state = { mode: 'orbit', floor: 0, all: false, xray: false, plan: true, measure: false, annot: false };
let meta = null;
const floors = [];
let dirty = true;
const requestRender = () => { dirty = true; };

// ------------------------------------------------------------ 로딩
async function init() {
  let res;
  try {
    res = await fetch(`${BASE}/api/models/${MID}`);
    if (!res.ok) throw new Error(res.status === 404 ? 'gone' : 'http');
    meta = await res.json();
  } catch (e) {
    if (e.message === 'gone') removeModel(BASE, MID);
    fail(e.message === 'gone' ? '링크가 만료되었거나 삭제되었습니다.' : '네트워크에 연결할 수 없습니다.\n신호가 잡히는 곳에서 다시 열어주세요.');
    return;
  }
  $('#title').textContent = meta.name || '건물 내부구조';
  // 업로드한 사람이 자동 인식 결과를 도면과 대조했는지 알려 준다(확인 전이면 구조를 그대로 믿지 않도록)
  const badge = $('#review-badge');
  if (meta.confirmed && meta.confirmed.at) {
    badge.textContent = `✓ 인식 결과 확인됨 · ${new Date(meta.confirmed.at).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })}${meta.confirmed.note ? ' · ' + meta.confirmed.note : ''}`;
    badge.className = 'review-badge ok';
  } else {
    badge.textContent = '⚠ 자동 인식 결과 · 사람이 아직 확인하지 않았습니다. 실제 구조와 다를 수 있습니다';
    badge.className = 'review-badge warn';
  }
  if (meta.expires) badge.textContent += ` · ${new Date(meta.expires).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })} 자동 삭제`;
  badge.hidden = false;
  initAnnots();
  initSave();
  buildChips();

  const loader = new GLTFLoader();
  const loaded = new Array(meta.floors.length).fill(0);
  const total = new Array(meta.floors.length).fill(0);
  const bar = $('#loading .bar');
  const onProg = (i) => (e) => {
    loaded[i] = e.loaded; total[i] = e.total || 0;
    const T = total.reduce((a, b) => a + b, 0);
    if (T > 0 && total.every((t) => t > 0)) {
      bar.classList.remove('indet');
      bar.querySelector('i').style.width = `${Math.round((loaded.reduce((a, b) => a + b, 0) / T) * 100)}%`;
    }
  };
  const jobs = meta.floors.map((f, i) => loader.loadAsync(`${BASE}/m/${MID}/${f.glb}`, onProg(i))
    .then((g) => { setupFloor(i, f, g); updateChips(); })
    .catch(() => { updateChips(); }));

  await Promise.race([jobs[0], Promise.allSettled(jobs)]);
  const first = floors.findIndex(Boolean);
  if (first < 0) {
    await Promise.allSettled(jobs);
    if (floors.findIndex(Boolean) < 0) { fail('3D 모델을 불러오지 못했습니다.'); return; }
  }
  state.floor = Math.max(0, floors.findIndex(Boolean));
  showFloors();
  fitOrbit();
  $('#loading').classList.add('done');
  hint(HINT.orbit);
}

function fail(msg) {
  const l = $('#loading');
  l.classList.add('error');
  $('#load-msg').textContent = msg;
  $('#load-msg').style.whiteSpace = 'pre-line';
}

function setupFloor(i, info, gltf) {
  const root = gltf.scene;
  const group = new THREE.Group();
  group.add(root);
  group.visible = false;
  scene.add(group);
  const F = { info, group, wallMats: new Set(), plans: [], slabs: [], pick: [], solid: [], planImage: null };
  const aniso = renderer.capabilities.getMaxAnisotropy();
  root.traverse((o) => {
    if (!o.isMesh) return;
    const m = o.material;
    switch (m.name) {
      case 'wall':
      case 'wall_top':
        F.wallMats.add(m); F.pick.push(o); F.solid.push(o);
        o.add(Object.assign(new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 40), edgeMat), { raycast() {} }));
        break;
      case 'plan':
        if (m.map) { m.map.anisotropy = aniso; F.planImage = m.map.image; }
        F.plans.push(o); F.pick.push(o);
        break;
      case 'floor': F.slabs.push(o); F.pick.push(o); break;
      case 'door':
      case 'window': F.pick.push(o); break;
      case 'glass': m.depthWrite = false; o.renderOrder = 2; F.solid.push(o); break;
      default: break;
    }
  });
  F.entrances = (info.entrances || []).map(([x, z, dx, dz]) => ({ x, z, dx, dz }));
  F.entrances.forEach((e) => group.add(entranceArrow(e)));
  root.updateMatrixWorld(true);
  F.box = new THREE.Box3();
  F.solid.forEach((o) => F.box.expandByObject(o));
  if (F.box.isEmpty()) F.box.setFromObject(root);
  floors[i] = F;
}

// 진입구 표시: 외벽 문 바깥 바닥에 안쪽을 가리키는 주황 화살표
const arrowMat = new THREE.MeshBasicMaterial({ color: 0xff5a1f, side: THREE.DoubleSide });
const arrowGeo = (() => {
  const sh = new THREE.Shape();
  sh.moveTo(0, 0.7); sh.lineTo(0.45, 0.1); sh.lineTo(0.17, 0.1); sh.lineTo(0.17, -0.6);
  sh.lineTo(-0.17, -0.6); sh.lineTo(-0.17, 0.1); sh.lineTo(-0.45, 0.1); sh.closePath();
  return new THREE.ShapeGeometry(sh).rotateX(-Math.PI / 2);
})();
function entranceArrow(e) {
  const m = new THREE.Mesh(arrowGeo, arrowMat);
  m.position.set(e.x - e.dx * 1.2, 0.06, e.z - e.dz * 1.2);
  m.rotation.y = Math.atan2(-e.dx, -e.dz);
  m.renderOrder = 3;
  return m;
}

// ------------------------------------------------------------ 층 선택
function buildChips() {
  const nav = $('#chips');
  if (meta.floors.length < 2) return;
  document.body.classList.add('multi');
  nav.hidden = false;
  const mk = (txt, fn) => { const b = document.createElement('button'); b.textContent = txt; b.onclick = fn; nav.appendChild(b); return b; };
  meta.floors.forEach((f, i) => mk(f.label, () => selectFloor(i)).disabled = true);
  mk('전체', () => selectFloor('all'));
  updateChips();
}

function updateChips() {
  const bs = [...$('#chips').children];
  bs.forEach((b, i) => {
    const isAll = i === bs.length - 1;
    b.disabled = isAll ? floors.filter(Boolean).length < 2 : !floors[i];
    b.classList.toggle('on', isAll ? state.all : (!state.all && i === state.floor));
  });
}

function selectFloor(i) {
  if (i === 'all') {
    state.all = true;
    if (state.mode === 'walk') setMode('orbit', true);
  } else {
    state.all = false;
    state.floor = i;
  }
  showFloors();
  updateChips();
  if (state.mode === 'top') fitTop(); else if (state.mode === 'orbit') fitOrbit();
  else enterWalk();
}

function showFloors() {
  const gap = (meta.wall_height + 0.15) * 1.8;
  floors.forEach((F, i) => {
    if (!F) return;
    F.group.visible = state.all || i === state.floor;
    F.group.position.y = state.all ? i * gap : 0;
  });
  const F = floors[state.floor];
  if (F) {
    const f = F.info;
    $('#sub').textContent = `${state.all ? '전체 층' : f.label} · ${f.width_m}×${f.depth_m}m · 문 ${f.doors} · 창 ${f.windows}`;
    $('#lg-ent').hidden = !visibleFloors().some((x) => x.entrances.length);
    const sc = $('#scale');
    sc.textContent = SCALE[f.scale_source] || '';
    sc.className = f.scale_source === 'assumed' ? 'warn-txt' : '';
  }
  applyToggles();
  clearMeasure();
}

function visibleFloors() { return floors.filter((F) => F && F.group.visible); }

// ------------------------------------------------------------ 토글
function applyToggles() {
  floors.forEach((F) => {
    if (!F) return;
    F.plans.forEach((m) => {
      m.visible = state.plan;
      // 전체 층 보기에서는 위층 도면이 아래층을 가리지 않도록 반투명
      Object.assign(m.material, { transparent: state.all, opacity: state.all ? 0.45 : 1, depthWrite: !state.all });
      m.material.needsUpdate = true;
    });
    F.slabs.forEach((m) => { m.visible = !state.plan; });
    F.wallMats.forEach((m) => {
      m.transparent = state.xray;
      m.opacity = state.xray ? 0.2 : 1;
      m.depthWrite = !state.xray;
      m.needsUpdate = true;
    });
  });
  edgeMat.opacity = state.xray ? 0.9 : 0.55;
  document.querySelectorAll('[data-toggle]').forEach((b) => {
    const on = !!state[b.dataset.toggle];
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', on);
  });
  requestRender();
}

document.querySelectorAll('[data-toggle]').forEach((b) => b.addEventListener('click', () => {
  const k = b.dataset.toggle;
  state[k] = !state[k];
  if (k === 'measure') { clearMeasure(); if (state.measure) hint(HINT.measure); }
  applyToggles();
}));

// ------------------------------------------------------------ 시점
function viewBox() {
  const b = new THREE.Box3();
  visibleFloors().forEach((F) => b.union(F.box.clone().translate(F.group.position)));
  return b;
}

let tween = null;
function animateTo(cam, ctl, pos, target, ms = 450) {
  const p0 = cam.position.clone();
  const t0 = ctl.target.clone();
  const start = performance.now();
  tween = () => {
    const k = Math.min(1, (performance.now() - start) / ms);
    const e = 1 - (1 - k) ** 3;
    cam.position.lerpVectors(p0, pos, e);
    ctl.target.lerpVectors(t0, target, e);
    if (k >= 1) tween = null;
  };
}

function orbitDistance(box, c) {
  // 상자 8개 꼭짓점이 화면 안(위·아래는 UI 띠를 고려해 여백 더)에 들어오는 최소 거리
  const dir = orbitDir();
  const f = dir.clone().negate();
  const right = new V3().crossVectors(f, new V3(0, 1, 0)).normalize();
  const up = new V3().crossVectors(right, f);
  const tv = Math.tan((persp.fov * DEG) / 2) * 0.72;
  const th = Math.tan((persp.fov * DEG) / 2) * persp.aspect * 0.9;
  let d = 1;
  for (let i = 0; i < 8; i++) {
    const p = new V3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).sub(c);
    const z = p.dot(dir);
    d = Math.max(d, z + Math.abs(p.dot(right)) / th, z + Math.abs(p.dot(up)) / tv);
  }
  return d;
}

function orbitDir() { return new V3().setFromSphericalCoords(1, 42 * DEG, -22 * DEG); }

function fitOrbit(target, dist) {
  const b = viewBox();
  const c = target || b.getCenter(new V3());
  const d = dist || orbitDistance(b, c);
  persp.fov = 50;
  persp.near = Math.max(0.05, d / 200);
  persp.far = d * 30;
  persp.updateProjectionMatrix();
  const pos = c.clone().addScaledVector(orbitDir(), d);
  if (camera === persp && state.mode === 'orbit' && !target) animateTo(persp, orbit, pos, c);
  else { persp.position.copy(pos); orbit.target.copy(c); }
  orbit.update();
  requestRender();
}

function setOrthoHalf(h) {
  const a = canvas.clientWidth / Math.max(1, canvas.clientHeight);
  Object.assign(ortho, { left: -h * a, right: h * a, top: h, bottom: -h, zoom: 1 });
  ortho.updateProjectionMatrix();
}

function fitTop(target, half) {
  const b = viewBox();
  const s = b.getSize(new V3());
  const c = target || b.getCenter(new V3());
  const a = canvas.clientWidth / Math.max(1, canvas.clientHeight);
  setOrthoHalf(half || Math.max(s.z / 2, s.x / 2 / a) * 1.18);
  ortho.position.set(c.x, b.max.y + 60, c.z + 1e-4);
  ortho.near = 1; ortho.far = b.max.y + 200;
  ortho.updateProjectionMatrix();
  planCtl.target.set(c.x, b.min.y, c.z);
  planCtl.update();
  requestRender();
}

function setMode(m, silent) {
  const prev = state.mode;
  if (m === prev) { resetView(); return; }
  // 시점 전환 시 보고 있던 위치를 이어받는다
  let focus = null; let half = null; let dist = null;
  if (prev === 'orbit') {
    focus = orbit.target.clone();
    dist = persp.position.distanceTo(orbit.target);
    half = dist * Math.tan((persp.fov * DEG) / 2);
  } else if (prev === 'top') {
    focus = planCtl.target.clone();
    half = ortho.top / ortho.zoom;
    dist = half / Math.tan(25 * DEG) * 1.1;
  } else if (prev === 'walk') {
    focus = new V3(walk.pos.x, walk.base, walk.pos.z);
    dist = 12;
    half = 7;
  }
  state.mode = m;
  document.body.classList.toggle('walk', m === 'walk');
  orbit.enabled = m === 'orbit';
  planCtl.enabled = m === 'top';
  $('#minimap').hidden = m !== 'walk';
  document.querySelectorAll('[data-mode]').forEach((b) => b.classList.toggle('on', b.dataset.mode === m));
  if (m === 'top') { camera = ortho; fitTop(focus, half); }
  else if (m === 'orbit') { camera = persp; fitOrbit(focus, dist); }
  else {
    if (state.all) { state.all = false; showFloors(); updateChips(); }
    camera = persp;
    enterWalk(floors[state.floor]?.entrances.length ? null : focus);
  }
  if (!silent) hint(HINT[m]);
  requestRender();
}

function resetView() {
  if (state.mode === 'orbit') fitOrbit();
  else if (state.mode === 'top') fitTop();
  else enterWalk();
}

document.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
$('#btn-reset').addEventListener('click', resetView);

// ------------------------------------------------------------ 1인칭(내부)
const walk = { pos: new V3(), yaw: 0, pitch: -0.12, base: 0, anim: null, keys: new Set() };
const ray = new THREE.Raycaster();

function floorAt(x, z) {
  const F = floors[state.floor];
  if (!F) return false;
  ray.set(new V3(x, 50, z), new V3(0, -1, 0));
  ray.far = 100;
  return ray.intersectObjects([...F.slabs], false).length > 0;
}

function enterWalk(focus) {
  const F = floors[state.floor];
  if (!F) return;
  walk.base = F.group.position.y;
  const ent = F.entrances[0];
  if (ent && !focus) {
    // 진입구 바깥에서 건물 안쪽을 바라보며 시작
    walk.pos.set(ent.x - ent.dx * 1.6, walk.base + EYE, ent.z - ent.dz * 1.6);
    walk.yaw = Math.atan2(-ent.dx, -ent.dz);
  } else {
    let p = focus ? focus.clone() : F.box.getCenter(new V3());
    if (!floorAt(p.x, p.z)) p = F.box.getCenter(new V3());
    walk.pos.set(p.x, walk.base + EYE, p.z);
    if (focus && camera === persp) {
      const d = new V3();
      persp.getWorldDirection(d);
      walk.yaw = Math.atan2(-d.x, -d.z);
    } else walk.yaw = 0;
  }
  walk.pitch = -0.12;
  walk.anim = null;
  persp.fov = 72;
  persp.near = 0.05;
  persp.far = 400;
  persp.updateProjectionMatrix();
  applyWalk();
  prepMinimap();
}

function applyWalk() {
  persp.position.copy(walk.pos);
  persp.rotation.set(walk.pitch, walk.yaw, 0, 'YXZ');
  requestRender();
}

function blockedDistance(from, dir, maxD) {
  const F = floors[state.floor];
  ray.set(new V3(from.x, walk.base + 1.0, from.z), dir);
  ray.far = maxD + RADIUS;
  const h = ray.intersectObjects(F.solid, false)[0];
  return h ? Math.max(0, h.distance - RADIUS) : maxD;
}

function walkTo(point) {
  const from = walk.pos.clone();
  const dir = new V3(point.x - from.x, 0, point.z - from.z);
  let len = dir.length();
  if (len < 0.05) return;
  dir.normalize();
  len = blockedDistance(from, dir, len);
  const to = from.clone().addScaledVector(dir, len);
  const ms = 250 + len * 110;
  const t0 = performance.now();
  walk.anim = () => {
    const k = Math.min(1, (performance.now() - t0) / ms);
    walk.pos.lerpVectors(from, to, 1 - (1 - k) ** 2);
    if (k >= 1) walk.anim = null;
  };
}

function step(dist) {
  const dir = new V3(-Math.sin(walk.yaw), 0, -Math.cos(walk.yaw)).multiplyScalar(Math.sign(dist));
  const d = blockedDistance(walk.pos, dir, Math.abs(dist));
  walk.pos.addScaledVector(dir, d);
}

let lastT = performance.now();
function walkTick() {
  const now = performance.now();
  const dt = Math.min(0.05, (now - lastT) / 1000);
  lastT = now;
  if (state.mode !== 'walk') return false;
  let moved = false;
  if (walk.anim) { walk.anim(); moved = true; }
  const k = walk.keys;
  if (k.size) {
    const turn = (k.has('ArrowLeft') || k.has('KeyQ') ? 1 : 0) - (k.has('ArrowRight') || k.has('KeyE') ? 1 : 0);
    const fwd = (k.has('ArrowUp') || k.has('KeyW') ? 1 : 0) - (k.has('ArrowDown') || k.has('KeyS') ? 1 : 0);
    const side = (k.has('KeyD') ? 1 : 0) - (k.has('KeyA') ? 1 : 0);
    walk.yaw += turn * 1.8 * dt;
    if (fwd) step(fwd * 2.6 * dt);
    if (side) {
      const yaw = walk.yaw;
      walk.yaw -= Math.PI / 2; step(side * 2.0 * dt); walk.yaw = yaw;
    }
    moved = true;
  }
  if (moved) applyWalk();
  return moved;
}

window.addEventListener('keydown', (e) => { if (state.mode === 'walk') walk.keys.add(e.code); });
window.addEventListener('keyup', (e) => walk.keys.delete(e.code));
window.addEventListener('blur', () => walk.keys.clear());

// ------------------------------------------------------------ 포인터(탭/1인칭 드래그)
const ptrs = new Map();
let tap = null;
let pinch = 0;
const ptrDist = () => { const [a, b] = [...ptrs.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };

canvas.addEventListener('pointerdown', (e) => {
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  tap = ptrs.size === 1 ? { x: e.clientX, y: e.clientY, t: performance.now() } : null;
  if (ptrs.size === 2) pinch = ptrDist();
  if (state.mode === 'walk') { canvas.setPointerCapture(e.pointerId); walk.anim = null; }
});
canvas.addEventListener('pointermove', (e) => {
  const p = ptrs.get(e.pointerId);
  if (!p) return;
  const dx = e.clientX - p.x;
  const dy = e.clientY - p.y;
  p.x = e.clientX; p.y = e.clientY;
  if (state.mode !== 'walk') return;
  if (ptrs.size === 1) {
    const k = 2.4 / Math.max(canvas.clientWidth, canvas.clientHeight);
    walk.yaw += dx * k * 1.6;
    walk.pitch = THREE.MathUtils.clamp(walk.pitch + dy * k * 1.6, -1.35, 1.35);
    applyWalk();
  } else if (ptrs.size === 2) {
    const d = ptrDist();
    step((d - pinch) * 0.025);
    pinch = d;
    applyWalk();
  }
});
const endPtr = (e) => {
  if (tap && ptrs.size === 1 && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) < 9 && performance.now() - tap.t < 400) onTap(e);
  ptrs.delete(e.pointerId);
  tap = null;
};
canvas.addEventListener('pointerup', endPtr);
canvas.addEventListener('pointercancel', (e) => { ptrs.delete(e.pointerId); tap = null; });
canvas.addEventListener('wheel', (e) => {
  if (state.mode !== 'walk') return;
  e.preventDefault();
  step(-e.deltaY * 0.01);
  applyWalk();
}, { passive: false });

function pick(e) {
  const r = canvas.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  ray.far = Infinity;
  const objs = visibleFloors().flatMap((F) => F.pick.filter((o) => o.visible));
  return ray.intersectObjects(objs, false)[0] || null;
}

function onTap(e) {
  const hit = pick(e);
  if (!hit) return;
  if (state.annot) { placeAnnot(hit); return; }
  if (state.measure) { addMeasure(hit.point); return; }
  if (state.mode === 'walk') walkTo(hit.point);
}

// ------------------------------------------------------------ 거리 재기
const measure = { pts: [], group: new THREE.Group(), label: null };
scene.add(measure.group);
const accent = new THREE.Color(0xff5a1f);
const markGeo = new THREE.SphereGeometry(1, 16, 12);
const markMat = new THREE.MeshBasicMaterial({ color: accent, depthTest: false });
const lineMat = new THREE.LineBasicMaterial({ color: accent, depthTest: false });

function clearMeasure() {
  measure.pts = [];
  measure.group.clear();
  measure.label?.remove();
  measure.label = null;
  requestRender();
}

function addMeasure(p) {
  if (measure.pts.length >= 2) clearMeasure();
  measure.pts.push(p.clone());
  const s = new THREE.Mesh(markGeo, markMat);
  s.renderOrder = 10;
  s.position.copy(p);
  s.userData.marker = true;
  measure.group.add(s);
  if (measure.pts.length === 2) {
    const [a, b] = measure.pts;
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), lineMat);
    l.renderOrder = 10;
    measure.group.add(l);
    const lab = document.createElement('div');
    lab.className = 'mlabel';
    const est = floors[state.floor]?.info.scale_source === 'assumed' ? ' (추정)' : '';
    lab.textContent = `${a.distanceTo(b).toFixed(1)} m${est}`;
    $('#labels').appendChild(lab);
    measure.label = lab;
  }
  requestRender();
}

function updateOverlay() {
  positionAnnots();
  // 마커 크기를 화면 기준으로 일정하게
  const scale = (p) => (camera.isOrthographicCamera ? (ortho.top / ortho.zoom) * 0.012 : camera.position.distanceTo(p) * 0.012);
  measure.group.children.forEach((o) => { if (o.userData.marker) o.scale.setScalar(Math.max(0.03, scale(o.position))); });
  if (measure.label && measure.pts.length === 2) {
    const m = measure.pts[0].clone().add(measure.pts[1]).multiplyScalar(0.5).project(camera);
    const r = canvas.getBoundingClientRect();
    measure.label.style.left = `${r.left + ((m.x + 1) / 2) * r.width}px`;
    measure.label.style.top = `${r.top + ((1 - m.y) / 2) * r.height}px`;
    measure.label.hidden = m.z > 1;
  }
}

// ------------------------------------------------------------ 방 이름·소방시설 표시
// 업로드한 기기(삭제 토큰이 저장된 곳)에서만 편집하고, 링크로 여는 대원은 읽기만 한다.
const KINDS = {
  room: ['방 이름', '#e8edf2'], stairs: ['계단', '#b08cff'], exit: ['비상구', '#22c55e'], hydrant: ['소화전', '#ff5c5c'],
  extinguisher: ['소화기', '#ff8a4c'], firedoor: ['방화문', '#ffb020'], hazard: ['위험', '#ff3b3b'],
};
const annot = { items: [], els: new Map(), kind: 'room', token: '', timer: null };

function ownerToken() {
  try {
    const list = JSON.parse(localStorage.getItem('fs3d.recent') || '[]');
    return list.find((x) => x.id === MID)?.token || '';
  } catch { return ''; }
}

function initAnnots() {
  annot.items = (meta.annotations || []).map((a) => ({ ...a }));
  annot.token = ownerToken();
  $('#btn-annot').hidden = !annot.token;
  const kinds = $('#ab-kinds');
  for (const [k, [label, color]] of Object.entries(KINDS)) {
    const b = document.createElement('button');
    b.dataset.kind = k; b.textContent = label; b.style.setProperty('--kc', color);
    b.setAttribute('role', 'radio');
    b.onclick = () => { annot.kind = k; syncKindButtons(); $('#annot-text').focus(); };
    kinds.appendChild(b);
  }
  syncKindButtons();
  renderAnnots();
}
function syncKindButtons() {
  document.querySelectorAll('#ab-kinds button').forEach((b) => {
    const on = b.dataset.kind === annot.kind;
    b.classList.toggle('on', on); b.setAttribute('aria-checked', on);
  });
  $('#annot-text').placeholder = annot.kind === 'room' ? '방 이름 (예: 침실1, 기계실)' : '메모(선택) 예) 동쪽 계단';
}

function renderAnnots() {
  const box = $('#labels');
  for (const el of annot.els.values()) el.remove();
  annot.els.clear();
  for (const a of annot.items) {
    const [label, color] = KINDS[a.kind] || KINDS.room;
    const el = document.createElement('div');
    el.className = 'alab';
    el.style.setProperty('--kc', color);
    const dot = document.createElement('i');
    const txt = document.createElement('span');
    txt.textContent = a.kind === 'room' ? a.text : (a.text ? `${label} · ${a.text}` : label);
    el.append(dot, txt);
    el.onclick = (e) => {
      if (!state.annot) return;
      e.stopPropagation();
      if (!confirm(`'${txt.textContent}' 표시를 삭제할까요?`)) return;
      annot.items = annot.items.filter((x) => x.id !== a.id);
      renderAnnots(); saveAnnots();
    };
    box.appendChild(el);
    annot.els.set(a.id, el);
  }
  requestRender();
}

const _av = new V3();
function positionAnnots() {
  if (!annot.els.size) return;
  const r = canvas.getBoundingClientRect();
  for (const a of annot.items) {
    const el = annot.els.get(a.id);
    const F = floors[a.floor];
    if (!el) continue;
    if (!F || !F.group.visible) { el.hidden = true; continue; }
    _av.set(a.x, F.group.position.y + (state.mode === 'walk' ? 1.3 : 0.25), a.z);
    // 1인칭에서는 가까운 것만 보여 화면이 어지럽지 않게 한다
    const near = state.mode !== 'walk' || camera.position.distanceTo(_av) < 14;
    _av.project(camera);
    if (_av.z > 1 || !near) { el.hidden = true; continue; }
    el.hidden = false;
    el.style.left = `${r.left + ((_av.x + 1) / 2) * r.width}px`;
    el.style.top = `${r.top + ((1 - _av.y) / 2) * r.height}px`;
  }
}

function annotMsg(text, bad) {
  const m = $('#annot-msg');
  m.textContent = text;
  m.className = bad ? 'ab-msg bad' : 'ab-msg';
}

function placeAnnot(hit) {
  const text = $('#annot-text').value.trim().slice(0, 30);
  if (annot.kind === 'room' && !text) { annotMsg('방 이름을 먼저 입력하세요', true); $('#annot-text').focus(); return; }
  if (annot.items.length >= 60) { annotMsg('표시는 최대 60개입니다. 하나를 삭제하세요', true); return; }
  const fi = floors.findIndex((F) => F && F.pick.includes(hit.object));
  if (fi < 0) return;
  annot.items.push({ id: Math.random().toString(36).slice(2, 10), floor: fi, kind: annot.kind,
    x: +hit.point.x.toFixed(2), z: +hit.point.z.toFixed(2), text });
  if (annot.kind === 'room') $('#annot-text').value = '';
  renderAnnots(); saveAnnots();
}

function saveAnnots() {
  annotMsg('저장 중…');
  clearTimeout(annot.timer);
  annot.timer = setTimeout(async () => {
    let res = null;
    try {
      res = await fetch(`${BASE}/api/models/${MID}/annotations`, { method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-Delete-Token': annot.token },
        body: JSON.stringify({ items: annot.items }) });
    } catch { /* 네트워크 오류 */ }
    if (res && res.ok) annotMsg(`저장됨 ✓ (${annot.items.length}개)`);
    else annotMsg(`저장하지 못했습니다${res ? ' (' + (await res.json().catch(() => ({}))).detail + ')' : ' · 연결 확인'}. 다음 표시 때 다시 저장합니다`, true);
  }, 350);
}

function setAnnotMode(on) {
  state.annot = on;
  document.body.classList.toggle('annot-on', on);
  $('#annot-bar').hidden = !on;
  $('#btn-annot').classList.toggle('on', on);
  $('#btn-annot').setAttribute('aria-pressed', on);
  if (on) {
    state.measure = false; clearMeasure(); applyToggles();
    if (state.mode !== 'top') setMode('top');
    if (!state.plan) { state.plan = true; applyToggles(); }
    annotMsg('평면 도면에서 위치를 탭하세요 · 표시를 탭하면 삭제');
  }
  requestRender();
}
$('#btn-annot').addEventListener('click', () => setAnnotMode(!state.annot));
$('#annot-done').addEventListener('click', () => setAnnotMode(false));

// ------------------------------------------------------------ 오프라인 저장(미리 저장)
// 출동 전에 신호가 좋은 곳에서 눌러 두면, 지하·철골 구조물처럼 신호가 약한 곳에서도 열린다(보관기간까지).
async function initSave() {
  const btn = $('#btn-save');
  purgeExpired(BASE).catch(() => {});
  if (!offlineOk()) return;       // HTTPS 가 아니거나 지원하지 않는 브라우저
  btn.hidden = false;
  const label = btn.querySelector('span');
  const refresh = async () => {
    const saved = await isSaved(BASE, meta).catch(() => false);
    btn.classList.toggle('on', saved);
    btn.setAttribute('aria-pressed', saved);
    label.textContent = saved ? '저장됨' : '저장';
    return saved;
  };
  await refresh();
  btn.addEventListener('click', async () => {
    if (btn.dataset.busy) return;
    if (await refresh()) {
      if (!confirm('이 기기에 저장한 모델을 지울까요? (서버의 링크는 그대로입니다)')) return;
      await removeModel(BASE, MID);
      await refresh();
      hint('이 기기의 저장본을 지웠습니다');
      return;
    }
    btn.dataset.busy = '1';
    try {
      await saveModel(BASE, meta, (i, n) => { label.textContent = `${i}/${n}`; });
      const kb = meta.floors.reduce((a, f) => a + (f.glb_kb || 0), 0);
      const until = meta.expires ? ` · ${new Date(meta.expires).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })}까지` : '';
      hint(`저장됨 · 약 ${kb}KB · 신호가 없어도 열립니다${until}`);
    } catch {
      hint('저장하지 못했습니다. 신호가 좋은 곳에서 다시 눌러 주세요');
    } finally {
      delete btn.dataset.busy;
      await refresh();
    }
  });
}

// ------------------------------------------------------------ 미니맵
const mm = $('#minimap');
const mctx = mm.getContext('2d');
let mmState = null;

function prepMinimap() {
  const F = floors[state.floor];
  if (!F?.planImage) { mm.hidden = true; return; }
  const [x0, z0, x1, z1] = F.info.plan_rect;
  const pad = 1.0;
  const bx0 = Math.max(x0, F.box.min.x - pad); const bx1 = Math.min(x1, F.box.max.x + pad);
  const bz0 = Math.max(z0, F.box.min.z - pad); const bz1 = Math.min(z1, F.box.max.z + pad);
  const short = Math.min(window.innerWidth, window.innerHeight);
  const W = Math.round(Math.min(220, Math.max(130, short * 0.36)));
  const H = Math.round((W * (bz1 - bz0)) / (bx1 - bx0));
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  mm.width = W * dpr; mm.height = H * dpr;
  mm.style.width = `${W}px`; mm.style.height = `${H}px`;
  const img = F.planImage;
  const iw = img.width; const ih = img.height;
  const bg = document.createElement('canvas');
  bg.width = mm.width; bg.height = mm.height;
  bg.getContext('2d').drawImage(img, ((bx0 - x0) / (x1 - x0)) * iw, ((bz0 - z0) / (z1 - z0)) * ih,
    ((bx1 - bx0) / (x1 - x0)) * iw, ((bz1 - bz0) / (z1 - z0)) * ih, 0, 0, bg.width, bg.height);
  mmState = { bx0, bz0, bx1, bz1, bg };
  mm.hidden = state.mode !== 'walk';
  drawMinimap();
}

function drawMinimap() {
  if (!mmState || mm.hidden) return;
  const { bx0, bz0, bx1, bz1, bg } = mmState;
  const w = mm.width; const h = mm.height;
  mctx.drawImage(bg, 0, 0);
  const u = ((walk.pos.x - bx0) / (bx1 - bx0)) * w;
  const v = ((walk.pos.z - bz0) / (bz1 - bz0)) * h;
  const a = Math.atan2(-Math.cos(walk.yaw), -Math.sin(walk.yaw));
  const r = w * 0.16;
  mctx.fillStyle = 'rgba(255,90,31,0.28)';
  mctx.beginPath();
  mctx.moveTo(u, v);
  mctx.arc(u, v, r, a - 0.6, a + 0.6);
  mctx.closePath();
  mctx.fill();
  mctx.fillStyle = '#ff5a1f';
  mctx.strokeStyle = '#fff';
  mctx.lineWidth = w * 0.012;
  mctx.beginPath();
  mctx.arc(u, v, w * 0.035, 0, Math.PI * 2);
  mctx.fill();
  mctx.stroke();
}

mm.addEventListener('pointerdown', (e) => {
  e.stopPropagation();
  if (!mmState) return;
  const r = mm.getBoundingClientRect();
  const { bx0, bz0, bx1, bz1 } = mmState;
  const x = bx0 + ((e.clientX - r.left) / r.width) * (bx1 - bx0);
  const z = bz0 + ((e.clientY - r.top) / r.height) * (bz1 - bz0);
  walk.anim = null;
  walk.pos.set(x, walk.base + EYE, z);
  applyWalk();
});

// ------------------------------------------------------------ 공유
$('#btn-share').addEventListener('click', async () => {
  const url = meta?.url || location.href;
  const title = `${meta?.name || '건물'} 내부구조 3D`;
  if (navigator.share) {
    try { await navigator.share({ title, text: title, url }); return; } catch (e) { if (e.name === 'AbortError') return; }
  }
  $('#qr-img').src = `/qr/${MID}.svg`;
  $('#share-url').value = url;
  $('#share-modal').hidden = false;
});
$('#share-close').addEventListener('click', () => { $('#share-modal').hidden = true; });
$('#share-modal').addEventListener('click', (e) => { if (e.target.id === 'share-modal') e.target.hidden = true; });
$('#share-copy').addEventListener('click', async () => {
  const v = $('#share-url').value;
  try { await navigator.clipboard.writeText(v); } catch { $('#share-url').select(); document.execCommand('copy'); }
  $('#share-copy').textContent = '복사됨 ✓';
  setTimeout(() => { $('#share-copy').textContent = '복사'; }, 1400);
});

// ------------------------------------------------------------ 안내 문구
let hintTimer = 0;
function hint(text) {
  const h = $('#hint');
  h.textContent = text;
  h.classList.add('show');
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => h.classList.remove('show'), 3200);
}

// ------------------------------------------------------------ 크기/루프
function resize() {
  const w = canvas.clientWidth; const h = canvas.clientHeight;
  renderer.setSize(w, h, false);
  persp.aspect = w / Math.max(1, h);
  persp.updateProjectionMatrix();
  setOrthoHalf(ortho.top || 10);
  if (state.mode === 'walk') prepMinimap();
  requestRender();
}
window.addEventListener('resize', resize);
new ResizeObserver(resize).observe(canvas);
resize();

function loop() {
  requestAnimationFrame(loop);
  let changed = false;
  if (tween) { tween(); changed = true; }
  if (state.mode === 'orbit') changed = orbit.update() || changed;
  else if (state.mode === 'top') changed = planCtl.update() || changed;
  else changed = walkTick() || changed;
  if (state.mode !== 'walk') lastT = performance.now();
  if (changed || dirty) {
    updateOverlay();
    renderer.render(scene, camera);
    if (state.mode === 'walk') drawMinimap();
    dirty = false;
  }
}
loop();

// 오프라인에서도 다시 열 수 있도록 캐시(HTTPS/localhost 에서만 동작)
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register(BASE + '/sw.js', { scope: BASE + '/' }).catch(() => {});
}

init();
