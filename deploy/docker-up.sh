#!/usr/bin/env bash
# Docker 로 서버를 띄운다(재부팅 후 자동 시작). 압축을 푼 폴더에서: ./deploy/docker-up.sh
# 업데이트도 같은 명령: 새 파일을 덮어쓴 뒤 다시 실행(.env 와 data/ 는 유지)
set -euo pipefail
cd "$(dirname "$0")/.."

if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else echo "Docker(Compose 포함)가 없습니다. Docker 를 설치하거나 sudo ./deploy/install.sh 를 쓰세요."; exit 1; fi

./deploy/ensure-env.sh
mkdir -p data

# 컨테이너는 root 가 아닌 번호로 돈다. 보통은 이 스크립트를 실행한 사용자의 번호로 맞춰 data/ 에 쓸 수 있게 한다.
if [ "$(id -u)" -eq 0 ]; then
  FS_UID=10001; FS_GID=10001
  chown -R "$FS_UID:$FS_GID" data
else
  FS_UID="$(id -u)"; FS_GID="$(id -g)"
  # 예전에 root 로 돌던 Docker 가 만든 파일이 남아 있으면 쓰지 못하니 한 번만 정리하도록 안내한다
  if [ -n "$(find data ! -writable -print -quit 2>/dev/null)" ]; then
    echo "data/ 안에 이 사용자가 쓸 수 없는 파일이 있습니다(이전에 root 로 실행된 Docker 가 만든 것)."
    echo "한 번만 아래를 실행한 뒤 다시 시도하세요:"
    echo "  sudo chown -R $FS_UID:$FS_GID data"
    exit 1
  fi
fi
export FS_UID FS_GID
set -a; . ./.env; set +a

$DC up -d --build

printf "· 서버 응답 확인 중"
for _ in $(seq 60); do
  curl -fs --max-time 3 "http://127.0.0.1:${PORT:-8000}/healthz" >/dev/null 2>&1 && { echo " 정상"; break; }
  printf "."; sleep 2
done
echo
echo "  접속:        ${PUBLIC_BASE_URL:-http://<서버 주소>:${PORT:-8000}}"
echo "  업로드 코드: ${UPLOAD_CODE:-(없음: 누구나 업로드 가능)}"
echo "  로그:        $DC logs -f    중지: $DC down"
