#!/usr/bin/env bash
# Docker 없이 리눅스 서버에 직접 설치하고 systemd 서비스로 등록한다(재부팅 후 자동 시작).
# 압축을 푼 폴더에서: sudo ./deploy/install.sh
# 필요: Python 3.12 이상, systemd. 업데이트는 새 파일을 덮어쓴 뒤 다시 실행(.env 와 data/ 는 유지)
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$PWD"
SVC=firescan3d
[ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요: sudo ./deploy/install.sh"; exit 1; }
RUN_USER="${SUDO_USER:-root}"
as_user() { if [ "$RUN_USER" = root ]; then "$@"; else sudo -u "$RUN_USER" "$@"; fi; }

# Python 3.12 이상 찾기
PY=""
for c in "${PYTHON:-}" python3.13 python3.12 python3; do
  [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.12 이상이 필요합니다."
  echo "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
  echo "  또는 Docker 방식: ./deploy/docker-up.sh"
  exit 1
fi
echo "· Python: $("$PY" --version)"

# 가상환경과 패키지(설치 폴더 소유자 권한)
[ -x .venv/bin/python ] || as_user "$PY" -m venv .venv \
  || { echo "venv 를 만들 수 없습니다. Ubuntu/Debian: sudo apt install python3.12-venv"; exit 1; }
as_user .venv/bin/pip install -q --upgrade pip
as_user .venv/bin/pip install -q -r requirements.txt
as_user mkdir -p data
as_user ./deploy/ensure-env.sh
set -a; . ./.env; set +a

cat > "/etc/systemd/system/$SVC.service" <<EOF
[Unit]
Description=FireScan 3D (floor plan to 3D link server)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
ExecStart=$DIR/.venv/bin/python -m uvicorn app.main:app --host \${HOST} --port \${PORT} --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"

printf "· 서버 응답 확인 중"
for _ in $(seq 30); do
  curl -fs --max-time 3 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && { echo " 정상"; break; }
  printf "."; sleep 1
done
echo
echo "  접속:        ${PUBLIC_BASE_URL:-http://<서버 주소>:${PORT}}"
echo "  업로드 코드: ${UPLOAD_CODE:-(없음: 누구나 업로드 가능)}"
echo "  로그:        journalctl -u $SVC -f    재시작: sudo systemctl restart $SVC"
