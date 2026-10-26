"""입력 파일(이미지/PDF/DXF)을 처리용 래스터 페이지로 변환."""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

MAX_SIDE = 2000      # 처리 해상도 상한(긴 변 px). 속도와 정밀도의 절충점
MIN_SIDE = 1000      # 너무 작은 이미지는 모폴로지 여유를 위해 확대
MAX_PDF_PAGES = 20

WALL_LAYER_RE = re.compile(r"wall|wal\b|벽|구조|conc|콘크리트|col|기둥|a-wall", re.I)
FONT_PATHS = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]


class UnsupportedFile(ValueError):
    pass


@dataclass
class Page:
    image: np.ndarray                 # BGR uint8, 바닥 텍스처 겸 처리 입력
    label: str
    m_per_px: float | None = None     # 축척을 알면(DXF) 지정
    ink: np.ndarray | None = None     # DXF: 벽 레이어만 그린 마스크(이진화 생략)


def _fit(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    s = max(h, w)
    if s > MAX_SIDE:
        f = MAX_SIDE / s
        return cv2.resize(img, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    if s < MIN_SIDE:
        f = MIN_SIDE / s
        return cv2.resize(img, (round(w * f), round(h * f)), interpolation=cv2.INTER_CUBIC)
    return img


def _load_image(data: bytes) -> np.ndarray:
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)   # 휴대폰 사진 회전 보정
    except Exception as e:  # noqa: BLE001
        raise UnsupportedFile("이미지를 읽을 수 없습니다") from e
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    return _fit(np.asarray(im)[:, :, ::-1].copy())


def _load_pdf(data: bytes, label: str) -> list[Page]:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        if i >= MAX_PDF_PAGES:
            break
        z = MAX_SIDE / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3][:, :, ::-1]
        pages.append(Page(_fit(arr.copy()), label if doc.page_count == 1 else f"{label}-{i + 1}"))
    return pages


_UNIT_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}


def _load_dxf(data: bytes, label: str) -> list[Page]:
    from ezdxf import recover
    from ezdxf.disassemble import recursive_decompose
    from ezdxf import path as dxfpath

    try:
        doc, _ = recover.read(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise UnsupportedFile("DXF를 읽을 수 없습니다") from e

    polys: list[tuple[str, np.ndarray, bool]] = []   # (layer, pts, filled)
    texts: list[tuple[str, float, float, float]] = []
    for e in recursive_decompose(doc.modelspace()):
        t = e.dxftype()
        layer = e.dxf.get("layer", "0")
        try:
            if t in ("TEXT", "MTEXT"):
                p = e.dxf.insert
                txt = e.plain_text() if t == "MTEXT" else e.dxf.text
                texts.append((txt, p.x, p.y, e.dxf.get("char_height" if t == "MTEXT" else "height", 1.0)))
            elif t == "HATCH":
                for p in dxfpath.from_hatch(e):
                    pts = np.array([(v.x, v.y) for v in p.flattening(0.01 * max(1.0, _extent_guess(doc)))])
                    if len(pts) >= 3:
                        polys.append((layer, pts, e.dxf.get("solid_fill", 0) == 1))
            elif t in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "SOLID"):
                p = dxfpath.make_path(e)
                pts = np.array([(v.x, v.y) for v in p.flattening(0.002 * max(1.0, _extent_guess(doc)))])
                if len(pts) >= 2:
                    polys.append((layer, pts, t == "SOLID"))
        except Exception:  # noqa: BLE001 - 깨진 엔티티는 건너뜀
            continue
    if not polys:
        raise UnsupportedFile("DXF에 도형이 없습니다")

    allpts = np.vstack([p for _, p, _ in polys])
    lo, hi = np.percentile(allpts, 0.5, axis=0), np.percentile(allpts, 99.5, axis=0)
    span = float(max(hi - lo))
    units = doc.header.get("$INSUNITS", 0)
    unit_m = _UNIT_M.get(units) or (0.001 if span > 500 else 1.0)
    margin = span * 0.04
    ppu = (MAX_SIDE - 1) / (span + 2 * margin)
    w = int((hi[0] - lo[0] + 2 * margin) * ppu) + 1
    h = int((hi[1] - lo[1] + 2 * margin) * ppu) + 1

    def to_px(pts):
        x = (pts[:, 0] - lo[0] + margin) * ppu
        y = (hi[1] + margin - pts[:, 1]) * ppu       # DXF y-up → 이미지 y-down
        return np.round(np.stack([x, y], 1)).astype(np.int32)

    wall_layers = {l for l, _, _ in polys if WALL_LAYER_RE.search(l)}
    tex = np.full((h, w, 3), 255, np.uint8)
    ink = np.zeros((h, w), np.uint8)
    for layer, pts, filled in polys:
        q = to_px(pts)
        is_wall = not wall_layers or layer in wall_layers
        color = (20, 20, 20) if is_wall else (120, 120, 120)
        if filled:
            cv2.fillPoly(tex, [q], color)
        else:
            cv2.polylines(tex, [q], False, color, 2 if is_wall else 1, cv2.LINE_AA)
        if is_wall:
            if filled:
                cv2.fillPoly(ink, [q], 255)
            else:
                cv2.polylines(ink, [q], False, 255, 2)
    if texts:
        tex = _draw_texts(tex, [(s, *to_px(np.array([[x, y]]))[0], hh * ppu) for s, x, y, hh in texts])
    return [Page(tex, label, m_per_px=unit_m / ppu, ink=ink)]


def _extent_guess(doc) -> float:
    try:
        ext = doc.header.get("$EXTMAX", (1, 1, 0)), doc.header.get("$EXTMIN", (0, 0, 0))
        return max(1.0, math.dist(ext[0][:2], ext[1][:2]))
    except Exception:  # noqa: BLE001
        return 1000.0


def _draw_texts(tex: np.ndarray, items) -> np.ndarray:
    fp = next((p for p in FONT_PATHS if Path(p).exists()), None)
    if not fp:
        return tex
    im = Image.fromarray(tex[:, :, ::-1])
    d = ImageDraw.Draw(im)
    for s, x, y, hh in items:
        size = int(max(8, min(200, hh)))
        try:
            d.text((x, y), str(s)[:60], fill=(0, 0, 160), font=ImageFont.truetype(fp, size), anchor="ls")
        except Exception:  # noqa: BLE001
            pass
    return np.asarray(im)[:, :, ::-1].copy()


def load_pages(data: bytes, filename: str, label: str) -> list[Page]:
    ext = Path(filename or "").suffix.lower()
    head = data[:2048]
    if head.startswith(b"%PDF") or ext == ".pdf":
        return _load_pdf(data, label)
    if ext == ".dxf" or (b"SECTION" in head and b"HEADER" in head or b"ENTITIES" in head):
        return _load_dxf(data, label)
    if ext == ".dwg" or head.startswith(b"AC10"):
        raise UnsupportedFile("DWG는 직접 지원하지 않습니다. CAD에서 DXF 또는 PDF로 저장해 올려주세요")
    return [Page(_load_image(data), label)]
