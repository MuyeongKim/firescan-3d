# FireScan 3D 서버 설치

평면도를 올리면 3D로 바꿔 공유 링크를 만들어 주는 웹 서버입니다.
배포 묶음(zip)을 풀거나 GitHub 저장소를 받은(`git clone`) 폴더에서 아래 **A, B 중 하나**만 실행하면 됩니다.

## 필요 사양

- 리눅스 서버(x86_64), 메모리 2GB 이상(도면 한 장을 처리할 때 최대 약 0.7GB 사용)
- 디스크: 층 하나에 약 0.25MB. 30일이 지나면 자동 삭제
- A 방식은 Docker(Compose 포함), B 방식은 Python 3.12 이상 + systemd

## A. Docker로 실행 (권장)

```bash
./deploy/docker-up.sh
```

컨테이너는 root가 아닌 사용자로 실행됩니다. 이 스크립트가 실행한 사용자의 번호로 맞춰 주므로 `data/`에 그대로 쓸 수 있습니다. `docker compose up`을 직접 실행하면 번호 10001로 돌아서 `data/` 권한을 직접 맞춰야 할 수 있으니, 가급적 이 스크립트로 실행하세요.

## B. Docker 없이 직접 설치

```bash
sudo ./deploy/install.sh
```

둘 다 처음 실행할 때 `.env.example`을 `.env`로 복사하고, 서버를 재부팅해도 자동으로 다시 뜹니다.

## 접속

- 브라우저에서 `http://<서버 주소>:8000` → 업로드 화면
- 업로드 코드: `.env` 파일의 `UPLOAD_CODE` 값. 도면을 올리는 사람만 필요하고, 링크를 받는 대원은 필요 없습니다
- 서버 방화벽에서 8000 포트를 엽니다(아래처럼 HTTPS를 붙이면 443)

## 설정 (`.env`)

| 항목 | 기본값 | 설명 |
| --- | --- | --- |
| `UPLOAD_CODE` | 묶음마다 새로 생성(GitHub에서 받았으면 처음 실행 때 생성) | 업로드 접근 코드. 처음 실행이 끝날 때 화면에도 표시. 비우면 누구나 업로드 가능 |
| `PUBLIC_BASE_URL` | 비어 있음 | 공유 링크·QR에 쓸 주소(예: `https://firescan.example.org`). 비우면 접속한 주소를 그대로 씀 |
| `BASE_PATH` | 비어 있음 | nginx 하위 경로로 서비스할 때 그 경로(예: `/kmy/firescan`). 이때 `PUBLIC_BASE_URL`에도 경로까지 적음 |
| `PORT` | 8000 | 서버 포트 |
| `HOST` | 0.0.0.0 | B 방식에서 대기 주소. 앞단에 nginx를 두면 `127.0.0.1` |
| `MODEL_TTL_DAYS` | 30 | 모델 보관 일수(0이면 무기한) |
| `MAX_UPLOAD_MB` | 40 | 파일 하나 최대 크기 |
| `AUDIT_DAYS` | 90 | 감사 기록 보관 일수(지난 기록은 정리 작업이 지움, 0 이하면 안 지움) |
| `AUDIT_LOG` | `data/audit.jsonl` | 감사 기록 파일 위치. 비우면 기본 위치 |
| `FORWARDED_ALLOW_IPS` | 127.0.0.1 | 앞단 nginx 주소. 이 주소에서 온 요청만 nginx가 알려 준 접속자 IP를 믿음. A 방식 앞에 nginx를 두면 `172.16.0.0/12` |

바꾼 뒤에는 처음과 같은 명령(A 또는 B)을 다시 실행하면 적용됩니다.

## HTTPS (권장)

HTTP로도 업로드·링크·3D 보기는 모두 됩니다. 다만 휴대폰의 **공유하기 버튼, 링크 복사, 신호가 끊겨도 다시 여는 저장 기능**은 HTTPS에서만 동작합니다.

- nginx가 있다면 `deploy/nginx.conf.example`을 참고합니다. `client_max_body_size 200m`이 없으면 업로드가 실패합니다
- `.env`에 `PUBLIC_BASE_URL=https://<도메인>`, B 방식이면 `HOST=127.0.0.1`도 지정합니다
- A 방식이면 `.env`에 `FORWARDED_ALLOW_IPS=172.16.0.0/12`도 넣습니다. 빠뜨리면 모든 접속자가 같은 IP로 보여, 한 사람이 코드를 8번 틀리면 모두의 업로드가 10분간 막힙니다
- 하위 경로(예: `https://<도메인>/kmy/firescan/`)로 내보낼 때는 `BASE_PATH=/kmy/firescan`, `PUBLIC_BASE_URL=https://<도메인>/kmy/firescan`을 지정합니다(nginx 예시 파일 맨 위 설명 참고)

## 업데이트

새로 받은 묶음을 같은 폴더에 덮어쓰고(`.env`와 `data/`는 그대로 둠) 처음과 같은 명령을 다시 실행합니다. 이미 만든 링크는 계속 열립니다.

## 확인과 문제 해결

| 증상 | 확인·조치 |
| --- | --- |
| 서버가 떴는지 | `curl http://127.0.0.1:8000/healthz` → `{"ok":true}` |
| 로그 보기 | A: `docker compose logs -f` · B: `journalctl -u firescan3d -f` |
| 업로드하면 413 오류 | nginx의 `client_max_body_size` |
| 공유 링크 주소가 틀림 | `.env`의 `PUBLIC_BASE_URL` 지정 |
| "Python 3.12 이상이 필요" | A 방식을 쓰거나 Python 3.12 설치(Ubuntu: `sudo apt install python3.12 python3.12-venv`) |
| A 방식에서 업로드가 실패하고 로그에 `Permission denied` | `data/`를 이전에 root로 돌던 Docker가 만들었기 때문입니다. `sudo chown -R $(id -u):$(id -g) data`를 한 번 실행한 뒤 `./deploy/docker-up.sh`를 다시 실행하세요(스크립트가 이런 경우를 미리 알려 줍니다) |

## 데이터와 제거

- 만든 모델은 `data/` 폴더에 있습니다(백업 대상). 업로드한 원본 파일은 저장하지 않습니다
- 누가 언제 올리고 지웠는지는 `data/audit.jsonl`에 한 줄씩 남습니다(접근 코드·건물명은 기록하지 않음). 업로드 화면의 '서버 보관 모델 관리' → '업로드·삭제 기록 보기'(업로드 코드 필요)에서도 볼 수 있습니다
- 제거: A는 `docker compose down`, B는 `sudo systemctl disable --now firescan3d && sudo rm /etc/systemd/system/firescan3d.service`
