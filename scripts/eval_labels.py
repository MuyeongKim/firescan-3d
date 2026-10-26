"""AI허브 '건축 도면 데이터'(구조 라벨)로 벽·문·창 인식을 정답과 비교한다(채점).

  python scripts/eval_labels.py --images datasets/aihub/Sample/01.원천데이터/STR --labels datasets/aihub/Sample/02.라벨링데이터/STR
  python scripts/eval_labels.py --images <STR 이미지 폴더> --labels <STR 라벨 폴더> --limit 50 --viz /tmp/viz --csv /tmp/out.csv

AI허브 이미지는 A3 용지 한 장에 세대 평면도가 여러 개 들어 있다. 여기서는 정답 벽 폴리곤을 이어 붙여 평면도 하나씩(여백 포함) 잘라
인식 핵심(walls.extract)에 넣고 정답과 비교한다 → '평면도 하나를 잘라 올렸을 때'의 정확도다. 용지째 올렸을 때 평면도를 나누는
전처리(prep)는 별도로 봐야 한다(eval_dataset.py).
채점은 처리 해상도 기준이고, 벽 두께의 절반을 허용 오차로 둔다(축척과 무관).
  벽   : 정밀도(예측 벽 중 정답 근처 비율)·재현율(정답 벽 중 예측 근처 비율)·F1
  문·창: 정답 객체마다 예측과 겹치면 찾은 것(재현율), 예측 덩어리마다 정답과 겹치면 맞은 것(정밀도).
         '개구부 재현율'은 문을 창으로(또는 반대로) 분류해도 틈을 찾았으면 인정한다
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEYS = {"구조_벽체": "wall", "구조_출입문": "door", "구조_창호": "window"}
COLORS = {"wall": (0, 0, 255), "door": (0, 200, 0), "window": (255, 120, 0)}      # BGR


def load_gt(path: Path):
    d = json.loads(path.read_text("utf-8"))
    names = {c["id"]: c["name"] for c in d["categories"]}
    polys: dict[str, list[np.ndarray]] = {"wall": [], "door": [], "window": []}
    for a in d["annotations"]:
        k = KEYS.get(names.get(a["category_id"], ""))
        seg = a.get("segmentation")
        if not k or not isinstance(seg, list):
            continue
        for s in seg:
            if isinstance(s, list) and len(s) >= 6:
                polys[k].append(np.asarray(s, np.float32).reshape(-1, 2))
    im = d["images"][0]
    return polys, int(im["width"]), int(im["height"])


def plans_of(polys, W: int, H: int) -> list[tuple[float, float, float, float]]:
    """정답 벽·문·창 폴리곤을 이어 붙여 평면도(연결된 덩어리)마다 (x0, y0, x1, y1) 를 돌려준다.
    벽 폴리곤끼리는 문 자리에서 끊겨 있으므로 문·창 폴리곤을 함께 채우고 간격은 용지 긴 변의 3% 까지 잇는다."""
    s = 0.125
    m = np.zeros((round(H * s), round(W * s)), np.uint8)
    for p in polys["wall"] + polys["door"] + polys["window"]:
        cv2.fillPoly(m, [np.round(p * s).astype(np.int32)], 255)
    k = max(3, int(0.03 * max(W, H) * s)) | 1
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 0.0015 * m.size or max(w, h) < 0.08 * max(m.shape):       # 글자·기호 같은 작은 조각 제외
            continue
        out.append((x / s, y / s, (x + w) / s, (y + h) / s))
    return sorted(out)


def raster(polys, shape, off, f) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.round((p - off) * f).astype(np.int32)], 255)
    return m


def ellipse(r: int):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def prf(pred: np.ndarray, gt: np.ndarray, tol: int):
    k = ellipse(tol)
    p, g = pred > 0, gt > 0
    prec = (p & (cv2.dilate(gt, k) > 0)).sum() / max(p.sum(), 1)
    rec = (g & (cv2.dilate(pred, k) > 0)).sum() / max(g.sum(), 1)
    return float(prec), float(rec), float(2 * prec * rec / max(prec + rec, 1e-9))


def hits(gt_polys, pred: np.ndarray, off, f, tol: int) -> int:
    """정답 객체 중 예측과 겹치는 개수."""
    pd = cv2.dilate(pred, ellipse(tol))
    n = 0
    for p in gt_polys:
        m = np.zeros(pred.shape, np.uint8)
        cv2.fillPoly(m, [np.round((p - off) * f).astype(np.int32)], 255)
        n += bool((m & pd).any())
    return n


def comps_ok(pred: np.ndarray, gt_mask: np.ndarray, tol: int) -> tuple[int, int]:
    """예측 덩어리 중 정답과 겹치는 개수와 전체 개수."""
    n, lab = cv2.connectedComponents((pred > 0).astype(np.uint8), connectivity=8)
    gd = cv2.dilate(gt_mask, ellipse(tol)) > 0
    ok = sum(1 for i in range(1, n) if (gd & (lab == i)).any())
    return ok, n - 1


def overlay(img, masks: dict[str, np.ndarray]) -> np.ndarray:
    out = img.copy()
    for k in ("wall", "window", "door"):
        if k in masks:
            out[masks[k] > 0] = COLORS[k]
    return cv2.addWeighted(out, 0.55, img, 0.45, 0)


def score_image(job: tuple[str, str, str | None, str]) -> list[dict]:
    from app.pipeline import walls as W
    from app.pipeline.loaders import _fit
    from app.pipeline.walls import extract

    img_path, lab_path, viz, disable = job
    # 기능을 하나씩 꺼 보는 비교(ablation): --disable hatch,tone
    if "hatch" in disable.split(","):
        W.hatch_fill_walls = lambda *_a, **_k: None
    if "tone" in disable.split(","):
        W.tone_fill_walls = lambda *_a, **_k: None
    stem = Path(img_path).stem
    polys, W, H = load_gt(Path(lab_path))
    im = cv2.imread(img_path, cv2.IMREAD_COLOR)
    rows: list[dict] = []
    for pi, (x0, y0, x1, y1) in enumerate(plans_of(polys, W, H)):
        mg = 0.08 * max(x1 - x0, y1 - y0)
        cx0, cy0 = max(0, int(x0 - mg)), max(0, int(y0 - mg))
        cx1, cy1 = min(W, int(x1 + mg)), min(H, int(y1 + mg))
        crop = im[cy0:cy1, cx0:cx1]
        fit = _fit(crop)
        f = fit.shape[1] / crop.shape[1]
        off = np.array([cx0, cy0], np.float32)
        shp = fit.shape[:2]
        gt = {k: raster(v, shp, off, f) for k, v in polys.items()}      # 잘라낸 그림에 보이는 정답은 모두 그린다
        box = (cx0, cy0, cx1, cy1)
        sel = {k: [p for p in v if box[0] <= p[:, 0].mean() < box[2] and box[1] <= p[:, 1].mean() < box[3]] for k, v in polys.items()}
        row = {"image": stem, "plan": pi, "gt_walls": len(sel["wall"]), "gt_doors": len(sel["door"]), "gt_windows": len(sel["window"])}
        t0 = time.perf_counter()
        try:
            r = extract(fit)
        except ValueError:
            row.update(status="fail:no_walls", f1=0.0, prec=0.0, rec=0.0)
            rows.append(row)
            continue
        except Exception as e:  # noqa: BLE001
            row.update(status=f"error:{type(e).__name__}", f1=0.0, prec=0.0, rec=0.0)
            rows.append(row)
            continue
        row["ms"] = round((time.perf_counter() - t0) * 1000)
        gtw = gt["wall"] > 0
        dt = cv2.distanceTransform(gt["wall"], cv2.DIST_L2, 3)
        thick = 2.0 * float(np.median(dt[gtw])) if gtw.any() else 4.0
        tol = max(2, int(round(0.5 * thick)))
        prec, rec, f1 = prf(r.walls, gt["wall"], tol)
        pdoor, pwin = r.doors, r.windows
        popen = cv2.bitwise_or(pdoor, pwin)
        dh = hits(sel["door"], pdoor, off, f, tol)             # 문·창 개수는 이 평면도 영역 안의 정답만 센다
        wh = hits(sel["window"], pwin, off, f, tol)
        doh = hits(sel["door"], popen, off, f, tol)
        woh = hits(sel["window"], popen, off, f, tol)
        dp = comps_ok(pdoor, gt["door"], tol)
        wp = comps_ok(pwin, gt["window"], tol)
        row.update(status="ok", f1=f1, prec=prec, rec=rec, tol=tol, thick_gt=round(thick, 1), mode=r.mode, scale=r.scale_source,
                   rooms=r.rooms, door_hit=dh, door_open_hit=doh, win_hit=wh, win_open_hit=woh,
                   door_pred_ok=dp[0], door_pred=dp[1], win_pred_ok=wp[0], win_pred=wp[1])
        rows.append(row)
        if viz:
            left = overlay(fit, {k: gt[k] for k in ("wall", "window", "door")})
            right = overlay(fit, {"wall": r.walls, "window": pwin, "door": pdoor})
            side = np.hstack([left, np.full((shp[0], 6, 3), 255, np.uint8), right])
            sc = min(1.0, 1800 / side.shape[1])
            cv2.imwrite(str(Path(viz) / f"{stem}_p{pi}_f1_{f1:.2f}.jpg"), cv2.resize(side, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA),
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=min(2, os.cpu_count() or 1))
    ap.add_argument("--viz", type=Path, help="정답|예측 나란히 그린 이미지를 저장할 폴더")
    ap.add_argument("--csv", type=Path)
    ap.add_argument("--worst", type=int, default=8)
    ap.add_argument("--disable", default="", help="끌 기능(쉼표): hatch=빗금 벽 경로, tone=회색 벽 경로. 효과가 있는지 비교할 때 쓴다")
    a = ap.parse_args()

    labs = {p.stem.lower(): p for p in a.labels.rglob("*.json")}
    imgs = [p for p in sorted(a.images.rglob("*")) if p.suffix.lower() == ".png" and p.stem.lower() in labs]
    if a.limit:
        imgs = imgs[:a.limit]
    if not imgs:
        print("이미지와 라벨이 짝지어진 파일이 없습니다(파일 이름이 같아야 합니다)")
        return 1
    if a.viz:
        a.viz.mkdir(parents=True, exist_ok=True)
    print(f"{len(imgs)}장 채점 (병렬 {a.workers}) …")
    jobs = [(str(p), str(labs[p.stem.lower()]), str(a.viz) if a.viz else None, a.disable) for p in imgs]
    rows: list[dict] = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, rs in enumerate(ex.map(score_image, jobs, chunksize=1), 1):
            rows += rs
            if i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  {round(time.perf_counter() - t0)}s", flush=True)

    ok = [r for r in rows if r["status"] == "ok"]
    print(f"\n=== 평면도 {len(rows)}개 (이미지 {len(imgs)}장), 처리 성공 {len(ok)}개")
    for k, c in sorted({(r["status"], 0) for r in rows if r["status"] != "ok"}):
        print(f"  {k}: {sum(1 for r in rows if r['status'] == k)}개")
    if not ok:
        return 0
    f1s = np.array([r["f1"] for r in rows])
    print(f"\n--- 벽 (허용 오차 = 정답 벽 두께의 절반)")
    print(f"  F1 평균 {f1s.mean():.2f}  중앙 {np.median(f1s):.2f}  | 정밀도 평균 {np.mean([r['prec'] for r in rows]):.2f}  재현율 평균 {np.mean([r['rec'] for r in rows]):.2f}")
    print(f"  F1 구간: 0.8 이상 {np.mean(f1s >= 0.8):.0%} / 0.5~0.8 {np.mean((f1s >= 0.5) & (f1s < 0.8)):.0%} / 0.5 미만 {np.mean(f1s < 0.5):.0%}")

    def micro(num: str, den: str, rs=ok):
        n, d = sum(r[num] for r in rs), sum(r[den] for r in rs)
        return f"{n}/{d} ({n / max(d, 1):.0%})"

    gd, gw = sum(r["gt_doors"] for r in ok), sum(r["gt_windows"] for r in ok)
    print("\n--- 문·창 (정답 객체 수 기준, 합산)")
    print(f"  문   재현율 {sum(r['door_hit'] for r in ok)}/{gd} ({sum(r['door_hit'] for r in ok) / max(gd, 1):.0%})  "
          f"개구부 재현율 {sum(r['door_open_hit'] for r in ok)}/{gd} ({sum(r['door_open_hit'] for r in ok) / max(gd, 1):.0%})  정밀도 {micro('door_pred_ok', 'door_pred')}")
    print(f"  창   재현율 {sum(r['win_hit'] for r in ok)}/{gw} ({sum(r['win_hit'] for r in ok) / max(gw, 1):.0%})  "
          f"개구부 재현율 {sum(r['win_open_hit'] for r in ok)}/{gw} ({sum(r['win_open_hit'] for r in ok) / max(gw, 1):.0%})  정밀도 {micro('win_pred_ok', 'win_pred')}")

    print("\n--- 처리 모드별 벽 F1")
    by = defaultdict(list)
    for r in ok:
        by[r["mode"]].append(r["f1"])
    for m, v in sorted(by.items()):
        print(f"  {m:7s} {len(v):4d}개  F1 평균 {np.mean(v):.2f}  (0.5 미만 {np.mean(np.array(v) < 0.5):.0%})")
    print("\n--- 정답 벽 두께(px, 처리 해상도) 분포:", f"중앙 {np.median([r['thick_gt'] for r in ok]):.1f}", f"(10%~90%: {np.percentile([r['thick_gt'] for r in ok], 10):.1f}~{np.percentile([r['thick_gt'] for r in ok], 90):.1f})")

    worst = sorted(rows, key=lambda r: r["f1"])[:a.worst]
    print(f"\n--- 벽 F1 이 낮은 평면도 {len(worst)}개 (눈으로 열어 볼 대상{' → --viz 폴더' if a.viz else ''})")
    for r in worst:
        print(f"  {r['image']} p{r['plan']}  F1 {r['f1']:.2f}  {r.get('mode', r['status'])}  정답 두께 {r.get('thick_gt', '-')}px")

    if a.csv:
        cols = ["image", "plan", "status", "f1", "prec", "rec", "mode", "scale", "rooms", "thick_gt", "tol", "gt_doors", "door_hit", "door_open_hit",
                "door_pred", "door_pred_ok", "gt_windows", "win_hit", "win_open_hit", "win_pred", "win_pred_ok", "ms"]
        with a.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\n평면도별 결과 → {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
