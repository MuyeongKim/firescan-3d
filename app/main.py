"""현장 진입 전 내부구조 3D 확인 서버.

  POST /api/models   도면 업로드 → 즉시 3D 생성 → 공유 링크 반환
  GET  /v/{id}       모바일/태블릿 3D 뷰어 (링크로 전달)
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import segno
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .pipeline import UnsupportedFile, build_floor, load_pages

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("DATA_DIR", ROOT.parent / "data")).resolve()
# nginx 가 하위 경로(예: /kmy/firescan)로 내보낼 때 화면이 만드는 링크의 앞부분. 비우면 루트(/)에서 서비스
BASE = os.environ.get("BASE_PATH", "").strip().rstrip("/")
if BASE and not re.fullmatch(r"/[A-Za-z0-9._~/-]+", BASE):
    raise SystemExit("BASE_PATH 는 /로 시작하는 영문·숫자·-_./ 경로여야 합니다")
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", 40))
MAX_FILES = 20
MAX_PAGES = 30                                              # PDF 페이지 포함 한 번에 만들 층 수 상한
TTL_DAYS = float(os.environ.get("MODEL_TTL_DAYS", 30))   # 0 이면 보관 기한 없음
ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")

POOL = ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) // 2))


class RateLimit:
    """IP별 슬라이딩 윈도 횟수 제한(단일 프로세스 메모리)."""

    def __init__(self, n: int, per_s: float):
        self.n, self.per = n, per_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str) -> deque:
        q, now = self.hits[key], time.monotonic()
        while q and now - q[0] > self.per:
            q.popleft()
        return q

    def full(self, key: str) -> bool:
        return len(self._trim(key)) >= self.n

    def hit(self, key: str) -> bool:
        q = self._trim(key)
        if len(q) >= self.n:
            return False
        q.append(time.monotonic())
        return True


UPLOADS = RateLimit(30, 600)        # 10분에 30회
# ------------------------------------------------------------------ 감사 기록
# 누가(접속 IP) 언제 무엇을 했는지만 남긴다. 접근 코드 값·건물명·메모·표시 글자처럼 민감한 내용은 기록하지 않는다.
AUDIT_DAYS = int(os.environ.get("AUDIT_DAYS", 90))          # 이 기간이 지난 기록은 정리 작업에서 지운다(0 이하면 지우지 않음)
AUDIT = Path(os.environ.get("AUDIT_LOG") or (DATA / "audit.jsonl"))
_audit_lock = threading.Lock()


def audit(event: str, request: Request | None = None, **fields) -> None:
    """감사 기록 한 줄(JSON)을 덧붙인다. 기록 실패가 서비스를 막아서는 안 되므로 오류는 삼킨다."""
    rec = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ev": event}
    if request is not None:
        rec["ip"] = (request.client.host if request.client else "?")[:64]
    for k, v in fields.items():
        rec[k] = v[:80] if isinstance(v, str) else v
    try:
        with _audit_lock:
            AUDIT.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(AUDIT, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_audit(limit: int = 200) -> list[dict]:
    try:
        lines = AUDIT.read_text("utf-8").splitlines()[-max(1, min(limit, 1000)):]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def trim_audit() -> None:
    """보관 기간(AUDIT_DAYS)이 지난 기록을 지운다."""
    if AUDIT_DAYS <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=AUDIT_DAYS)
    try:
        with _audit_lock:
            lines = AUDIT.read_text("utf-8").splitlines()
            keep = []
            for ln in lines:
                try:
                    if datetime.fromisoformat(json.loads(ln)["t"]) >= cutoff:
                        keep.append(ln)
                except (ValueError, KeyError):
                    keep.append(ln)
            if len(keep) != len(lines):
                tmp = AUDIT.with_suffix(".tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write("\n".join(keep) + ("\n" if keep else ""))
                tmp.replace(AUDIT)
    except OSError:
        pass


BAD_CODE = RateLimit(8, 600)        # 접근 코드 오입력 10분에 8회


TTL_CHOICES = (1, 7, 30)   # 업로드할 때 고를 수 있는 보관 일수(서버 MODEL_TTL_DAYS 가 더 짧으면 그 값이 상한)


def pick_ttl(requested) -> int | None:
    """보관 일수를 정한다. 값이 없으면 서버 기본값(MODEL_TTL_DAYS, 0 이면 무기한=None)."""
    if requested in (None, ""):
        return None if TTL_DAYS <= 0 else int(TTL_DAYS) if TTL_DAYS >= 1 else 1
    try:
        days = int(requested)
    except (TypeError, ValueError):
        raise HTTPException(422, "보관 기간은 1, 7, 30일 중에서 고르세요")
    if days not in TTL_CHOICES:
        raise HTTPException(422, "보관 기간은 1, 7, 30일 중에서 고르세요")
    return min(days, int(TTL_DAYS)) if TTL_DAYS >= 1 else days


def expiry_of(meta: dict, fallback_ts: float | None = None) -> float | None:
    """모델의 만료 시각(epoch). 생성 시각 기준이며, 확인·표시 저장으로 파일이 다시 써져도 늘어나지 않는다."""
    exp = meta.get("expires")
    if exp:
        try:
            return datetime.fromisoformat(exp).timestamp()
        except ValueError:
            pass
    try:   # 예전 모델: 생성 시각 + 서버 기본 기간
        created = datetime.fromisoformat(meta["created"]).timestamp()
    except (KeyError, ValueError):
        created = fallback_ts
    if created is None or TTL_DAYS <= 0:
        return None
    return created + TTL_DAYS * 86400


def cleanup_expired():
    if not DATA.exists():
        return
    now = time.time()
    for d in DATA.iterdir():
        if not d.is_dir():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text("utf-8"))
            exp = expiry_of(meta, d.stat().st_mtime)
        except (OSError, ValueError):
            exp = d.stat().st_mtime + TTL_DAYS * 86400 if TTL_DAYS > 0 else None   # 깨진 모델은 기본 기간 뒤 정리
        if exp is not None and exp < now:
            shutil.rmtree(d, ignore_errors=True)
            audit("expire", id=d.name)


@asynccontextmanager
async def lifespan(_: FastAPI):
    DATA.mkdir(parents=True, exist_ok=True)

    async def janitor():
        while True:
            await asyncio.to_thread(cleanup_expired)
            await asyncio.to_thread(trim_audit)
            await asyncio.sleep(3600)

    task = asyncio.create_task(janitor())
    yield
    task.cancel()


app = FastAPI(title="FireScan 3D", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def privacy_headers(request: Request, call_next):
    resp = await call_next(request)
    # 링크 자체가 열람 권한이므로 검색 노출·리퍼러 유출 차단
    resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


# ------------------------------------------------------------------ helpers
def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def setting(env: str, fname: str) -> str:
    """환경변수 > DATA_DIR/<fname>(deploy/public.sh 가 실행 중에 기록) 순으로 읽는다."""
    v = os.environ.get(env, "").strip()
    if v:
        return v
    try:
        return (DATA / fname).read_text("utf-8").strip()
    except OSError:
        return ""


def share_base(request: Request) -> str:
    """휴대폰에서 열 수 있는 기준 주소. 공개 주소가 있으면 그것, localhost 접속이면 LAN IP."""
    if public := setting("PUBLIC_BASE_URL", "public_url"):
        return public.rstrip("/")
    base = str(request.base_url).rstrip("/")
    host = request.url.hostname or ""
    if host in ("localhost", "127.0.0.1", "::1") and (ip := lan_ip()):
        return f"{request.url.scheme}://{ip}:{request.url.port or 80}"
    return base


def model_dir(mid: str) -> Path:
    if not mid or len(mid) > 32 or not set(mid) <= ID_CHARS:
        raise HTTPException(404, "모델을 찾을 수 없습니다")
    d = DATA / mid
    try:
        exp = expiry_of(json.loads((d / "meta.json").read_text("utf-8")))
    except (OSError, ValueError):
        raise HTTPException(404, "모델을 찾을 수 없거나 보관 기한이 지났습니다")
    if exp is not None and exp < time.time():   # 정리 작업 전이라도 만료된 링크는 열리지 않는다
        raise HTTPException(404, "모델을 찾을 수 없거나 보관 기한이 지났습니다")
    return d


def read_meta(mid: str) -> dict:
    return json.loads((model_dir(mid) / "meta.json").read_text("utf-8"))


def public_meta(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k != "delete_token"}


# ------------------------------------------------------------------ pages
def render(name: str) -> str:
    return (STATIC / name).read_text("utf-8").replace("{{BASE}}", html.escape(BASE))


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(render("index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/v/{mid}", response_class=HTMLResponse)
def viewer(mid: str, request: Request):
    try:
        meta = read_meta(mid)
    except HTTPException:
        return HTMLResponse(render("missing.html"), status_code=404)
    base = share_base(request)
    floors = meta["floors"]
    title = meta.get("name") or "건물 내부구조"
    desc = f"{len(floors)}개 층 · " + " / ".join(f["label"] for f in floors[:6]) + " · 탭하여 3D로 확인"
    page = render("viewer.html")
    for k, v in {
        "{{TITLE}}": html.escape(title), "{{DESC}}": html.escape(desc),
        "{{OG_IMAGE}}": html.escape(f"{base}/m/{mid}/{floors[0]['preview']}"),
        "{{OG_URL}}": html.escape(f"{base}/v/{mid}"), "{{MODEL_ID}}": html.escape(mid),
        "{{FIRST_GLB}}": html.escape(f"{BASE}/m/{mid}/{floors[0]['glb']}"),
    }.items():
        page = page.replace(k, v)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker():
    return Response(render("sw.js"), media_type="text/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": BASE + "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return Response(render("manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/config")
def config():
    return {"upload_code_required": bool(setting("UPLOAD_CODE", "upload_code"))}


# ------------------------------------------------------------------ api
@app.post("/api/models")
async def create_model(
    request: Request,
    files: list[UploadFile] = File(...),
    labels: list[str] = Form(default=[]),
    name: str = Form(default=""),
    width_m: float | None = Form(default=None),
    wall_height: float = Form(default=2.8),
    ttl_days: str = Form(default=""),
    x_upload_code: str = Header(default=""),
):
    t0 = time.perf_counter()
    ip = request.client.host if request.client else "?"
    if setting("UPLOAD_CODE", "upload_code"):
        check_upload_code(request, x_upload_code)
    ttl = pick_ttl(ttl_days)   # 잘못된 값은 무거운 처리 전에 거절
    if not UPLOADS.hit(ip):
        raise HTTPException(429, "업로드가 너무 잦습니다. 잠시 후 다시 시도하세요")
    if not files:
        raise HTTPException(400, "도면 파일을 선택하세요")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"한 번에 최대 {MAX_FILES}개 파일까지 올릴 수 있습니다")
    wall_height = min(max(wall_height or 2.8, 2.0), 6.0)
    if width_m is not None and not (1 <= width_m <= 1000):
        width_m = None

    # 읽기 + 페이지 분해(PDF 다중 페이지 → 여러 층)
    raw = []
    for i, f in enumerate(files):
        data = await f.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"{f.filename}: 파일이 너무 큽니다(최대 {MAX_UPLOAD_MB:.0f}MB)")
        raw.append((f.filename or f"file{i}", data, (labels[i] if i < len(labels) else "").strip()))

    def split(item):
        fn, data, lab = item
        return fn, load_pages(data, fn, lab)

    warnings: list[str] = []
    pages = []
    for fn, res in zip([r[0] for r in raw], await asyncio.gather(
            *[asyncio.wrap_future(POOL.submit(split, r)) for r in raw], return_exceptions=True)):
        if isinstance(res, Exception):
            warnings.append(f"{fn}: {res if isinstance(res, UnsupportedFile) else '읽기 실패'}")
            continue
        pages.extend(res[1])
    if len(pages) > MAX_PAGES:
        warnings.append(f"층(페이지)이 많아 앞의 {MAX_PAGES}개만 생성했습니다")
        pages = pages[:MAX_PAGES]
    n = 0
    for p in pages:                      # 이름 없는 층은 순서대로 1F, 2F …
        n += 1
        if not p.label:
            p.label = f"{n}F"

    results = await asyncio.gather(
        *[asyncio.wrap_future(POOL.submit(build_floor, p, wall_height, width_m)) for p in pages],
        return_exceptions=True)

    mid = secrets.token_urlsafe(8)
    d = DATA / mid
    d.mkdir(parents=True)
    floors = []
    for i, (p, r) in enumerate(zip(pages, results)):
        if isinstance(r, Exception):
            warnings.append(f"{p.label}: 벽을 인식하지 못했습니다" if isinstance(r, ValueError) else
                            f"{p.label}: 처리 오류")
            continue
        (d / f"f{i}.glb").write_bytes(r.glb)
        (d / f"f{i}.jpg").write_bytes(r.preview)
        floors.append({**r.info, "glb": f"f{i}.glb", "preview": f"f{i}.jpg",
                       "glb_kb": round(len(r.glb) / 1024)})
    if not floors:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(422, "도면에서 벽을 찾지 못했습니다. " + " / ".join(warnings))

    created = datetime.now(timezone.utc)
    meta = {
        "id": mid, "name": name.strip()[:80], "created": created.isoformat(),
        "ttl_days": ttl, "expires": (created + timedelta(days=ttl)).isoformat() if ttl else None,
        "wall_height": wall_height, "floors": floors, "warnings": warnings,
        "total_ms": round((time.perf_counter() - t0) * 1000), "delete_token": secrets.token_urlsafe(16),
        "confirmed": None,  # 업로드한 사람이 인식 결과를 확인하면 {at, checks, note} 로 채워진다
        "annotations": [],  # 방 이름·소방시설 표시(업로드한 사람이 편집, 열람자는 읽기만)
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    audit("upload", request, id=mid, floors=len(floors), kb=sum(f.get("glb_kb", 0) for f in floors),
          ttl=ttl, ms=meta["total_ms"])
    base = share_base(request)
    return JSONResponse({**meta, "url": f"{base}/v/{mid}", "qr": f"{BASE}/qr/{mid}.svg",
                         "local_url": f"{BASE}/v/{mid}"}, status_code=201)


@app.get("/api/models/{mid}")
def get_model(mid: str, request: Request):
    meta = public_meta(read_meta(mid))
    return JSONResponse({**meta, "url": f"{share_base(request)}/v/{mid}"},
                        headers={"Cache-Control": "no-cache"})


CONFIRM_CHECKS = ("walls", "entrance", "doors")


@app.post("/api/models/{mid}/confirm")
async def confirm_model(mid: str, request: Request, x_delete_token: str = Header(default="")):
    """업로드한 사람이 자동 인식 결과(벽·진입구·문)를 도면과 대조해 확인했음을 기록한다. 삭제와 같은 토큰으로만 가능."""
    meta = read_meta(mid)
    if not secrets.compare_digest(x_delete_token.encode(), meta.get("delete_token", "").encode()):
        raise HTTPException(403, "확인 권한이 없습니다")
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "요청이 너무 큽니다")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(422, "요청 형식이 올바르지 않습니다")
    checks = body.get("checks") if isinstance(body, dict) else None
    if not isinstance(checks, dict) or not all(checks.get(k) is True for k in CONFIRM_CHECKS):
        raise HTTPException(422, "벽·진입구·문 확인 항목을 모두 확인해야 합니다")
    note = body.get("note", "")
    note = note.strip()[:200] if isinstance(note, str) else ""
    meta["confirmed"] = {"at": datetime.now(timezone.utc).isoformat(), "checks": list(CONFIRM_CHECKS), "note": note}
    path = model_dir(mid) / "meta.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    tmp.replace(path)
    audit("confirm", request, id=mid)
    return {"confirmed": meta["confirmed"]}


def check_upload_code(request: Request, given: str) -> None:
    """업로드 접근 코드를 검사한다(틀리면 401, 오입력이 많으면 IP 별 10분 429). 코드가 설정돼 있어야 한다."""
    code = setting("UPLOAD_CODE", "upload_code")
    if not code:
        raise HTTPException(403, "업로드 접근 코드가 설정되어 있지 않아 이 작업은 사용할 수 없습니다")
    ip = request.client.host if request.client else "?"
    if BAD_CODE.full(ip):
        audit("code_locked", request, path=request.url.path)
        raise HTTPException(429, "접근 코드 오입력이 많아 10분간 제한됩니다")
    if not secrets.compare_digest(given.strip().upper().encode(), code.upper().encode()):
        BAD_CODE.hit(ip)
        audit("code_fail", request, path=request.url.path)   # 입력한 값은 남기지 않는다
        raise HTTPException(401, "업로드 접근 코드가 올바르지 않습니다")


ANNOT_KINDS = {"room", "stairs", "exit", "hydrant", "extinguisher", "firedoor", "hazard"}
ANNOT_MAX = 60
ANNOT_TEXT_MAX = 30


def clean_annotations(items, floors) -> list[dict]:
    """표시 목록을 검증·정규화한다. 종류는 허용 목록, 좌표는 해당 층 도면 범위 안, 글자는 제어문자 제거 후 30자."""
    if not isinstance(items, list) or len(items) > ANNOT_MAX:
        raise HTTPException(422, f"표시는 최대 {ANNOT_MAX}개까지 저장할 수 있습니다")
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            raise HTTPException(422, "표시 형식이 올바르지 않습니다")
        fl, kind = it.get("floor"), it.get("kind")
        x, z = it.get("x"), it.get("z")
        if type(fl) is not int or not 0 <= fl < len(floors) or kind not in ANNOT_KINDS:
            raise HTTPException(422, "표시의 층 또는 종류가 올바르지 않습니다")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e4 for v in (x, z)):
            raise HTTPException(422, "표시 좌표가 올바르지 않습니다")
        x0, z0, x1, z1 = floors[fl]["plan_rect"]
        if not (x0 - 1 <= x <= x1 + 1 and z0 - 1 <= z <= z1 + 1):
            raise HTTPException(422, "표시가 도면 범위 밖에 있습니다")
        text = it.get("text", "")
        if not isinstance(text, str):
            raise HTTPException(422, "표시 글자가 올바르지 않습니다")
        text = " ".join("".join(c for c in text if c.isprintable()).split())[:ANNOT_TEXT_MAX]
        if kind == "room" and not text:
            raise HTTPException(422, "방 이름은 비워 둘 수 없습니다")
        aid = it.get("id")
        if not isinstance(aid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", aid) or aid in seen:
            raise HTTPException(422, "표시 번호가 올바르지 않습니다")
        seen.add(aid)
        out.append({"id": aid, "floor": fl, "kind": kind, "x": round(float(x), 2), "z": round(float(z), 2), "text": text})
    return out


@app.put("/api/models/{mid}/annotations")
async def put_annotations(mid: str, request: Request, x_delete_token: str = Header(default="")):
    """방 이름·소방시설 표시 전체를 교체한다. 업로드한 사람(삭제 토큰 소지자)만 가능."""
    meta = read_meta(mid)
    if not secrets.compare_digest(x_delete_token.encode(), meta.get("delete_token", "").encode()):
        raise HTTPException(403, "편집 권한이 없습니다")
    raw = await request.body()
    if len(raw) > 32768:
        raise HTTPException(413, "요청이 너무 큽니다")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(422, "요청 형식이 올바르지 않습니다")
    items = clean_annotations(body.get("items") if isinstance(body, dict) else None, meta["floors"])
    meta["annotations"] = items
    path = model_dir(mid) / "meta.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    tmp.replace(path)
    audit("annotations", request, id=mid, count=len(items))
    return {"annotations": items}


@app.get("/api/models")
def list_models(request: Request, x_upload_code: str = Header(default="")):
    """서버에 보관 중인 모델 목록(업로드 접근 코드 필요). 상황실이 다른 기기에서도 확인·삭제할 수 있게 한다."""
    check_upload_code(request, x_upload_code)
    audit("list", request)
    rows = []
    for d in DATA.iterdir():
        try:
            meta = json.loads((d / "meta.json").read_text("utf-8"))
            exp = expiry_of(meta, d.stat().st_mtime)
        except (OSError, ValueError):
            continue
        if exp is not None and exp < time.time():
            continue
        rows.append({"id": meta["id"], "name": meta.get("name", ""), "created": meta.get("created"),
                     "expires": datetime.fromtimestamp(exp, timezone.utc).isoformat() if exp else None,
                     "floors": len(meta.get("floors", [])), "confirmed": bool(meta.get("confirmed")),
                     "annotations": len(meta.get("annotations", []))})
    rows.sort(key=lambda r: r["created"] or "", reverse=True)
    return JSONResponse({"models": rows}, headers={"Cache-Control": "no-store"})


@app.get("/api/audit")
def get_audit(request: Request, limit: int = 100, x_upload_code: str = Header(default="")):
    """최근 감사 기록(업로드 접근 코드 필요). 누가(IP) 언제 업로드·삭제·접근 실패를 했는지 확인한다."""
    check_upload_code(request, x_upload_code)
    return JSONResponse({"events": read_audit(limit), "keep_days": AUDIT_DAYS}, headers={"Cache-Control": "no-store"})


@app.delete("/api/models/{mid}")
def delete_model(mid: str, request: Request, x_delete_token: str = Header(default=""), x_upload_code: str = Header(default="")):
    """삭제 토큰(업로드한 기기) 또는 업로드 접근 코드(상황실 등 다른 기기)로 삭제한다."""
    meta = read_meta(mid)
    by = "token"
    if x_delete_token and secrets.compare_digest(x_delete_token.encode(), meta.get("delete_token", "").encode()):
        pass
    elif x_upload_code:
        check_upload_code(request, x_upload_code)
        by = "code"
    else:
        audit("delete_denied", request, id=mid)
        raise HTTPException(403, "삭제 권한이 없습니다")
    shutil.rmtree(model_dir(mid), ignore_errors=True)
    audit("delete", request, id=mid, by=by)
    return {"deleted": mid}


@app.get("/m/{mid}/{fname}")
def model_file(mid: str, fname: str):
    if not (fname.endswith((".glb", ".jpg")) and fname[0] == "f" and fname[1:].split(".")[0].isdigit()):
        raise HTTPException(404)
    path = model_dir(mid) / fname
    if not path.exists():
        raise HTTPException(404)
    mt = "model/gltf-binary" if fname.endswith(".glb") else "image/jpeg"
    return FileResponse(path, media_type=mt, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/qr/{mid}.svg")
def qr(mid: str, request: Request):
    model_dir(mid)
    buf = io.BytesIO()
    segno.make(f"{share_base(request)}/v/{mid}", error="m").save(
        buf, kind="svg", scale=8, border=2, dark="#111", light="#fff", xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})
