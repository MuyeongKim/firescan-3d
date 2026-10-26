"""샘플 도면 회귀 점검: 처리 시간·인식 결과를 출력하고 인식 오버레이를 한 장으로 저장.

사용법: python scripts/make_samples.py && python scripts/check_samples.py [출력폴더]
오버레이 색: 빨강=벽, 초록=문, 파랑=창, 회색=건물 바닥
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.pipeline import walls as W  # noqa: E402
from app.pipeline.loaders import load_pages  # noqa: E402

SP = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples"
tiles = []
for f in ['apt_filled.png','apt_hatch.png','apt_cad.pdf','evac_photo.jpg','apt.dxf','apt_small.jpg','apt_2f.png']:
    data = (ROOT / 'samples' / f).read_bytes()
    t0 = time.perf_counter()
    pages = load_pages(data, f, '1F')
    t1 = time.perf_counter()
    p = pages[0]
    r = W.extract(p.image, ink=p.ink, m_per_px=p.m_per_px)
    t2 = time.perf_counter()
    ys, xs = np.nonzero(r.walls)
    wm = (xs.max()-xs.min())*r.m_per_px; hm = (ys.max()-ys.min())*r.m_per_px
    nd = cv2.connectedComponents(r.doors)[0]-1; nw = cv2.connectedComponents(r.windows)[0]-1
    print(f"{f:16s} {p.image.shape[1]}x{p.image.shape[0]} load {1000*(t1-t0):5.0f}ms extract {1000*(t2-t1):5.0f}ms mode={r.mode:6s} t={r.t_px:5.1f}px scale={r.m_per_px*1000:.2f}mm/px ({r.scale_source}) size={wm:.1f}x{hm:.1f}m doors={nd} wins={nw} {r.notes} 진입구={len(r.entrances)}")
    ov = W.debug_overlay(p.image, r)
    tiles.append(cv2.resize(ov, (800, 620)))
while len(tiles) % 3: tiles.append(np.full_like(tiles[0], 255))
sheet = np.vstack([np.hstack(tiles[i:i+3]) for i in range(0, len(tiles), 3)])
cv2.imwrite(str(SP / 'check_sheet.jpg'), sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])
print('overlay ->', SP / 'check_sheet.jpg')
