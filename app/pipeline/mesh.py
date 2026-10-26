"""벽/문/창 마스크 → 돌출(extrude) 메시 → GLB."""
from __future__ import annotations

import cv2
import mapbox_earcut as earcut
import numpy as np

from .glb import GLB
from .walls import WallResult

DOOR_TOP_M = 2.1
SILL_M = 0.9
SLAB_M = 0.15

COLORS = {
    "wall": (0.90, 0.88, 0.84),
    "wall_top": (0.20, 0.22, 0.26),
    "door": (0.10, 0.80, 0.40),
    "glass": (0.35, 0.70, 1.00),
    "window": (0.20, 0.55, 0.95),
    "floor": (0.42, 0.44, 0.47),
}


class Geo:
    def __init__(self):
        self.pos, self.nrm, self.idx, self.uv = [], [], [], []
        self.n = 0

    def add(self, pos, nrm, tri, uv=None):
        self.idx.append(tri + self.n)
        self.pos.append(pos)
        self.nrm.append(nrm)
        if uv is not None:
            self.uv.append(uv)
        self.n += len(pos)

    def arrays(self):
        if not self.pos:
            return None
        g = {"pos": np.vstack(self.pos).astype(np.float32), "nrm": np.vstack(self.nrm).astype(np.float32),
             "idx": np.concatenate(self.idx).astype(np.uint32)}
        if self.uv:
            g["uv"] = np.vstack(self.uv).astype(np.float32)
        return g


def _area(r):
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def polygons(mask: np.ndarray, eps: float, min_area: float):
    cs, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None:
        return []
    hier = hier[0]
    out = []
    for i, c in enumerate(cs):
        if hier[i][3] != -1 or cv2.contourArea(c) < min_area:
            continue
        outer = cv2.approxPolyDP(c, eps, True)[:, 0, :].astype(np.float64)
        if len(outer) < 3:
            continue
        holes, j = [], hier[i][2]
        while j != -1:
            if cv2.contourArea(cs[j]) >= min_area:
                h = cv2.approxPolyDP(cs[j], eps, True)[:, 0, :].astype(np.float64)
                if len(h) >= 3:
                    holes.append(h)
            j = hier[j][0]
        out.append((outer, holes))
    return out


def prism(side: Geo, top: Geo | None, poly, y0: float, y1: float, T):
    """다각형(외곽+구멍)을 y0~y1 로 돌출. 바닥면은 보이지 않으므로 생략."""
    outer, holes = poly
    rings = [outer if _area(outer) > 0 else outer[::-1]]
    rings += [h if _area(h) < 0 else h[::-1] for h in holes]
    h = y1 - y0
    for r in rings:
        p = T(r)
        q = np.roll(p, -1, axis=0)
        d = q - p
        ln = np.linalg.norm(d, axis=1)
        keep = ln > 1e-6
        p, q, d, ln = p[keep], q[keep], d[keep], ln[keep]
        n = len(p)
        if n == 0:
            continue
        out = np.stack([d[:, 1] / ln, np.zeros(n), -d[:, 0] / ln], 1)   # 바깥 법선
        a = np.column_stack([p[:, 0], np.full(n, y0), p[:, 1]])
        b = np.column_stack([q[:, 0], np.full(n, y0), q[:, 1]])
        c = np.column_stack([q[:, 0], np.full(n, y1), q[:, 1]])
        dd = np.column_stack([p[:, 0], np.full(n, y1), p[:, 1]])
        pos = np.stack([a, b, c, dd], 1).reshape(-1, 3)
        nrm = np.repeat(out, 4, axis=0)
        base = np.arange(n)[:, None] * 4
        tri = np.concatenate([base + [0, 2, 1], base + [0, 3, 2]], axis=0)
        side.add(pos, nrm, tri)
    if top is None or h <= 0:
        return
    flat = np.vstack(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    tri = np.asarray(earcut.triangulate_float32(flat.astype(np.float32), ends), np.int64).reshape(-1, 3)
    if len(tri) == 0:
        return
    w = T(flat)
    p0, p1, p2 = w[tri[:, 0]], w[tri[:, 1]], w[tri[:, 2]]
    cy = (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0]) - (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
    flip = cy < 0
    tri[flip] = tri[flip][:, [0, 2, 1]]
    pos = np.column_stack([w[:, 0], np.full(len(w), y1), w[:, 1]])
    top.add(pos, np.tile([0, 1, 0], (len(w), 1)), tri)


def build_glb(r: WallResult, texture_jpeg: bytes, image_shape, wall_h: float, label: str):
    H, W = image_shape[:2]
    s = r.m_per_px
    ys, xs = np.nonzero(r.walls)
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2

    def T(pts):
        return np.column_stack([(pts[:, 0] - cx) * s, (pts[:, 1] - cy) * s])

    eps = max(1.0, 0.06 * r.t_px)
    min_area = max(4.0, 0.25 * r.t_px ** 2)
    door_top = min(DOOR_TOP_M, wall_h - 0.1)
    sill = min(SILL_M, door_top - 0.3)

    wall_side, wall_top, door, glass, glass_top, floor, floor_side, win_top = (Geo() for _ in range(8))
    wall_polys = polygons(r.walls, eps, min_area)
    for poly in wall_polys:
        prism(wall_side, wall_top, poly, 0.0, wall_h, T)
    door_polys = polygons(r.doors, eps, 4)
    for poly in door_polys:
        prism(wall_side, door, poly, door_top, wall_h, T)     # 상인방 윗면을 초록으로 → 평면에서 문 위치가 보임
        prism(door, door, poly, 0.0, 0.03, T)
    win_polys = polygons(r.windows, eps, 4)
    for poly in win_polys:
        prism(wall_side, wall_top, poly, 0.0, sill, T)
        prism(wall_side, win_top, poly, door_top, wall_h, T)
        prism(glass, glass_top, poly, sill, door_top, T)
    for poly in polygons(r.footprint, max(2.0, eps * 2), 100):
        prism(floor_side, floor, poly, -SLAB_M, 0.0, T)

    # 원본 도면 바닥 텍스처(전체 이미지 범위)
    plan = Geo()
    corners = T(np.array([[0, 0], [W, 0], [W, H], [0, H]], np.float64))
    plan.add(np.column_stack([corners[:, 0], np.full(4, 0.004), corners[:, 1]]), np.tile([0, 1, 0], (4, 1)),
             np.array([[0, 2, 1], [0, 3, 2]]), uv=np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32))

    g = GLB()
    m_wall = g.material("wall", COLORS["wall"])
    m_top = g.material("wall_top", COLORS["wall_top"])
    m_door = g.material("door", COLORS["door"], emissive=(0.0, 0.25, 0.1))
    m_glass = g.material("glass", COLORS["glass"], roughness=0.2, alpha=0.45, double_sided=True)
    m_win = g.material("window", COLORS["window"], emissive=(0.0, 0.08, 0.2))
    m_floor = g.material("floor", COLORS["floor"])
    m_plan = g.material("plan", (1, 1, 1), unlit=True, texture=texture_jpeg)
    g.mesh("plan", [(plan.arrays(), m_plan)])
    g.mesh("floor", [(floor.arrays(), m_floor), (floor_side.arrays(), m_floor)])
    g.mesh("walls", [(wall_side.arrays(), m_wall), (wall_top.arrays(), m_top)])
    g.mesh("doors", [(door.arrays(), m_door)])
    g.mesh("glass", [(glass.arrays(), m_glass), (glass_top.arrays(), m_glass), (win_top.arrays(), m_win)])
    bx = ((xs.max() - xs.min()) * s, (ys.max() - ys.min()) * s)
    info = {
        "label": label, "width_m": round(float(bx[0]), 2), "depth_m": round(float(bx[1]), 2),
        "wall_height": wall_h, "m_per_px": round(s, 6), "scale_source": r.scale_source, "mode": r.mode,
        "doors": len(door_polys), "windows": len(win_polys), "wall_parts": len(wall_polys),
        "plan_rect": [round(float(v), 3) for v in (*corners[0], *corners[2])], "notes": r.notes,
        # 진입구: [x, z, 안쪽 방향 dx, dz] (m)
        "entrances": [[round(float((x - cx) * s), 3), round(float((y - cy) * s), 3), nx, ny] for x, y, nx, ny in r.entrances],
    }
    return g.build(extras=info), info


def preview_jpeg(bgr: np.ndarray, r: WallResult, width=1000) -> bytes:
    """링크 미리보기(og:image)용 평면 요약 이미지."""
    out = (bgr.astype(np.float32) * 0.35 + 165).astype(np.uint8)
    out[r.walls > 0] = (40, 50, 60)
    out[r.doors > 0] = (90, 200, 30)
    out[r.windows > 0] = (255, 170, 60)
    ys, xs = np.nonzero(r.walls | r.footprint)
    pad = int(0.04 * max(out.shape[:2]))
    y0, y1 = max(0, ys.min() - pad), min(out.shape[0], ys.max() + pad)
    x0, x1 = max(0, xs.min() - pad), min(out.shape[1], xs.max() + pad)
    out = out[y0:y1, x0:x1]
    f = width / out.shape[1]
    out = cv2.resize(out, (width, max(1, int(out.shape[0] * f))), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
