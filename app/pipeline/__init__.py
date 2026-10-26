"""도면 → 3D 생성 파이프라인 진입점."""
from __future__ import annotations

import time
from dataclasses import dataclass

import cv2

from .loaders import Page, UnsupportedFile, load_pages  # noqa: F401
from .mesh import build_glb, preview_jpeg
from .walls import extract


@dataclass
class FloorOutput:
    glb: bytes
    preview: bytes
    info: dict


def build_floor(page: Page, wall_height: float = 2.8, width_m: float | None = None) -> FloorOutput:
    t0 = time.perf_counter()
    image, notes = page.image, list(page.notes)
    try:
        r = extract(image, ink=page.ink, m_per_px=page.m_per_px, width_m=width_m)
    except ValueError:
        r = None
    if page.alt is not None:
        # 기울기·원근 보정이 인식을 나쁘게 만들 수 있으므로 보정 전후를 모두 인식해 닫힌 방이 더 많은 쪽을 쓴다(동점이면 원본)
        try:
            r0 = extract(page.alt, ink=page.ink, m_per_px=page.m_per_px, width_m=width_m)
        except ValueError:
            r0 = None
        if r0 is not None and (r is None or r0.rooms >= r.rooms):
            r, image = r0, page.alt
            notes = [n for n in notes if n != page.rect_note] + ["전처리: 기울기·원근 보정을 시도했지만 원본이 더 잘 인식되어 원본을 썼습니다"]
    if r is None:
        raise ValueError("벽을 찾지 못했습니다")
    r.notes = [*notes, *r.notes]
    tex = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
    glb, info = build_glb(r, tex, image.shape, wall_height, page.label)
    info["ms"] = round((time.perf_counter() - t0) * 1000)
    return FloorOutput(glb, preview_jpeg(image, r), info)
