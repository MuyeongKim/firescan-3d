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
import secrets
import shutil
import socket
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
BAD_CODE = RateLimit(8, 600)        # 접근 코드 오입력 10분에 8회


def cleanup_expired():
    if TTL_DAYS <= 0 or not DATA.exists():
        return
    limit = time.time() - TTL_DAYS * 86400
    for d in DATA.iterdir():
        if d.is_dir() and d.stat().st_mtime < limit:
            shutil.rmtree(d, ignore_errors=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    DATA.mkdir(parents=True, exist_ok=True)

    async def janitor():
        while True:
            await asyncio.to_thread(cleanup_expired)
            await asyncio.sleep(6 * 3600)

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
    if not (d / "meta.json").exists():
        raise HTTPException(404, "모델을 찾을 수 없거나 보관 기한이 지났습니다")
    return d


def read_meta(mid: str) -> dict:
    return json.loads((model_dir(mid) / "meta.json").read_text("utf-8"))


def public_meta(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k != "delete_token"}


# ------------------------------------------------------------------ pages
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/v/{mid}", response_class=HTMLResponse)
def viewer(mid: str, request: Request):
    try:
        meta = read_meta(mid)
    except HTTPException:
        return HTMLResponse((STATIC / "missing.html").read_text("utf-8"), status_code=404)
    base = share_base(request)
    floors = meta["floors"]
    title = meta.get("name") or "건물 내부구조"
    desc = f"{len(floors)}개 층 · " + " / ".join(f["label"] for f in floors[:6]) + " · 탭하여 3D로 확인"
    page = (STATIC / "viewer.html").read_text("utf-8")
    for k, v in {
        "{{TITLE}}": html.escape(title), "{{DESC}}": html.escape(desc),
        "{{OG_IMAGE}}": html.escape(f"{base}/m/{mid}/{floors[0]['preview']}"),
        "{{OG_URL}}": html.escape(f"{base}/v/{mid}"), "{{MODEL_ID}}": html.escape(mid),
        "{{FIRST_GLB}}": html.escape(f"/m/{mid}/{floors[0]['glb']}"),
    }.items():
        page = page.replace(k, v)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


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
    x_upload_code: str = Header(default=""),
):
    t0 = time.perf_counter()
    ip = request.client.host if request.client else "?"
    if code := setting("UPLOAD_CODE", "upload_code"):
        if BAD_CODE.full(ip):
            raise HTTPException(429, "접근 코드 오입력이 많아 10분간 업로드가 제한됩니다")
        if not secrets.compare_digest(x_upload_code.strip().upper().encode(), code.upper().encode()):
            BAD_CODE.hit(ip)
            raise HTTPException(401, "업로드 접근 코드가 올바르지 않습니다")
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

    meta = {
        "id": mid, "name": name.strip()[:80], "created": datetime.now(timezone.utc).isoformat(),
        "wall_height": wall_height, "floors": floors, "warnings": warnings,
        "total_ms": round((time.perf_counter() - t0) * 1000), "delete_token": secrets.token_urlsafe(16),
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    base = share_base(request)
    return JSONResponse({**meta, "url": f"{base}/v/{mid}", "qr": f"/qr/{mid}.svg",
                         "local_url": f"/v/{mid}"}, status_code=201)


@app.get("/api/models/{mid}")
def get_model(mid: str, request: Request):
    meta = public_meta(read_meta(mid))
    return JSONResponse({**meta, "url": f"{share_base(request)}/v/{mid}"},
                        headers={"Cache-Control": "no-cache"})


@app.delete("/api/models/{mid}")
def delete_model(mid: str, x_delete_token: str = Header(default="")):
    meta = read_meta(mid)
    if not secrets.compare_digest(x_delete_token, meta.get("delete_token", "")):
        raise HTTPException(403, "삭제 권한이 없습니다")
    shutil.rmtree(model_dir(mid), ignore_errors=True)
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
