# FireScan 3D — 도면 → 3D 내부구조 → 링크 공유

화재·구조 현장에서 **내부 진입 전** 설계도면이나 간단한 평면도(피난안내도 사진 포함)로
건물 내부 구조를 3D로 빠르게 확인하고, **링크 하나로** 대원 휴대폰·태블릿에 전달하는 도구.

- 생성은 서버에서: 도면 1장당 약 0.3–0.4초 (학습 모델 없이 OpenCV 규칙 기반)
- 전달은 링크로: 업로드 즉시 공유 링크 + QR + 카카오톡 등 미리보기 이미지
- 보기는 웹에서: 앱 설치 없이 모바일/태블릿 브라우저에서 3D (첫 방문 약 400KB)

## 빠른 시작

```bash
./run.sh
```

- PC: http://localhost:8000 에서 도면 업로드
- 같은 Wi‑Fi의 휴대폰/태블릿: 결과 화면의 QR을 스캔하거나 `http://<PC IP>:8000/v/...` 링크 열기
  (localhost 로 접속해도 공유 링크와 QR은 자동으로 PC의 LAN IP 주소로 만들어짐)

### 외부망(LTE/5G)에서 열리게 — HTTPS 공개

```bash
brew install cloudflared      # 최초 1회
./deploy/public.sh
```

터미널에 **공개 주소 · 업로드 코드 · QR** 이 표시된다. 이후 만드는 링크는 모두 `https://…` 공개 주소로 발급되어
어느 망의 휴대폰에서도 열린다. 이 창에서 Ctrl+C 하면 공개가 끝난다(남는 설정 없음).

샘플 도면으로 확인:

```bash
.venv/bin/python scripts/make_samples.py     # samples/ 에 6가지 형식 생성
.venv/bin/python scripts/check_samples.py    # 인식 결과·처리시간 출력 + 오버레이 이미지
```

## 사용 흐름

1. **업로드** (`/`): 촬영 버튼(현장 피난안내도 촬영) 또는 파일 선택 / 끌어놓기 / Ctrl+V.
   여러 장이면 순서대로 1F, 2F … (층 이름 직접 입력 가능). PDF 여러 페이지 = 여러 층.
2. **생성**: 업로드 즉시 서버가 벽·문·창·진입구를 인식해 층별 GLB 생성 → 링크 발급.
3. **인식 확인**: 업로드한 사람이 결과 화면에서 인식 오버레이(벽·문)와 자동 경고(축척 추정·문/진입구 없음·창 미구분)를 보고,
   벽·진입구·문 세 항목을 모두 체크해야 **공유(링크·QR)가 열린다**. 틀리면 "삭제하고 다시". 확인 기록(시각·메모)은 서버에
   남고(`POST /api/models/{id}/confirm`, 삭제와 같은 `X-Delete-Token` 필요) 뷰어 상단에 "확인됨/확인 전" 으로 표시된다.
4. **공유**: 공유하기(휴대폰 공유 시트) / 링크 복사 / QR. 링크 미리보기에 평면 요약 이미지가 뜸.
5. **표시 편집(선택)**: 업로드한 기기에서 연 뷰어에만 **표시** 버튼이 보인다. 평면 도면에서 위치를 탭해 방 이름·계단·비상구·소화전·소화기·방화문·위험을
   추가하고(최대 60개, 글자 30자), 표시를 탭하면 삭제한다. 자동 저장되며(`PUT /api/models/{id}/annotations`, `X-Delete-Token` 필요) 링크로 여는 대원은 읽기만 한다.
6. **보기** (`/v/<id>`):

| 기능 | 설명 |
|---|---|
| 3D | 한 손가락 회전, 두 손가락 이동·확대 |
| 평면 | 정사영 위에서 보기. 문=초록, 창=파랑, 진입구=주황 화살표 |
| 내부 | 1인칭. **외부 출입문 바깥에서 안쪽을 보며 시작**, 바닥 탭으로 이동(벽 통과 안 함), 드래그로 둘러보기, 미니맵 탭으로 순간이동 |
| 투시 | 벽 반투명 — 방 배치를 한눈에 |
| 도면 | 원본 도면을 바닥에 표시/숨김(실명·표기 확인용) |
| 거리 | 두 지점 탭 → 거리(m). 호스 전개 길이 가늠용 |
| 층 | 1F/2F…/전체(적층, 위층 도면 반투명) |

한 번 연 모델은 서비스 워커가 캐시하므로 지하 등 신호가 약한 곳에서도 다시 열림(HTTPS 필요).

## 인식 방식 (`app/pipeline/walls.py`)

도면 유형을 자동 판별한다.

| 유형 | 예 | 처리 |
|---|---|---|
| filled | 벽이 검게 칠해진 평면도 이미지 | 획 폭 분포의 골짜기로 얇은 획(글자·문 호·가구·치수) 제거 |
| double | CAD 출력 PDF, 해치 벽, DXF | 선으로 둘러싸인 좁은 공간(벽 속)을 채운 뒤 filled 와 동일 |
| single | 피난안내도·스케치(단선) | 수평/수직 직선 + 긴 사선만 벽으로, 최소 12cm 두께 부여 |

- 컬러 표식(빨간 피난 화살표, 초록 비상구 등)은 채도로 걸러 벽으로 오인하지 않음
- 사진은 조명 불균일을 배경 나눗셈으로 평탄화, EXIF 회전 보정
- **축척**: 입력값(건물 가로 길이) > DXF 단위 > **문 폭(문짝 선이 확인된 개구부 중앙값 = 0.9m)** > 벽 두께(0.2m) > 가정(긴 변 20m).
  샘플 7종에서 오차 0–7%. 추정 근거는 뷰어 범례에 표시되며 '미확인'이면 거리값에 (추정) 표기
- **문/창**: 벽선 위의 끊김을 찾고, 끊김 안에 벽 방향 평행선이 있으면 창, 없으면 문(폭 0.68–1.3m)
- **진입구**: 문 양쪽을 찍어 한쪽만 건물 밖이면 외부 출입문. 문짝이 확인된 것·넓은 것 우선

## 서버 API

| | |
|---|---|
| `POST /api/models` | multipart: `files`(여러 개), `labels`(층 이름, 선택), `name`, `width_m`, `wall_height`, `ttl_days`(1·7·30, 선택) → `{id, url, qr, floors[], delete_token}` |
| `GET /api/models/{id}` | 모델 정보(층별 크기·문/창 수·축척 근거·진입구) |
| `POST /api/models/{id}/confirm` | JSON `{checks:{walls,entrance,doors:true}, note?}` + 헤더 `X-Delete-Token` → 인식 결과 확인 기록 |
| `PUT /api/models/{id}/annotations` | JSON `{items:[{id,floor,kind,x,z,text}]}` 전체 교체 + 헤더 `X-Delete-Token`. kind: room·stairs·exit·hydrant·extinguisher·firedoor·hazard |
| `DELETE /api/models/{id}` | 헤더 `X-Delete-Token`(업로드한 기기에만 저장됨) **또는** `X-Upload-Code`(다른 기기·상황실에서 삭제) 필요 |
| `GET /api/models` | 서버 보관 모델 목록(id·이름·생성·만료·층 수·확인 여부). 헤더 `X-Upload-Code` 필요, 코드가 설정돼 있어야 함 |
| `GET /v/{id}` | 뷰어(og:title/og:image 포함) |
| `GET /m/{id}/f{n}.glb` | 층별 glTF 바이너리 — 다른 3D 도구에서도 열림 |
| `GET /qr/{id}.svg` | 공유 링크 QR |

## 배포

### 1) 이 PC를 공개 서버로 — `deploy/public.sh`

공유기·방화벽 설정 없이 터널로 HTTPS 공개 주소를 만든다. 서버도 함께 띄운다.

| 방식 | 실행 | 주소 | 외부망 측정치(이 PC 기준) |
|---|---|---|---|
| **Cloudflare 임시 터널** (기본) | `./deploy/public.sh` | `https://<무작위>.trycloudflare.com` — **재시작마다 바뀜** | 연결 0.03초, 3D 라이브러리 101KB 0.34초, 2개 층 업로드→링크 0.49초 |
| Tailscale Funnel | `TUNNEL=tailscale ./deploy/public.sh` | `https://<기기>.<tailnet>.ts.net` — 고정 | 연결 1.2–2.7초, 같은 파일 2.4초(도쿄 릴레이 경유) |
| **Cloudflare 고정 터널** (운영 권장) | `CF_TUNNEL=firescan PUBLIC_BASE_URL=https://firescan.example.org ./deploy/public.sh` | 본인 도메인 — 고정 | 임시 터널과 같은 경로라 같은 속도 예상(미검증) |

- 임시 터널은 무료·계정 불필요지만 **스크립트를 다시 켜면 주소가 바뀌어 이전에 보낸 링크가 열리지 않는다.**
  현장 출동 중에는 켜 둔 채로 쓰고, 상시 운영은 고정 터널로.
- 고정 터널 준비(최초 1회, Cloudflare 계정과 Cloudflare에 연결된 도메인 필요):
  ```bash
  cloudflared tunnel login                       # 브라우저에서 도메인 선택
  cloudflared tunnel create firescan
  cloudflared tunnel route dns firescan firescan.example.org
  ```
- 업로드 코드는 처음 실행할 때 `data/upload_code` 에 8자리로 만들어진다. 바꾸려면 파일을 지우거나 `UPLOAD_CODE=…` 지정.
  대원은 링크만 받으면 되고, 코드는 도면을 올리는 사람(상황실·지휘차)만 필요하다.

### 2) 클라우드 상시 운영 — Fly.io 도쿄 (`deploy/fly.sh`)

Mac 을 꺼도 `https://<앱이름>.fly.dev` 로 계속 열린다. 잠들지 않는 서버 1대(2 vCPU 공유·2GB) + 모델 저장 볼륨 1GB,
예상 비용 월 약 $18(도쿄 단가 = 미국 단가 × 1.308, [Fly 가격표](https://fly.io/pricing.md)).

처음 한 번(본인이 직접):
1. [fly.io](https://fly.io) 가입
2. 결제 카드 등록(가입 체험은 최대 7일·머신 2시간이라 상시 운영에는 카드가 필요)
3. 이 Mac 에서 `fly auth login` → 브라우저에서 승인

그다음:
```bash
./deploy/fly.sh firescan-3d      # 이미 쓰인 이름이면 다른 이름
```
앱·볼륨 생성, 업로드 코드·공개 주소 설정, 원격 빌드·배포, 외부 접속 확인까지 한 번에 한다.
코드를 고친 뒤 같은 명령을 다시 실행하면 같은 주소로 새 버전이 올라가고 저장된 모델은 유지된다.

### 3) 자체 서버에 설치 — 배포 묶음

```bash
./deploy/package.sh      # → dist/firescan3d-server.tar.gz / .zip (약 280KB, 업로드 코드 새로 생성)
```
묶음을 서버에 올려 풀고 Docker(`./deploy/docker-up.sh`) 또는 직접 설치(`sudo ./deploy/install.sh`, Python 3.12 이상) 중
하나를 실행한다. 설치·HTTPS·업데이트·문제 해결은 묶음 안의 [SERVER.md](SERVER.md) 참고.
깨끗한 폴더에 풀어 직접 설치 방식으로 업로드→링크→3D 파일 제공까지 시험함(Docker 방식은 이 PC에 Docker 가 없어 미검증).

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `PUBLIC_BASE_URL` | (요청 주소) | 공유 링크·QR·미리보기에 쓸 외부 주소. 없으면 `data/public_url`(public.sh 가 기록) |
| `UPLOAD_CODE` | (없음) | 업로드 접근 코드. 없으면 `data/upload_code`, 둘 다 없으면 코드 없이 업로드 가능 |
| `DATA_DIR` | `./data` | 모델 저장 위치 |
| `MODEL_TTL_DAYS` | 30 | 기본·상한 보관 기간(업로드 때 1·7·30일 중 고르며 이 값이 더 짧으면 그 값이 상한, 0=무기한) |
| `MAX_UPLOAD_MB` | 40 | 파일당 최대 크기 |

HTTPS 로 열면 공유 시트·클립보드·**오프라인 캐시**가 모두 동작한다. 한 번 연 모델은 휴대폰에 저장되어
신호가 끊기거나 서버 PC가 꺼져도(터널이 오류를 돌려줘도) 다시 열린다 — 서버 중지 상태에서 검증함.

## 보안·운영 메모

- 링크 ID는 64비트 무작위값으로, 링크가 곧 열람 권한. 검색 차단(`noindex`), 리퍼러 차단 헤더 적용
- 보관 기간은 업로드할 때 1·7·30일 중에서 고르고(화면 기본 7일), 만료는 **생성 시각 기준**이라 확인·표시 저장으로 늘어나지 않는다. 만료된 링크는 정리 작업(1시간 주기) 전에도 즉시 열리지 않는다. 업로드한 기기에서는 "모두 삭제", 다른 기기에서는 업로드 접근 코드로 "서버 보관 모델 관리"에서 목록을 보고 삭제할 수 있다(코드 보유자는 모든 모델을 지울 수 있음)
- 업로드 원본 파일은 저장하지 않음. 단, 도면 이미지는 GLB 바닥 텍스처·미리보기로 들어가므로 결과물도 민감 정보로 취급. 기한 지나면 자동 삭제
- 업로드는 접근 코드 필요(공개 실행 시 자동 생성). IP당 10분에 업로드 30회, 코드 오입력 8회 초과 시 10분 차단
  — Cloudflare 터널 뒤에서 방문자 IP별로 따로 막히는 것 확인. Tailscale Funnel 뒤에서는 미검증
- 열람(링크)은 로그인 없이 열린다 — 현장 공유 속도를 우선한 설계. 더 강한 통제가 필요하면 Cloudflare Access 등 앞단 인증 추가

## 한계와 다음 단계

- DWG 직접 지원 안 함(DXF/PDF로 저장해 업로드)
- 2중선 CAD 도면은 창이 벽으로 채워짐(바닥 도면에는 보임). 레이어 정보(DXF) 활용으로 개선 가능
- 사진은 원근 보정 없이 처리 → 비스듬히 찍으면 형태가 약간 기울어짐. 문서 스캐너식 4점 보정 추가 여지
- 방 이름·소방시설은 업로드한 사람이 수동으로 표시(자동 인식은 아님) — OCR/비전 모델로 자동 표시는 후속 과제
- 층 간 정렬은 각 층 벽 중심 기준(도면 크롭이 다르면 어긋날 수 있음)
- 실제 도면 데이터로 검증 필요: 현재 수치는 합성 샘플 7종 기준

## 구조

```
app/
  main.py              FastAPI 서버(업로드·링크·QR·파일 서빙·만료 정리·업로드 코드/횟수 제한)
  pipeline/
    loaders.py         이미지/PDF/DXF → 래스터(+DXF 단위·레이어)
    walls.py           벽·문·창·진입구 인식, 축척 추정
    mesh.py            돌출 메시 + 미리보기 이미지
    glb.py             의존성 없는 GLB 작성기
  static/              업로드 페이지, 3D 뷰어(three.js 0.185.1 내장), 서비스 워커
deploy/
  public.sh            HTTPS 공개 실행(Cloudflare/Tailscale 터널 + 업로드 코드 + 터미널 QR)
  fly.sh               Fly.io 도쿄 상시 배포(fly.toml)
  package.sh           자체 서버용 배포 묶음 생성(dist/)
  docker-up.sh         서버에서 Docker 로 실행
  install.sh           서버에 직접 설치(systemd 등록)
  nginx.conf.example   HTTPS 앞단 예시
scripts/
  make_samples.py      합성 샘플 도면 생성(채움 벽/해치/CAD PDF/피난안내도 사진/DXF/2층)
  check_samples.py     회귀 점검
```
