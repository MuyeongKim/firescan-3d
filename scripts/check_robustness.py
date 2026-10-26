"""현장에서 흔한 '깨끗하지 않은 도면' 변형으로 인식이 무너지지 않는지 점검한다(회귀 시험).

samples/ 의 합성 도면(apt_filled·apt_hatch·evac_photo·apt.dxf)에서 변형을 만들어 파이프라인에 넣고, 기대와 다르면 비정상 종료한다.
  python scripts/check_robustness.py            # 변형 파일은 임시 폴더에 만들고 끝나면 지운다
  python scripts/check_robustness.py --keep DIR # 변형 파일을 DIR 에 남긴다(눈으로 확인용)
기준 건물은 14.9 x 10.8m, 문 7개(1층 샘플 기준, 2층 샘플은 문 7개·13.6 x 9.9m). 합성 샘플 기준이며 실제 도면으로 검증된 수치가 아니다.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.pipeline import UnsupportedFile, build_floor, load_pages  # noqa: E402

S = ROOT / "samples"
W_M, D_M = 14.9, 10.8


def photo(img, ang=6, persp=0.08, shadow=0.5, noise=12, blur=1.2, scale=0.55, rng=None):
    rng = rng or np.random.default_rng(1)
    im = cv2.resize(img, None, fx=scale, fy=scale)
    h, w = im.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    d = w * persp
    dst = np.float32([[d, d * 0.6], [w - d * 0.3, 0], [w, h - d * 0.5], [0, h]])
    out = cv2.warpPerspective(im, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(200, 205, 210))
    out = cv2.warpAffine(out, cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1), (w, h), borderValue=(190, 195, 200))
    out = out * np.linspace(1, 1 - shadow, w)[None, :, None].astype(np.float32)
    out = np.clip(out + rng.normal(0, noise, out.shape), 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(out, (0, 0), blur)


def make_variants(out: Path) -> dict[str, tuple[Path, str]]:
    """이름 → (파일, 기대). 기대: 'ok'(정확), 'ok_loose'(사진류: 크기 오차 25% 허용), 'fail:<코드>'(올바르게 거절)"""
    base, cad = cv2.imread(str(S / "apt_filled.png")), cv2.imread(str(S / "apt_hatch.png"))
    g = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    v: dict[str, tuple[Path, str]] = {}

    def save(name, img, exp, q=None):
        p = out / name
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, q] if q else [])
        v[name] = (p, exp)

    rgba = np.dstack([base, np.where(g > 235, 0, 255).astype(np.uint8)])
    save("01_투명배경.png", rgba, "ok")
    bp = np.zeros_like(base); bp[:] = (120, 60, 10); bp[(255 - g) > 100] = (255, 255, 255)
    save("02_청사진_흰선.png", bp, "ok")                       # 색이 두 가지뿐인 이미지(1비트)도 포함
    dk = np.zeros_like(base); dk[cv2.cvtColor(cad, cv2.COLOR_BGR2GRAY) < 128] = (220, 220, 220)
    save("03_검은배경_CAD.png", dk, "ok")
    h, w = cad.shape[:2]
    sheet = np.full((h + 260, w + 260, 3), 255, np.uint8); sheet[110:110 + h, 130:130 + w] = cad
    cv2.rectangle(sheet, (20, 20), (w + 240, h + 240), (0, 0, 0), 6)
    cv2.rectangle(sheet, (w - 420, h + 130), (w + 235, h + 235), (0, 0, 0), 3)
    cv2.line(sheet, (w - 420, h + 180), (w + 235, h + 180), (0, 0, 0), 2)
    cv2.putText(sheet, "DRAWING TITLE  A-101", (w - 400, h + 168), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
    save("04_도면테두리_표제란.png", sheet, "ok")
    save("05_비스듬한사진.jpg", photo(base), "ok_loose", 35)
    save("07_매우작은_640px.jpg", cv2.resize(base, (640, 496)), "ok", 60)
    big = cv2.resize(base, (4000, 3100), interpolation=cv2.INTER_CUBIC)
    save("08_큰스캔_4000px.jpg", np.clip(big + np.random.default_rng(1).normal(0, 6, big.shape), 0, 255).astype(np.uint8), "ok", 85)
    evac = cv2.imread(str(S / "evac_photo.jpg"))
    save("09_피난안내도_어두움.jpg", np.clip(cv2.GaussianBlur(evac, (0, 0), 1.6).astype(np.float32) * 0.55, 0, 255).astype(np.uint8), "ok_loose", 40)
    dg = evac.copy(); eh, ew = dg.shape[:2]
    cv2.line(dg, (int(ew * 0.30), int(eh * 0.55)), (int(ew * 0.48), int(eh * 0.82)), (25, 25, 25), 11)
    save("19_사선벽_단선도면.png", dg, "ok_loose")                 # 단선 도면의 사선 검출(HoughLinesP): OpenCV 5 는 결과 모양이 달라 예외가 났던 곳
    # 벽을 검게가 아니라 연한 회색(238)으로만 칠한 도면(분양 평면도 방식, 테두리선 없음). 문 호·글자는 그대로 두고,
    # 방 안에 타일 격자와 건물 밖에 치수선을 더해 '벽이 아닌 선'이 벽으로 잡히지 않는지도 본다.
    # 테두리선이 있으면 기존 2중선 방식으로도 풀려서 이 기능의 회귀 시험이 되지 않는다
    wm = cv2.morphologyEx((g < 100).astype(np.uint8) * 255, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    tone = np.full_like(base, 255)
    tone[(g < 100) & (wm == 0)] = (20, 20, 20)                         # 문 호·글자 등 가는 획
    tone[wm > 0] = (238, 238, 238)
    for x in range(1200, 1560, 40):
        cv2.line(tone, (x, 700), (x, 1000), (150, 150, 150), 1)       # 타일 격자(가는 회색선)
    for y in range(700, 1000, 40):
        cv2.line(tone, (1200, y), (1560, y), (150, 150, 150), 1)
    cv2.line(tone, (60, 40), (base.shape[1] - 60, 40), (30, 30, 30), 1)  # 건물 바깥 치수선
    save("20_회색벽_테두리없음.png", tone, "ok_loose")
    # CAD 빗금(해치) 벽: 벽 안을 잉크 기준(150) 언저리의 중간 회색 사선으로 촘촘히 채우고 1px 테두리를 두른다. 가구·치수 같은 1px 선이 많아
    # 획 폭 분포를 지배하므로 filled·double 어느 쪽도 못 고르고 단선으로 빠져, 문 폭으로 구한 축척이 터무니없이 커졌던(77m) 경우
    yy, xx = np.mgrid[:g.shape[0], :g.shape[1]]
    rng = np.random.default_rng(5)
    hat = np.full_like(base, 255)
    hat[(g < 100) & (wm == 0)] = (20, 20, 20)
    fill = (wm > 0) & (((xx + yy) % 3) != 0)
    hat[fill] = rng.integers(125, 165, size=(int(fill.sum()), 1)).astype(np.uint8)
    hat[(wm > 0) & (cv2.erode(wm, np.ones((3, 3), np.uint8)) == 0)] = (15, 15, 15)
    for _ in range(60):
        x0, y0 = int(rng.integers(100, g.shape[1] - 300)), int(rng.integers(100, g.shape[0] - 200))
        cv2.rectangle(hat, (x0, y0), (x0 + int(rng.integers(40, 160)), y0 + int(rng.integers(20, 90))), (30, 30, 30), 1)
    save("21_빗금벽_CAD.png", hat, "ok_loose")
    col = base.copy(); col[g > 200] = (200, 230, 250)
    save("13_컬러방_도면.png", col, "ok")
    p2 = cv2.imread(str(S / "apt_2f.png"))
    hh = min(base.shape[0], p2.shape[0])
    save("a_한장에두도면.png", np.hstack([base[:hh], np.full((hh, 120, 3), 255, np.uint8), p2[:hh]]), "multi:2")
    gc = cv2.cvtColor(cad, cv2.COLOR_BGR2GRAY).astype(np.float32)
    faint = np.clip(255 - (255 - gc) * 0.35 + np.random.default_rng(3).normal(0, 4, gc.shape), 0, 255).astype(np.uint8)
    save("c_연한스캔.jpg", faint, "ok", 60)                       # 대비 35%
    leg = np.full((base.shape[0], 700, 3), 255, np.uint8)
    for k in range(12):
        cv2.rectangle(leg, (40, 60 + k * 100), (100, 110 + k * 100), [(0, 0, 255), (0, 160, 0), (255, 100, 0)][k % 3], -1)
        cv2.putText(leg, f"LEGEND {k}", (130, 100 + k * 100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    cv2.rectangle(leg, (10, 10), (690, base.shape[0] - 10), (0, 0, 0), 4)
    save("e_범례포함.png", np.hstack([base, leg]), "ok")            # 범례 표는 방이 하나뿐이라 제외된다
    corner = np.full((3000, 4000, 3), 255, np.uint8); corner[2300:2845, 3200:3900] = cv2.resize(base, (700, 545))
    save("f_구석에작게.png", corner, "ok")                          # 큰 이미지의 구석에 작게 있는 도면
    d = pymupdf.open(); c = d.new_page(width=842, height=595); c.insert_text((100, 200), "COVER PAGE", fontsize=40)
    pg = d.new_page(width=842, height=595); pg.insert_image(pymupdf.Rect(20, 20, 822, 575), filename=str(S / "apt_hatch.png"))
    d.save(str(out / "11_표지포함PDF.pdf")); v["11_표지포함PDF.pdf"] = (out / "11_표지포함PDF.pdf", "ok")
    shutil.copy(S / "apt.dxf", out / "15_도면.txt"); v["15_도면.txt"] = (out / "15_도면.txt", "ok")
    (out / "16_도면.dwg").write_bytes(b"AC1032" + bytes(range(256)) * 20); v["16_도면.dwg"] = (out / "16_도면.dwg", "fail:dwg")
    cv2.imwrite(str(out / "17_빈이미지.png"), np.full((800, 1000, 3), 255, np.uint8)); v["17_빈이미지.png"] = (out / "17_빈이미지.png", "fail:no_walls")
    t = np.full((800, 1000, 3), 255, np.uint8); cv2.putText(t, "Hello Floor Plan", (100, 400), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    cv2.imwrite(str(out / "18_글자만.png"), t); v["18_글자만.png"] = (out / "18_글자만.png", "fail:not_plan")
    return v


def run(path: Path):
    """main.py 의 업로드 처리와 같은 판단으로 한 파일을 돌린다. 반환: (층 info 목록, 실패 코드). 한 장에서 나뉜 조각은 방 2개 이상만 남긴다."""
    from app.main import fail_reason, not_a_plan
    try:
        pages = load_pages(path.read_bytes(), path.name, "")
    except UnsupportedFile as e:
        return [], fail_reason(e)
    last, built = "no_walls", []
    for p in pages:
        try:
            out = build_floor(p)
        except Exception as e:  # noqa: BLE001
            last = fail_reason(e)
            continue
        built.append((p, out.info))
    groups: dict = {}
    for i, (p, _) in enumerate(built):
        if p.group:
            groups.setdefault(p.group, []).append(i)
    skip: set[int] = set()
    for idxs in groups.values():
        good = [i for i in idxs if built[i][1].get("rooms", 0) >= 2] or [max(idxs, key=lambda i: built[i][1].get("rooms", 0))]
        skip |= set(idxs) - set(good)
    infos = []
    for i, (_, info) in enumerate(built):
        if i in skip:
            continue
        if not_a_plan(info):
            last = "not_plan"
            continue
        infos.append(info)
    return infos, last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", help="변형 파일을 남길 폴더")
    a = ap.parse_args()
    tmp = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="fs3d_var_"))
    tmp.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, (path, exp) in make_variants(tmp).items():
        infos, why = run(path)
        info = infos[0] if infos else None
        if exp.startswith("fail:"):
            ok = not infos and why == exp.split(":")[1]
            res = f"거절({why})"
        elif exp.startswith("multi:"):
            ok = len(infos) == int(exp.split(":")[1]) and all(i["doors"] == 7 for i in infos)
            res = f"{len(infos)}개 층 " + " | ".join(f"{i['label']} {i['width_m']}x{i['depth_m']}m 문{i['doors']}" for i in infos)
        else:
            tol = 0.25 if exp == "ok_loose" else 0.04
            ok = info is not None
            res = "실패(" + why + ")"
            if info:
                dw, dd = sorted((info["width_m"], info["depth_m"]), reverse=True), sorted((W_M, D_M), reverse=True)
                err = max(abs(dw[0] - dd[0]) / dd[0], abs(dw[1] - dd[1]) / dd[1])
                ok = err <= tol and (exp == "ok_loose" or info["doors"] == 7) and len(infos) == 1
                res = f"{info['width_m']}x{info['depth_m']}m 문{info['doors']} 방{info.get('rooms')} 오차 {err:.0%}"
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name:26s} {res}")
    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n전부 통과" if not bad else f"\n실패 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
