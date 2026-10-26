"""테스트용 평면도 샘플 생성기.

같은 아파트 평면(14m x 10m)을 여러 도면 스타일로 그려 파이프라인을 검증한다.
  apt_filled.png   - 벽체가 검게 채워진 일반 평면도 이미지
  apt_hatch.png    - 2중선 + 해치 벽체
  apt_cad.pdf      - 벡터 PDF, 2중선 벽체(채움 없음)
  evac_photo.jpg   - 단선 피난안내도를 휴대폰으로 찍은 사진(원근/조명/노이즈)
  apt.dxf          - 레이어가 나뉜 CAD 도면
  apt_2f.png       - 다층 테스트용 2층(좌우 반전 배치)

사용법: python scripts/make_samples.py [출력폴더]
"""
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "samples"
PPM = 100          # px per meter
MARGIN = 2.0       # m
W_M, H_M = 14.0, 10.0
EXT_T, INT_T = 0.25, 0.15

FONT_PATHS = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def font(size):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---------------------------------------------------------------- layout (m)
# 벽: (x1, y1, x2, y2, 두께)
WALLS = [
    (0, 0, W_M, 0, EXT_T), (W_M, 0, W_M, H_M, EXT_T),
    (W_M, H_M, 0, H_M, EXT_T), (0, H_M, 0, 0, EXT_T),
    (4.5, 0, 4.5, 10, INT_T), (9.5, 0, 9.5, 10, INT_T),
    (0, 5.5, 4.5, 5.5, INT_T), (2.5, 5.5, 2.5, 10, INT_T),
    (9.5, 6.0, 14, 6.0, INT_T), (4.5, 6.5, 7.0, 6.5, INT_T),
    (7.0, 6.5, 7.0, 10, INT_T),
]
# 문: (벽 인덱스, 벽 위 위치 m(시작점 기준), 폭, 열림방향 +1/-1)
DOORS = [
    (4, 3.5, 0.9, -1), (6, 1.25, 0.8, -1), (6, 3.5, 0.8, -1),
    (5, 3.0, 0.9, 1), (5, 8.0, 0.9, 1), (2, 14 - 8.2, 1.0, -1),
]
OPENINGS = [(9, 1.25, 1.2)]  # 문짝 없는 개구부
WINDOWS = [(0, 2.25, 1.8), (0, 7.0, 3.0), (0, 11.75, 1.8), (1, 8.0, 1.5), (3, 10 - 7.75, 0.6)]
COLUMNS = [(0, 0), (14, 0), (0, 10), (14, 10), (4.5, 0), (9.5, 0), (4.5, 10), (9.5, 10)]
ROOMS = [("침실1", 2.25, 2.75), ("거실", 7.0, 3.25), ("침실2", 11.75, 3.0), ("욕실", 1.25, 7.75),
         ("드레스룸", 3.5, 7.75), ("주방", 5.75, 8.25), ("현관", 8.25, 8.25), ("침실3", 11.75, 8.0)]


def wall_dir(i):
    x1, y1, x2, y2, t = WALLS[i]
    L = math.hypot(x2 - x1, y2 - y1)
    return (x1, y1), ((x2 - x1) / L, (y2 - y1) / L), L, t


def at(i, s):
    (x1, y1), (dx, dy), _, _ = wall_dir(i)
    return x1 + dx * s, y1 + dy * s


def px(x, y, ppm=PPM, margin=MARGIN):
    return (x + margin) * ppm, (y + margin) * ppm


def wall_mask(ppm=PPM, margin=MARGIN, mirror=False):
    """벽 실루엣(채움) 마스크. 개구부/창/문은 비움."""
    w, h = int((W_M + 2 * margin) * ppm), int((H_M + 2 * margin) * ppm)
    m = np.zeros((h, w), np.uint8)
    for x1, y1, x2, y2, t in WALLS:
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        ex = t / 2
        pts = [(x1 - ux * ex - uy * ex, y1 - uy * ex + ux * ex), (x2 + ux * ex - uy * ex, y2 + uy * ex + ux * ex),
               (x2 + ux * ex + uy * ex, y2 + uy * ex - ux * ex), (x1 - ux * ex + uy * ex, y1 - uy * ex - ux * ex)]
        cv2.fillPoly(m, [np.array([px(*p, ppm, margin) for p in pts], np.int32)], 255)
    for x, y in COLUMNS:
        a, b = px(x - 0.3, y - 0.3, ppm, margin), px(x + 0.3, y + 0.3, ppm, margin)
        cv2.rectangle(m, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), 255, -1)
    for i, s, wd, *_ in DOORS + [(o[0], o[1], o[2]) for o in OPENINGS] + WINDOWS:
        _, (dx, dy), _, t = wall_dir(i)
        cx, cy = at(i, s)
        hw, ht = wd / 2, t / 2 + 0.02
        pts = [(cx - dx * hw - dy * ht, cy - dy * hw + dx * ht), (cx + dx * hw - dy * ht, cy + dy * hw + dx * ht),
               (cx + dx * hw + dy * ht, cy + dy * hw - dx * ht), (cx - dx * hw + dy * ht, cy - dy * hw - dx * ht)]
        cv2.fillPoly(m, [np.array([px(*p, ppm, margin) for p in pts], np.int32)], 0)
    if mirror:
        m = m[:, ::-1].copy()
    return m


def draw_details(d: ImageDraw.ImageDraw, ppm=PPM, margin=MARGIN, lw=2, color=(0, 0, 0), labels=True, dims=True,
                 furniture=True, windows=True):
    P = lambda x, y: px(x, y, ppm, margin)
    # 문짝 + 열림 호
    for i, s, wd, sw in DOORS:
        _, (dx, dy), _, t = wall_dir(i)
        hx, hy = at(i, s - wd / 2)
        nx, ny = -dy * sw, dx * sw
        tip = (hx + nx * wd, hy + ny * wd)
        d.line([P(hx, hy), P(*tip)], fill=color, width=lw)
        a0 = math.degrees(math.atan2(ny, nx))
        a1 = math.degrees(math.atan2(dy, dx))
        lo, hi = sorted([a0, a1])
        if hi - lo > 180:
            lo, hi = hi, lo + 360
        c = P(hx, hy)
        r = wd * ppm
        d.arc([c[0] - r, c[1] - r, c[0] + r, c[1] + r], lo, hi, fill=color, width=max(1, lw - 1))
    if windows:
        for i, s, wd in WINDOWS:
            _, (dx, dy), _, t = wall_dir(i)
            for off in (-t / 2, 0, t / 2):
                a = at(i, s - wd / 2)
                b = at(i, s + wd / 2)
                d.line([P(a[0] - dy * off, a[1] + dx * off), P(b[0] - dy * off, b[1] + dx * off)], fill=color,
                       width=max(1, lw - 1))
    if furniture:
        d.rectangle([P(0.5, 0.5), P(2.6, 2.4)], outline=color, width=max(1, lw - 1))      # 침단
        d.rectangle([P(0.5, 0.5), P(2.6, 0.9)], outline=color, width=max(1, lw - 1))
        d.rectangle([P(5.2, 4.6), P(8.8, 5.5)], outline=color, width=max(1, lw - 1))      # 소파
        c = P(5.75, 7.6)
        d.ellipse([c[0] - 0.5 * ppm, c[1] - 0.5 * ppm, c[0] + 0.5 * ppm, c[1] + 0.5 * ppm], outline=color,
                  width=max(1, lw - 1))
        d.rectangle([P(0.3, 8.3), P(2.2, 9.7)], outline=color, width=max(1, lw - 1))      # 욕조
        c = P(1.8, 6.2)
        d.ellipse([c[0] - 0.2 * ppm, c[1] - 0.3 * ppm, c[0] + 0.2 * ppm, c[1] + 0.3 * ppm], outline=color, width=1)
    if labels:
        f = font(int(0.38 * ppm))
        for name, x, y in ROOMS:
            d.text(P(x, y), name, fill=color, font=f, anchor="mm")
    if dims:
        f = font(int(0.22 * ppm))
        ys = -1.1
        xs = [0, 4.5, 9.5, 14]
        d.line([P(0, ys), P(14, ys)], fill=color, width=1)
        for x in xs:
            d.line([P(x, ys - 0.15), P(x, ys + 0.15)], fill=color, width=1)
        for a, b in zip(xs, xs[1:]):
            d.text(P((a + b) / 2, ys - 0.25), f"{int((b - a) * 1000)}", fill=color, font=f, anchor="mm")
        xl = -1.1
        d.line([P(xl, 0), P(xl, 10)], fill=color, width=1)
        for y in (0, 5.5, 10):
            d.line([P(xl - 0.15, y), P(xl + 0.15, y)], fill=color, width=1)


def make_filled(mirror=False):
    m = wall_mask(mirror=mirror)
    img = Image.fromarray(np.where(m > 0, 25, 255).astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(img)
    if not mirror:
        draw_details(d)
    else:
        f = font(int(0.38 * PPM))
        for name, x, y in ROOMS:
            p = px(W_M - x, y)
            d.text(p, name.replace("침실", "사무실"), fill=(0, 0, 0), font=f, anchor="mm")
    return img


def make_hatch():
    m = wall_mask()
    edges = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    hatch = np.zeros_like(m)
    for k in range(-m.shape[0], m.shape[1], 9):
        cv2.line(hatch, (k, 0), (k + m.shape[0], m.shape[0]), 255, 1)
    ink = (edges > 0) | ((hatch > 0) & (m > 0))
    img = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).convert("RGB")
    draw_details(ImageDraw.Draw(img), lw=2)
    return img


def wall_outlines(ppm, margin):
    m = wall_mask(ppm, margin)
    cs, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    return [c[:, 0, :].astype(float) / ppm - margin for c in cs]


def make_pdf(path):
    import pymupdf as fitz
    scale = 72 / 0.0254 / 100  # 1:100 축척, pt per m
    doc = fitz.open()
    page = doc.new_page(width=(W_M + 4) * scale, height=(H_M + 4) * scale)
    T = lambda x, y: fitz.Point((x + 2) * scale, (y + 2) * scale)
    for c in wall_outlines(200, 2):
        pts = [T(*p) for p in c] + [T(*c[0])]
        page.draw_polyline(pts, color=(0, 0, 0), width=0.6)
    # 문/가구/치수는 래스터 함수 재사용 대신 간단히 벡터로
    for i, s, wd, sw in DOORS:
        _, (dx, dy), _, t = wall_dir(i)
        hx, hy = at(i, s - wd / 2)
        nx, ny = -dy * sw, dx * sw
        page.draw_line(T(hx, hy), T(hx + nx * wd, hy + ny * wd), width=0.4)
        pts = []
        for k in range(17):
            a = k / 16 * math.pi / 2
            vx = dx * math.cos(a) + nx * math.sin(a)
            vy = dy * math.cos(a) + ny * math.sin(a)
            pts.append(T(hx + vx * wd, hy + vy * wd))
        page.draw_polyline(pts, width=0.25)
    for i, s, wd in WINDOWS:
        _, (dx, dy), _, t = wall_dir(i)
        for off in (-t / 2, 0, t / 2):
            a, b = at(i, s - wd / 2), at(i, s + wd / 2)
            page.draw_line(T(a[0] - dy * off, a[1] + dx * off), T(b[0] - dy * off, b[1] + dx * off), width=0.25)
    page.draw_rect(fitz.Rect(T(0.5, 0.5), T(2.6, 2.4)), width=0.3)
    page.draw_rect(fitz.Rect(T(5.2, 4.6), T(8.8, 5.5)), width=0.3)
    page.draw_rect(fitz.Rect(T(0.3, 8.3), T(2.2, 9.7)), width=0.3)
    for name, x, y in ROOMS:
        page.insert_text(T(x - 0.4, y), name.encode("ascii", "ignore").decode() or "ROOM", fontsize=8)
    page.draw_line(T(0, -1.1), T(14, -1.1), width=0.25)
    doc.save(path)


def make_evac_photo():
    m = wall_mask()
    h, w = m.shape
    img = Image.new("RGB", (w, h), (250, 248, 240))
    d = ImageDraw.Draw(img)
    fills = [(0.0, 0.0, 4.5, 5.5, (255, 236, 200)), (4.5, 0, 9.5, 6.5, (210, 235, 255)),
             (9.5, 0, 14, 6, (255, 236, 200)), (0, 5.5, 4.5, 10, (225, 245, 225)),
             (4.5, 6.5, 14, 10, (240, 225, 250))]
    for x1, y1, x2, y2, c in fills:
        d.rectangle([px(x1, y1), px(x2, y2)], fill=c)
    # 단선 벽: 마스크의 중심선만 남기는 대신 벽 중심선을 직접 그림(개구부 제외)
    line = np.zeros_like(m)
    for x1, y1, x2, y2, t in WALLS:
        cv2.line(line, tuple(int(v) for v in px(x1, y1)), tuple(int(v) for v in px(x2, y2)), 255, 5 if t > 0.2 else 3)
    line[(cv2.dilate(m, np.ones((5, 5), np.uint8)) == 0)] = 0
    arr = np.array(img)
    arr[line > 0] = (30, 30, 30)
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    draw_details(d, lw=2, color=(40, 40, 40), dims=False, furniture=False, windows=False)
    # 피난 화살표(빨강), 비상구(초록), 현재위치
    for a, b in [((7, 3), (8.2, 8.5)), ((11.75, 8), (9.8, 8.0)), ((2.25, 3), (4.0, 3.5))]:
        d.line([px(*a), px(*b)], fill=(220, 30, 30), width=8)
        bx, by = px(*b)
        d.ellipse([bx - 12, by - 12, bx + 12, by + 12], fill=(220, 30, 30))
    ex, ey = px(8.2, 10.4)
    d.rectangle([ex - 50, ey - 20, ex + 50, ey + 20], fill=(0, 150, 70))
    d.text((ex, ey), "비상구", fill="white", font=font(26), anchor="mm")
    d.text(px(7, -1.2), "피 난 안 내 도", fill=(200, 20, 20), font=font(60), anchor="mm")
    arr = np.array(img).astype(np.float32)
    # 사진 효과: 원근, 조명 그라디언트, 노이즈, 흐림, JPEG
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[60, 40], [w - 20, 0], [w - 70, h - 30], [10, h - 60]])
    M = cv2.getPerspectiveTransform(src, dst)
    arr = cv2.warpPerspective(arr, M, (w, h), borderValue=(180, 175, 165))
    yy, xx = np.mgrid[0:h, 0:w]
    light = 0.62 + 0.38 * (1 - ((xx - w * 0.8) ** 2 + (yy - h * 0.2) ** 2) ** 0.5 / (w * 1.1))
    arr = arr * light[..., None]
    arr += np.random.default_rng(1).normal(0, 6, arr.shape)
    arr = cv2.GaussianBlur(arr, (3, 3), 0.8)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def make_dxf(path):
    import ezdxf
    doc = ezdxf.new(units=4)  # mm
    for name in ("A-WALL", "A-DOOR", "A-FURN", "A-ANNO", "A-DIMS", "A-COLS"):
        doc.layers.add(name)
    msp = doc.modelspace()
    mm = lambda x, y: (x * 1000, -y * 1000)
    for c in wall_outlines(200, 2):
        msp.add_lwpolyline([mm(*p) for p in c], close=True, dxfattribs={"layer": "A-WALL"})
    for x, y in COLUMNS:
        h = msp.add_hatch(dxfattribs={"layer": "A-COLS"})
        h.paths.add_polyline_path([mm(x - .3, y - .3), mm(x + .3, y - .3), mm(x + .3, y + .3), mm(x - .3, y + .3)])
    for i, s, wd, sw in DOORS:
        _, (dx, dy), _, t = wall_dir(i)
        hx, hy = at(i, s - wd / 2)
        nx, ny = -dy * sw, dx * sw
        msp.add_line(mm(hx, hy), mm(hx + nx * wd, hy + ny * wd), dxfattribs={"layer": "A-DOOR"})
        a0 = math.degrees(math.atan2(-dy, dx))
        a1 = math.degrees(math.atan2(-ny, nx))
        lo, hi = (a0, a1) if (a1 - a0) % 360 < 180 else (a1, a0)
        msp.add_arc(mm(hx, hy), wd * 1000, lo, hi, dxfattribs={"layer": "A-DOOR"})
    msp.add_lwpolyline([mm(0.5, 0.5), mm(2.6, 0.5), mm(2.6, 2.4), mm(0.5, 2.4)], close=True,
                       dxfattribs={"layer": "A-FURN"})
    for name, x, y in ROOMS:
        msp.add_text(name, height=300, dxfattribs={"layer": "A-ANNO"}).set_placement(mm(x, y))
    msp.add_line(mm(0, -1.1), mm(14, -1.1), dxfattribs={"layer": "A-DIMS"})
    doc.saveas(path)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_filled().save(OUT / "apt_filled.png")
    make_filled(mirror=True).save(OUT / "apt_2f.png")
    make_hatch().save(OUT / "apt_hatch.png")
    make_evac_photo().save(OUT / "evac_photo.jpg", quality=72)
    make_pdf(str(OUT / "apt_cad.pdf"))
    make_dxf(str(OUT / "apt.dxf"))
    small = make_filled().resize((900, 700), Image.LANCZOS)
    small.save(OUT / "apt_small.jpg", quality=80)
    print("samples ->", OUT)
