"""폴더의 도면 이미지를 모두 인식 파이프라인(서버 업로드와 같은 처리)에 넣어 실패 유형과 결과 분포를 집계한다. 라벨이 없어도 된다.

  python scripts/eval_dataset.py datasets/aihub --limit 300          # 폴더 아래 도면을 앞에서 300장
  python scripts/eval_dataset.py DIR --random 500 --csv /tmp/out.csv  # 무작위 500장, 장별 결과를 CSV 로 저장
  python scripts/eval_dataset.py DIR --workers 2 --examples 10        # 병렬 2개(장당 최대 약 0.7GB), 의심 사례 예시 10개씩

집계: 성공률, 실패 유형(분류 코드·예외 종류), 의심 결과(터무니없는 크기·닫힌 방 0·문 0·단선 모드), 처리 모드와 축척 근거 분포,
크기·처리 시간 분포. 의심·실패 사례마다 예시 파일명을 보여 줘서 눈으로 열어 볼 수 있다. 데이터는 datasets/ (Git 제외)에 둔다.
'성공'은 오류 없이 평면도로 처리됐다는 뜻이지 맞게 인식했다는 뜻이 아니다 — 정답 비교는 라벨이 있을 때 따로 한다.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".dxf"}
SIZE_OK_M = (3.0, 80.0)        # 한 층 건물 긴 변이 이 범위 밖이면 의심(축척 오류)


def run_one(path: str) -> dict:
    """한 파일을 main.py 의 업로드 처리와 같은 판단으로 돌린다. 실패 유형은 분류 코드 또는 error:<예외 종류>."""
    from app.main import fail_reason, not_a_plan
    from app.pipeline import UnsupportedFile, build_floor, load_pages

    p = Path(path)
    row: dict = {"file": p.name, "path": path, "status": "ok", "pages": 0}
    t0 = time.perf_counter()
    try:
        pages = load_pages(p.read_bytes(), p.name, "")
    except Exception as e:  # noqa: BLE001
        row["status"] = f"fail:{fail_reason(e)}" if isinstance(e, UnsupportedFile) else f"error:load:{type(e).__name__}"
        row["ms"] = round((time.perf_counter() - t0) * 1000)
        return row
    row["pages"] = len(pages)
    last, best = "fail:no_walls", None
    for pg in pages:
        try:
            out = build_floor(pg)
        except ValueError:
            continue
        except Exception as e:  # noqa: BLE001
            last = f"error:{type(e).__name__}"
            continue
        if not_a_plan(out.info):
            last = "fail:not_plan"
            continue
        if best is None or out.info.get("rooms", 0) > best.get("rooms", 0):
            best = out.info
    row["ms"] = round((time.perf_counter() - t0) * 1000)
    if best is None:
        row["status"] = last
        return row
    row.update(width_m=best["width_m"], depth_m=best["depth_m"], doors=best["doors"], windows=best["windows"],
               rooms=best.get("rooms", 0), mode=best.get("mode", "?"), scale=best.get("scale_source", "?"))
    return row


def flags_of(r: dict) -> list[str]:
    if r["status"] != "ok":
        return []
    f = []
    lo, hi = SIZE_OK_M
    long_side = max(r["width_m"], r["depth_m"])
    if not lo <= long_side <= hi:
        f.append("absurd_size")
    if r["rooms"] == 0:
        f.append("no_rooms")
    if r["doors"] == 0:
        f.append("no_doors")
    if r["mode"] == "single":
        f.append("single_mode")
    if r["scale"] == "assumed":
        f.append("scale_assumed")
    return f


def collect(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith(("check_sheet", ".")))


def pct(v: list[float], q: float) -> float:
    return float(np.percentile(v, q)) if v else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path, help="도면 이미지가 있는 폴더(하위 폴더 포함)")
    ap.add_argument("--limit", type=int, help="앞에서 N 장만")
    ap.add_argument("--random", type=int, help="무작위 N 장(--seed 로 고정)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=min(2, os.cpu_count() or 1))
    ap.add_argument("--examples", type=int, default=6, help="유형별로 보여 줄 예시 파일 수")
    ap.add_argument("--csv", type=Path, help="장별 결과를 저장할 CSV 경로")
    a = ap.parse_args()

    files = collect(a.dir)
    if not files:
        print(f"{a.dir} 에서 도면({', '.join(sorted(EXTS))})을 찾지 못했습니다")
        return 1
    if a.random:
        files = random.Random(a.seed).sample(files, min(a.random, len(files)))
    elif a.limit:
        files = files[:a.limit]
    print(f"{len(files)}장 처리 (병렬 {a.workers}) …")

    rows, t0 = [], time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(run_one, [str(p) for p in files], chunksize=1), 1):
            rows.append(r)
            if i % 50 == 0 or i == len(files):
                print(f"  {i}/{len(files)}  {round(time.perf_counter() - t0)}s", flush=True)
    for r in rows:
        r["flags"] = ",".join(flags_of(r))

    n = len(rows)
    ok = [r for r in rows if r["status"] == "ok"]
    print(f"\n=== 결과: 총 {n}장, 평면도로 처리 {len(ok)}장 ({len(ok) / n:.0%}), 걸린 시간 {round(time.perf_counter() - t0)}s")

    bad = Counter(r["status"] for r in rows if r["status"] != "ok")
    if bad:
        print("\n--- 실패 유형 (fail: 정상 거절, error: 예외 = 고쳐야 할 결함 후보)")
        for k, v in bad.most_common():
            ex_ = [r["file"] for r in rows if r["status"] == k][:a.examples]
            print(f"  {k:28s} {v:5d}장 ({v / n:.1%})  예: {', '.join(ex_)}")

    if ok:
        print("\n--- 의심 결과 (성공했지만 맞게 인식했는지 의심되는 것)")
        fc = Counter(f for r in ok for f in r["flags"].split(",") if f)
        meaning = {"absurd_size": f"건물 긴 변이 {SIZE_OK_M[0]:g}~{SIZE_OK_M[1]:g}m 밖(축척 오류)", "no_rooms": "닫힌 방 0개",
                   "no_doors": "문 0개", "single_mode": "단선 모드(벽 두께를 못 고름)", "scale_assumed": "축척 근거 없음(가정 20m)"}
        for k in meaning:
            if fc[k]:
                ex_ = [r["file"] for r in ok if k in r["flags"].split(",")][:a.examples]
                print(f"  {k:14s} {fc[k]:5d}장 ({fc[k] / len(ok):.1%}) {meaning[k]}  예: {', '.join(ex_)}")
        clean = sum(1 for r in ok if not r["flags"])
        print(f"  의심 항목 없음: {clean}장 ({clean / len(ok):.1%} of 처리된 도면)")

        print("\n--- 분포")
        print("  모드      :", dict(Counter(r["mode"] for r in ok).most_common()))
        print("  축척 근거 :", dict(Counter(r["scale"] for r in ok).most_common()))
        ls = [max(r["width_m"], r["depth_m"]) for r in ok]
        print(f"  긴 변(m)  : 중앙 {pct(ls, 50):.1f}  (10%~90%: {pct(ls, 10):.1f}~{pct(ls, 90):.1f})")
        print(f"  문 / 창 / 방 개수 중앙값: {pct([r['doors'] for r in ok], 50):.0f} / {pct([r['windows'] for r in ok], 50):.0f} / {pct([r['rooms'] for r in ok], 50):.0f}")
    ms = [r["ms"] for r in rows]
    print(f"  장당 처리 시간(ms): 중앙 {pct(ms, 50):.0f}  95% {pct(ms, 95):.0f}  최대 {max(ms)}")

    if a.csv:
        cols = ["file", "status", "flags", "pages", "width_m", "depth_m", "doors", "windows", "rooms", "mode", "scale", "ms", "path"]
        with a.csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\n장별 결과 → {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
