"""평면도 래스터 → 벽/문/창 마스크와 축척 추정.

규칙 기반(OpenCV)으로 학습 데이터 없이 1초 내외에 끝나도록 설계했다.
도면 종류를 자동 판별해 처리 방식을 고른다.
  filled : 벽이 검게 채워진 도면 → 얇은 획(글자·문 호·가구·치수) 제거
  double : 2중선/해치 벽(CAD 출력) → 선 사이 공동을 메운 뒤 filled와 동일 처리
  single : 단선 도면(피난안내도·스케치) → 직선 성분만 벽으로 사용
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

WALL_T_M = 0.2           # 벽 두께 추정 기준(국내 주거/업무시설 평균)
DOOR_M = 0.9             # 문 폭 기준(단선 도면 축척 추정용)
ASSUMED_SPAN_M = 20.0    # 축척 단서가 없을 때 건물 긴 변 가정
DOOR_GAP_M = (0.68, 1.3)
WINDOW_GAP_M = (0.5, 4.0)
HEAL_GAP_M = 0.45        # 이보다 좁은 틈은 벽으로 메움


@dataclass
class WallResult:
    walls: np.ndarray                  # uint8 0/255
    doors: np.ndarray                  # 문 개구부(상인방 생성)
    windows: np.ndarray                # 창 개구부(창대+상인방+유리)
    footprint: np.ndarray              # 건물 바닥 영역
    mode: str
    t_px: float                        # 대표 벽 두께(px)
    m_per_px: float
    scale_source: str                  # user | dxf | wall | door | assumed
    notes: list[str] = field(default_factory=list)
    entrances: list[tuple[float, float, float, float]] = field(default_factory=list)  # (x, y, 안쪽 방향 nx, ny) px


# ------------------------------------------------------------------ helpers
def _odd(n: float) -> int:
    n = max(1, int(round(n)))
    return n if n % 2 else n + 1


def _rect(w: int, h: int):
    return cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, int(w)), max(1, int(h))))


def _ellipse(d: float):
    d = _odd(d)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))


def binarize(bgr: np.ndarray) -> np.ndarray:
    """어두운 무채색 획 = 잉크. 조명 불균일(사진)이면 배경을 나눠 평탄화."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, w = gray.shape
    small = cv2.resize(gray, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
    k = _odd(max(small.shape) / 12)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, _rect(k, k))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    spread = float(np.percentile(bg, 97) - np.percentile(bg, 3))
    if spread > 25:
        bgf = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        gray = np.clip(gray.astype(np.float32) / np.maximum(bgf, 1) * 245, 0, 255).astype(np.uint8)
    thr, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = min(thr, 150)
    dark = gray < thr
    vivid = (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70)   # 빨간 피난화살표, 초록 비상구 등 컬러 표식 제외
    ink = (dark & ~vivid).astype(np.uint8) * 255
    return cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) if spread > 25 else ink


def remove_clutter(ink: np.ndarray) -> np.ndarray:
    """글자·기호 같은 작은 성분과 사진 테두리의 큰 어두운 덩어리 제거."""
    H, W = ink.shape
    long = max(H, W)
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    bw, bh, area = st[:, 2], st[:, 3], st[:, 4]
    smax = np.maximum(bw, bh)
    fill = area / np.maximum(bw * bh, 1)
    small = smax < 0.015 * long
    textlike = small & (fill < 0.6)
    tiny = area < max(8, (0.002 * long) ** 2)
    x0, y0 = st[:, 0], st[:, 1]
    border = (x0 == 0) | (y0 == 0) | (x0 + bw >= W) | (y0 + bh >= H)
    blob = border & ((bw > 0.5 * W) | (bh > 0.5 * H)) & (fill > 0.5)
    drop = textlike | tiny | blob
    drop[0] = True
    keep = ~drop
    return (keep[lab]).astype(np.uint8) * 255


def width_hist(mask: np.ndarray, area_weighted=True, maxw=None):
    """획 폭 분포: 거리변환의 능선(중심선) 값 = 반폭."""
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    ridge = (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8)) - 1e-3) & (dt > 0.5)
    w = np.maximum(1, 2 * dt[ridge] - 1)
    maxw = maxw or int(w.max(initial=1)) + 2
    wi = np.minimum(np.round(w).astype(int), maxw - 1)
    return np.bincount(wi, weights=w if area_weighted else None, minlength=maxw).astype(np.float64)


def _smooth(h: np.ndarray, s=1.0):
    k = np.exp(-0.5 * (np.arange(-3, 4) / s) ** 2)
    return np.convolve(h, k / k.sum(), mode="same")


def _peaks(h: np.ndarray, rel=0.08):
    m = h.max(initial=0)
    out = []
    for i in range(1, len(h) - 1):
        if h[i] >= h[i - 1] and h[i] > h[i + 1] and h[i] >= rel * m:
            out.append(i)
    return out


# ------------------------------------------------------------------ stages
def fill_cavities(ink: np.ndarray, long: int):
    """2중선/해치 벽: 선으로 둘러싸인 좁은 배경 영역(벽 속)을 채운다.
    배경 능선 폭이 좁은 값에 몰려 있을 때만 동작. 반환: (채운 마스크, 대표 공동 폭) 또는 None."""
    inv = cv2.bitwise_not(ink)
    n, lab = cv2.connectedComponents(inv, connectivity=4)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    enclosed = inv.copy()
    enclosed[np.isin(lab, border)] = 0
    hb = width_hist(enclosed, area_weighted=False, maxw=int(0.03 * long) + 3)
    hi = width_hist(ink, area_weighted=False)
    lo = 3
    if hb[lo:].sum() == 0:
        return None
    p = int(np.argmax(_smooth(hb)[lo:])) + lo
    band = hb[max(lo, int(p / 1.6)): int(p * 1.8) + 1]
    if band.sum() / max(hi.sum(), 1) < 0.15:
        return None
    # 폭이 wfill 보다 좁은 부분만 채움(기둥 내부처럼 뚱뚱한 부분은 열림 연산에서 살아남음)
    wfill = min(2.6 * p, 0.035 * long)
    thin = cv2.bitwise_and(enclosed, cv2.bitwise_not(cv2.morphologyEx(enclosed, cv2.MORPH_OPEN, _ellipse(wfill))))
    out = ink | thin
    # 벽으로 완전히 둘러싸인 작은 구멍(기둥 속, 샤프트) 채움
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(out), connectivity=4)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    small = st[:, 4] < (6 * p) ** 2
    small[border] = False
    return out | (small[lab].astype(np.uint8) * 255), p


def choose_open_kernel(mask: np.ndarray, long: int):
    """채움 벽 판별: 얇은 획 봉우리와 두꺼운 벽 봉우리 사이 골짜기 = 제거 기준 폭."""
    h = width_hist(mask, area_weighted=True, maxw=int(0.05 * long) + 3)
    hs = _smooth(h)
    pk = _peaks(hs)                      # 면적이 큰 봉우리(벽 후보)
    if not pk:
        return None
    faint = _peaks(hs, rel=0.01)         # 얇은 획은 면적이 작아 낮은 기준으로 찾는다
    thin = faint[0] if faint and faint[0] * 1.8 <= pk[0] else pk[0]
    thick = [p for p in pk if p >= max(4, 1.8 * thin)]
    if thick:
        tp = thick[0]
        seg = hs[thin: tp + 1]
        # 골짜기는 평평한 0 구간인 경우가 많다 → 첫 저점 구간의 가운데
        low = np.nonzero(seg <= seg.min() + 0.02 * hs.max())[0]
        run_end = low[0]
        while run_end + 1 < len(seg) and seg[run_end + 1] <= seg.min() + 0.02 * hs.max():
            run_end += 1
        valley = thin + (low[0] + run_end) // 2
        k = int(np.clip(valley, 3, 0.7 * tp))
    elif thin >= 0.005 * long:          # 얇은 획 없이 벽만 있는 도면
        k = max(3, int(0.5 * thin))
    else:
        return None
    ws = np.arange(len(h))
    sel = ws >= k
    if h[sel].sum() < 0.2 * h.sum():
        return None
    cum = np.cumsum(h[sel]) / h[sel].sum()
    t = float(ws[sel][np.searchsorted(cum, 0.5)])
    return k, t


def single_line_walls(ink: np.ndarray, long: int):
    """단선 도면: 수평/수직 직선 + 긴 사선만 벽으로."""
    L = max(12, int(0.012 * long))
    hv = cv2.morphologyEx(ink, cv2.MORPH_OPEN, _rect(L, 1)) | cv2.morphologyEx(ink, cv2.MORPH_OPEN, _rect(1, L))
    resid = cv2.bitwise_and(ink, cv2.bitwise_not(cv2.dilate(hv, np.ones((5, 5), np.uint8))))
    stroke = choose_stroke(ink)
    lines = cv2.HoughLinesP(resid, 1, np.pi / 360, threshold=L, minLineLength=int(2.5 * L), maxLineGap=3)
    out = hv.copy()
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            cv2.line(out, (x1, y1), (x2, y2), 255, max(1, int(round(stroke))))
    return out, stroke


def choose_stroke(ink):
    h = width_hist(ink, area_weighted=False)
    return float(max(1, np.argmax(_smooth(h)[1:]) + 1))


def run_len(m: np.ndarray) -> np.ndarray:
    """행 방향으로 각 True 픽셀이 속한 연속 구간의 길이."""
    h, w = m.shape
    idx = np.broadcast_to(np.arange(w, dtype=np.int32), (h, w))
    last_f = np.maximum.accumulate(np.where(~m, idx, -1), axis=1)
    next_f = np.minimum.accumulate(np.where(~m, idx, w)[:, ::-1], axis=1)[:, ::-1]
    return np.where(m, next_f - last_f - 1, 0)


def wall_gaps(m: np.ndarray, gmax: int, min_run: float, t: float):
    """가로 방향으로 같은 줄의 두 벽 끝 사이 빈칸(길이 ≤ gmax)과 그 길이.
    양쪽 벽이 그 방향으로 min_run 이상 이어져야 한다 → 방 내부(직교 벽 사이)는 제외.
    세로 방향은 호출 측에서 전치해서 쓴다."""
    h, w = m.shape
    idx = np.broadcast_to(np.arange(w, dtype=np.int32), (h, w))
    left = np.maximum.accumulate(np.where(m, idx, -1), axis=1)
    right = np.minimum.accumulate(np.where(m, idx, 2 * w)[:, ::-1], axis=1)[:, ::-1]
    span = right - left - 1
    ok = (~m) & (left >= 0) & (right < 2 * w) & (span <= gmax)
    rl = run_len(m)
    lr = np.take_along_axis(rl, np.clip(left, 0, w - 1), 1)
    rr = np.take_along_axis(rl, np.clip(right, 0, w - 1), 1)
    ok &= (lr >= min_run) & (rr >= min_run)
    # 벽면에 바로 붙은 띠(기둥 돌출부 사이)는 개구부가 아님: 수직 방향 ±t 안에 벽이 없어야 함
    near = cv2.dilate(m.astype(np.uint8), _rect(1, 2 * int(np.ceil(t)) + 1)) > 0
    return ok & ~near, span


@dataclass
class Gap:
    x: int
    y: int
    region: np.ndarray      # bool, bbox 크기
    length: int             # px
    window: bool
    swing: bool = False     # 문짝(경첩에서 수직으로 뻗은 선) 확인됨
    axis: int = 1           # 1: 가로 벽의 개구부, 0: 세로 벽의 개구부


def find_gaps(walls: np.ndarray, ink_orig: np.ndarray | None, t: float, gmax: int, min_run: float) -> list[Gap]:
    wb = walls > 0
    thin_lim = 1.6 * t + 3
    swing_src = ink_orig if ink_orig is not None else walls
    out = []
    for axis in (1, 0):            # 1: 원본 그대로(가로 벽), 0: 전치(세로 벽)
        m = wb if axis == 1 else wb.T
        ink = None if ink_orig is None else (ink_orig if axis == 1 else ink_orig.T)
        cand, span = wall_gaps(m, gmax, min_run, t)
        n, lab, st, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=4)
        for i in range(1, n):
            x, y, w, h, _ = st[i]
            reg = lab[y:y + h, x:x + w] == i
            # 바깥쪽에서 붙어 들어온 더 긴 빈칸 줄은 잘라낸다(개구부 줄은 길이가 같다)
            rs = np.where(reg, span[y:y + h, x:x + w], 1 << 30).min(axis=1)
            keep = rs <= rs.min() * 1.15 + 1
            r0 = r1 = int(np.argmin(rs))
            while r0 > 0 and keep[r0 - 1]:
                r0 -= 1
            while r1 + 1 < h and keep[r1 + 1]:
                r1 += 1
            reg = reg[r0:r1 + 1]
            cols = np.nonzero(reg.any(axis=0))[0]
            reg = reg[:, cols[0]:cols[-1] + 1]
            X, Y = x + int(cols[0]), y + r0
            hh, ww = reg.shape
            if hh > thin_lim or reg.sum() < 0.5 * ww * hh:
                continue
            win = False
            if ink is not None:
                inside = (ink[Y:Y + hh, X:X + ww] > 0) & reg
                win = inside.sum() / max(reg.sum(), 1) > 0.08 and inside.any(axis=0).mean() > 0.7
            gx, gy, R = (X, Y, reg) if axis == 1 else (Y, X, reg.T)
            sw = has_swing(swing_src, gx, gy, R.shape[1], R.shape[0], axis)
            out.append(Gap(gx, gy, R, int(ww), win, sw, axis))
    return out


def has_swing(ink: np.ndarray, x: int, y: int, w: int, h: int, axis: int) -> bool:
    """개구부 양 끝(문틀) 근처에서 벽에 수직으로 뻗은 문짝 선이 있는지."""
    m = ink if axis == 1 else ink.T
    if axis == 0:
        x, y, w, h = y, x, h, w
    L = w
    H, W = m.shape
    for c0 in (x, x + w - 1):
        cols = slice(max(0, c0 - 4), min(W, c0 + 5))
        for rows in (slice(max(0, y - L), y), slice(y + h, min(H, y + h + L))):
            band = m[rows, cols] > 0
            if band.size and band.sum(axis=0).max(initial=0) >= 0.6 * L:
                return True
    return False


def paint(shape, gaps: list[Gap]) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    for g in gaps:
        h, w = g.region.shape
        m[g.y:g.y + h, g.x:g.x + w][g.region] = 255
    return m


def door_scale(gaps: list[Gap], t: float, single: bool):
    """문 개구부 길이 중앙값 = 0.9m 로 축척 추정. 문 폭은 표준화돼 있어 벽 두께보다 안정적."""
    lo, hi = (6, 60) if single else (2.5, 9)    # 벽 두께 대비 문 폭 비율 범위
    cand = [g for g in gaps if not g.window and lo * t <= g.length <= hi * t]
    swung = [g.length for g in cand if g.swing]
    lens = swung if len(swung) >= 2 else [g.length for g in cand]
    if len(lens) < (2 if lens is swung else 3):
        return None
    return DOOR_M / float(np.median(lens)), len(lens)


def find_entrances(doors: list[Gap], footprint: np.ndarray, m_per_px: float):
    """외벽에 난 문 = 진입구. 문 양쪽을 찍어 한쪽만 건물 밖이면 외부 출입문."""
    H, W = footprint.shape
    d = 0.7 / m_per_px
    out = []
    for g in doors:
        h, w = g.region.shape
        cx, cy = g.x + w / 2, g.y + h / 2
        nx, ny = (0.0, 1.0) if g.axis == 1 else (1.0, 0.0)
        half = (h if g.axis == 1 else w) / 2
        inside = []
        for sgn in (1, -1):
            px, py = int(cx + sgn * nx * (half + d)), int(cy + sgn * ny * (half + d))
            inside.append(0 <= px < W and 0 <= py < H and footprint[py, px] > 0)
        if inside[0] != inside[1]:
            sgn = 1 if inside[0] else -1
            out.append((not g.swing, -g.length, (cx, cy, sgn * nx, sgn * ny)))
    return [e for *_, e in sorted(out)]     # 문짝 확인된 것, 넓은 것 우선


def footprint_of(walls: np.ndarray, m_per_px: float, t: float) -> np.ndarray:
    """건물 바닥: 벽선 위의 끊김(문·창·넓은 개구부 ≤5m)을 이어 붙인 뒤 외곽 윤곽을 채움."""
    wb = walls > 0
    g = int(5.0 / m_per_px)
    bridged = wb | wall_gaps(wb, g, 1.25 * t, t)[0] | wall_gaps(wb.T, g, 1.25 * t, t)[0].T
    m = bridged.astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, _rect(3, 3))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, cs, -1, 255, -1)
    return out


# ------------------------------------------------------------------ main
def extract(bgr: np.ndarray, ink: np.ndarray | None = None, m_per_px: float | None = None,
            width_m: float | None = None) -> WallResult:
    H, W = bgr.shape[:2]
    long = max(H, W)
    notes: list[str] = []
    ink_orig = binarize(bgr)
    if ink is None:
        ink = ink_orig
    clean = remove_clutter(ink)

    mode, base, ko = "filled", clean, None
    cav = fill_cavities(clean, long)
    if cav:
        filled, p = cav
        ko = choose_open_kernel(filled, long)
        if ko:
            mode, base = "double", filled
            notes.append(f"2중선/해치 벽 (공동 {p}px)")
    if mode == "filled":
        ko = choose_open_kernel(clean, long)
        if not ko:
            mode = "single"

    if mode == "single":
        walls, t = single_line_walls(clean, long)
    else:
        k, t = ko
        walls = cv2.morphologyEx(base, cv2.MORPH_OPEN, _rect(k, k))

    # 작은 잔여물 제거
    n, lab, st, _ = cv2.connectedComponentsWithStats(walls, connectivity=8)
    min_area = max(3 * t * t, 0.0001 * H * W) if mode != "single" else (0.01 * long) ** 2
    keep = st[:, 4] >= min_area
    keep[0] = False
    walls = keep[lab].astype(np.uint8) * 255
    ys, xs = np.nonzero(walls)
    if len(xs) == 0:
        raise ValueError("벽을 찾지 못했습니다")
    span_px = max(xs.max() - xs.min(), ys.max() - ys.min(), 1)

    # 개구부(픽셀 단위) → 축척 결정
    single = mode == "single"
    min_run = (2.5 * t + 2) if single else 1.25 * t
    gaps = find_gaps(walls, None if single else ink_orig, t, int(0.25 * long), min_run)
    if width_m:
        m_per_px, scale_source = width_m / max(xs.max() - xs.min(), 1), "user"
    elif m_per_px:
        scale_source = "dxf"
    elif (ds := door_scale(gaps, t, single)):
        m_per_px, scale_source = ds[0], "door"
        notes.append(f"문 {ds[1]}개 폭으로 축척 추정")
    elif not single:
        m_per_px, scale_source = WALL_T_M / t, "wall"
    else:
        m_per_px, scale_source = ASSUMED_SPAN_M / span_px, "assumed"

    def within(g, lo, hi):
        return lo <= g.length * m_per_px <= hi

    doors = [g for g in gaps if not g.window and within(g, *DOOR_GAP_M)]
    wins = [g for g in gaps if g.window and within(g, *WINDOW_GAP_M)]
    heal = [g for g in gaps if g.length * m_per_px < HEAL_GAP_M]
    walls = walls | paint(walls.shape, heal)
    door_m, win_m = paint(walls.shape, doors), paint(walls.shape, wins)

    if single:   # 단선은 최소 12cm 두께로
        tgt = max(t, 0.12 / m_per_px)
        if tgt > t + 1:
            walls = cv2.dilate(walls, _ellipse(tgt - t + 1)) & ~door_m
            t = tgt
    fp = footprint_of(walls | door_m | win_m, m_per_px, t)
    ent = find_entrances(doors, fp, m_per_px)
    return WallResult(walls, door_m, win_m, fp, mode, float(t), float(m_per_px), scale_source, notes, ent)


def debug_overlay(bgr: np.ndarray, r: WallResult) -> np.ndarray:
    out = (bgr.astype(np.float32) * 0.45 + 140).astype(np.uint8)
    out[r.footprint > 0] = (out[r.footprint > 0] * 0.8 + np.array([40, 40, 0]) * 0.2).astype(np.uint8)
    out[r.walls > 0] = (40, 60, 230)
    out[r.doors > 0] = (40, 200, 40)
    out[r.windows > 0] = (230, 160, 30)
    return out
