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
