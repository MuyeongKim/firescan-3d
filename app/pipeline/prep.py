"""래스터 도면(사진·스캔·캡처·PDF 렌더) 전처리: 어두운 배경 반전, 도면 테두리 제거, 기울기·원근 보정.

현장에서 올라오는 도면은 깨끗한 흰 종이 위의 검은 선이 아닌 경우가 많다. 인식(`walls.extract`)은 흰 바탕의 어두운 선을 가정하므로,
그 가정에 맞게 입력을 먼저 바로잡는다. 이미 반듯한 도면은 건드리지 않는다(각 단계는 확실할 때만 적용).
"""
from __future__ import annotations

import cv2
import numpy as np

from .walls import binarize, remove_clutter


def invert_if_dark(img: np.ndarray) -> tuple[np.ndarray, str | None]:
    """청사진(파란 바탕에 흰 선)·CAD 화면 캡처(검은 바탕)처럼 바탕이 어두우면 명암을 뒤집는다."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if np.median(g) < 100 and (g < 90).mean() > 0.55:
        return cv2.cvtColor(255 - g, cv2.COLOR_GRAY2BGR), "어두운 배경 도면이라 명암을 뒤집어 읽었습니다"
    return img, None


def _frame_lines(ink: np.ndarray):
    """가장자리 15% 안에서 전체 길이의 70% 이상을 잇는 가로·세로 선(도면 테두리 후보)을 찾는다."""
    h, w = ink.shape
    rows = ink.sum(axis=1) / 255.0
    cols = ink.sum(axis=0) / 255.0
    mh, mw = int(h * 0.15), int(w * 0.15)

    def pick(profile, lo, hi, need):
        idx = [i for i in range(lo, hi) if profile[i] > need]
        return idx

    top = pick(rows, 0, mh, 0.7 * w)
    bot = pick(rows, h - mh, h, 0.7 * w)
    left = pick(cols, 0, mw, 0.7 * h)
    right = pick(cols, w - mw, w, 0.7 * h)
    return top, bot, left, right


def crop_frame(img: np.ndarray) -> tuple[np.ndarray, str | None]:
    """도면 시트의 테두리(표제란 포함 가능)를 잘라 낸다.

    건물의 외벽을 테두리로 오인하면 안 되므로, 네 변이 모두 있고 **테두리 안쪽에 여백**(내용이 테두리에 붙지 않음)이 있을 때만 자른다.
    """
    h, w = img.shape[:2]
    ink = binarize(img)
    top, bot, left, right = _frame_lines(ink)
    if not (top and bot and left and right):
        return img, None
    y0, y1 = max(top) + 1, min(bot)       # 가장 안쪽 선 바로 안
    x0, x1 = max(left) + 1, min(right)
    if y1 - y0 < 0.5 * h or x1 - x0 < 0.5 * w:
        return img, None
    inner = ink[y0:y1, x0:x1]
    # 테두리 선 자체와 이어진 얇은 가장자리를 빼고 내용의 범위를 본다
    pad = max(6, int(0.01 * min(h, w)))
    core = inner[pad:-pad, pad:-pad]
    ys, xs = np.nonzero(core)
    if len(xs) == 0:
        return img, None
    ih, iw = core.shape
    margins = [ys.min() / ih, (ih - 1 - ys.max()) / ih, xs.min() / iw, (iw - 1 - xs.max()) / iw]
    # 내용이 두 변 이상에서 3% 넘게 떨어져 있으면 시트 테두리, 아니면 건물 외벽으로 보고 자르지 않는다
    if sum(m > 0.03 for m in margins) < 2:
        return img, None
    return img[y0 + pad:y1 - pad, x0 + pad:x1 - pad].copy(), "도면 테두리를 잘라냈습니다"


def _order(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.float32([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]])   # 좌상, 우상, 우하, 좌하


def _angle_dev(q: np.ndarray) -> float:
    dev = 0.0
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        dev = max(dev, abs(np.degrees(np.arccos(np.clip(cos, -1, 1))) - 90))
    return dev


def rectify(img: np.ndarray) -> tuple[np.ndarray, str | None]:
    """사진의 기울기·원근을 바로잡는다. 건물의 바깥 윤곽(볼록 껍질)이 사각형에 가까울 때만 그 네 모서리를 직사각형으로 편다."""
    h, w = img.shape[:2]
    ink = remove_clutter(binarize(img))
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = [i for i in range(1, n) if st[i, 4] >= 0.002 * h * w]
    if not keep:
        return img, None
    pts = cv2.findNonZero(np.isin(lab, keep).astype(np.uint8))
    hull = cv2.convexHull(pts)
    if cv2.contourArea(hull) < 0.2 * h * w:
        return img, None
    quad = None
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.05, 0.07):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            quad = ap.reshape(4, 2).astype(np.float32)
            break
    if quad is not None and cv2.contourArea(hull) / max(cv2.contourArea(quad), 1.0) > 1.12:
        quad = None   # 껍질이 사각형보다 12% 넘게 크면(L자·원형 건물 등) 원근 보정을 하지 않는다
    if quad is None:
        box = cv2.minAreaRect(hull)   # 사각형이 아니어도 기울기만은 바로잡을 수 있다
        ang = box[2] if box[2] <= 45 else box[2] - 90
        if abs(ang) < 1.5 or abs(ang) > 20:
            return img, None
        M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        cos, sin = abs(M[0, 0]), abs(M[0, 1])
        nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
        M[0, 2] += nw / 2 - w / 2
        M[1, 2] += nh / 2 - h / 2
        out = cv2.warpAffine(img, M, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
        return out, f"기울어진 도면을 {abs(ang):.1f}° 바로잡았습니다"
    q = _order(quad)
    dev = _angle_dev(q)
    top, bot = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])
    lef, rig = np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])
    rot = abs(np.degrees(np.arctan2(q[1][1] - q[0][1], q[1][0] - q[0][0])))
    if dev < 2.5 and rot < 1.5 and max(top, bot) / max(min(top, bot), 1) < 1.03 and max(lef, rig) / max(min(lef, rig), 1) < 1.03:
        return img, None          # 이미 반듯하다
    if dev > 25:
        return img, None          # 사각형으로 보기 어려운 윤곽
    W, H = int(max(top, bot)), int(max(lef, rig))   # 가까운 쪽 변 길이에 맞춘다
    # 바깥 벽이 잘리지 않도록 모서리를 중심에서 바깥쪽으로 조금 넓힌다
    c = q.mean(axis=0)
    q = c + (q - c) * 1.03
    dst = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    pad = int(0.02 * max(W, H))
    dst = dst + pad
    M = cv2.getPerspectiveTransform(q, dst)
    out = cv2.warpPerspective(img, M, (W + 2 * pad, H + 2 * pad), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    return out, f"비스듬하게 찍힌 도면의 원근을 바로잡았습니다(모서리 어긋남 {dev:.0f}°)"


def _boxes(img: np.ndarray):
    """도면 후보 덩어리(벽처럼 큰 연결 성분)의 상자 목록. 글자·치수선·작은 기호는 빠진다. 반환: (이미지 크기 h, w, 상자들[x0,y0,x1,y1])."""
    h, w = img.shape[:2]
    long = max(h, w)
    ink = remove_clutter(binarize(img))
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = st[i]
        if max(bw, bh) >= 0.06 * long and area >= 0.0003 * h * w:
            boxes.append([x, y, x + bw, y + bh])
    return h, w, boxes


def _merge(boxes: list[list[int]], gap: int) -> list[list[int]]:
    boxes = [b[:] for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    boxes.pop(j)
                    changed = True
                    break
            if changed:
                break
    return boxes


def crop_to_content(img: np.ndarray) -> tuple[np.ndarray, str | None]:
    """큰 이미지의 한쪽 구석에 도면이 작게 있으면 내용 영역으로 잘라 해상도를 살린다(이후 축소할 때 도면이 뭉개지지 않게)."""
    h, w, boxes = _boxes(img)
    if not boxes:
        return img, None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    if (x1 - x0) * (y1 - y0) >= 0.4 * h * w:
        return img, None
    m = int(0.04 * max(x1 - x0, y1 - y0))
    out = img[max(0, y0 - m):min(h, y1 + m), max(0, x0 - m):min(w, x1 + m)]
    return out.copy(), "도면이 이미지의 일부만 차지해 그 부분만 잘라 읽었습니다"


def _gaps(profile: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """내용이 없는 연속 구간(양 끝에 닿지 않는 것) 중 min_len 이상인 것 [(시작, 끝)]."""
    out, start = [], None
    for i, occupied in enumerate(profile):
        if not occupied and start is None:
            start = i
        elif occupied and start is not None:
            if start > 0 and i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def split_plans(img: np.ndarray) -> list[np.ndarray]:
    """한 장에 도면이 여러 개(예: 1층·2층을 나란히 그린 시트)이면 따로 떼어 읽는 순서(위→아래, 왼→오른)로 돌려준다.

    벽은 문 자리에서 끊기므로 '떨어진 덩어리'로는 판단하지 않는다. 도면 전체 높이(너비)를 가로지르는 **빈 띠**(긴 변의 4% 이상)가 있을 때만
    그 띠에서 자른다. 하나이거나 판단이 어려우면 빈 목록.
    """
    h, w = img.shape[:2]
    long = max(h, w)
    ink = remove_clutter(binarize(img)) > 0
    gmin = max(int(0.04 * long), 30)

    def trim(box):
        x0, y0, x1, y1 = box
        ys, xs = np.nonzero(ink[y0:y1, x0:x1])
        if len(xs) == 0:
            return None
        return (x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1)

    def cut(box, depth=0):
        box = trim(box)
        if box is None:
            return []
        x0, y0, x1, y1 = box
        if depth < 4:
            for axis in (1, 0):                       # 먼저 열(좌우로 나란히), 다음 행(위아래로 쌓임)
                sub = ink[y0:y1, x0:x1]
                gaps = _gaps(sub.any(axis=0 if axis == 1 else 1), gmin)
                if gaps:
                    g0, g1 = max(gaps, key=lambda g: g[1] - g[0])
                    if axis == 1:
                        return cut((x0, y0, x0 + g0, y1), depth + 1) + cut((x0 + g1, y0, x1, y1), depth + 1)
                    return cut((x0, y0, x1, y0 + g0), depth + 1) + cut((x0, y0 + g1, x1, y1), depth + 1)
        return [box]

    boxes = [b for b in cut((0, 0, w, h))
             if min(b[2] - b[0], b[3] - b[1]) >= 0.12 * long and (b[2] - b[0]) * (b[3] - b[1]) >= 0.04 * h * w]
    if not 2 <= len(boxes) <= 6:
        return []
    row = 0.25 * long
    boxes.sort(key=lambda b: (round(b[1] / row), b[0]))
    m = int(0.03 * long)
    return [img[max(0, b[1] - m):min(h, b[3] + m), max(0, b[0] - m):min(w, b[2] + m)].copy() for b in boxes]


def normalize(img: np.ndarray) -> tuple[np.ndarray, list[str], np.ndarray | None, str | None]:
    """어두운 배경 반전 → 도면 테두리 제거 → 기울기·원근 보정.

    (처리된 이미지, 적용한 처리 설명, 보정 전 이미지(보정을 적용한 때만), 보정 설명)을 돌려준다.
    기울기·원근 보정은 어두운 사진 등에서 오히려 인식을 나쁘게 할 수 있어, 호출한 쪽이 보정 전후를 비교해 고를 수 있게 둘 다 돌려준다.
    """
    notes: list[str] = []
    alt: np.ndarray | None = None
    rect_note: str | None = None
    for step in (invert_if_dark, crop_frame, rectify):
        before = img
        try:
            img, note = step(img)
        except Exception:  # noqa: BLE001 - 전처리 실패가 업로드를 막으면 안 된다(원본 그대로 진행)
            img = before
            continue
        if note:
            notes.append("전처리: " + note)
            if step is rectify:
                alt, rect_note = before, notes[-1]
    return img, notes, alt, rect_note


def normalize_many(img: np.ndarray) -> list[tuple[np.ndarray, list[str], np.ndarray | None, str | None]]:
    """한 장에 도면이 여러 개면 나눠서 각각 `normalize` 한다. 나뉘지 않으면 원소 하나."""
    parts = split_plans(img)
    if not parts:
        return [normalize(img)]
    out = []
    for k, part in enumerate(parts, 1):
        res = normalize(part)
        out.append((res[0], [f"전처리: 한 장에 든 도면 {len(parts)}개 중 {k}번째로 나눠 읽었습니다", *res[1]], res[2], res[3]))
    return out
