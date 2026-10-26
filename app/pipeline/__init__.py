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
    r = extract(page.image, ink=page.ink, m_per_px=page.m_per_px, width_m=width_m)
    tex = cv2.imencode(".jpg", page.image, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
    glb, info = build_glb(r, tex, page.image.shape, wall_height, page.label)
    info["ms"] = round((time.perf_counter() - t0) * 1000)
    return FloorOutput(glb, preview_jpeg(page.image, r), info)
