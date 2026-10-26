#!/usr/bin/env bash
# 자체 서버에 올릴 배포 묶음을 만든다 → dist/firescan3d-server.tar.gz, dist/firescan3d-server.zip
# 묶음마다 업로드 코드를 새로 만들어 .env.example 에 넣는다(서버에서 처음 실행할 때 .env 로 복사됨).
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=firescan3d-server
TMP="$(mktemp -d)"
STAGE="$TMP/$NAME"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$STAGE/deploy" dist

cp -R app "$STAGE/"
cp requirements.txt Dockerfile .dockerignore docker-compose.yml SERVER.md "$STAGE/"
cp deploy/docker-up.sh deploy/install.sh deploy/nginx.conf.example "$STAGE/deploy/"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$STAGE" \( -name .DS_Store -o -name '*.pyc' \) -delete
chmod +x "$STAGE"/deploy/*.sh

CODE="$(python3 -c "import secrets; a='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; print(''.join(secrets.choice(a) for _ in range(8)))")"
cat > "$STAGE/.env.example" <<EOF
# FireScan 3D 서버 설정. 처음 실행할 때 .env 로 복사되며, 바꾼 뒤에는 실행 명령을 다시 실행하면 적용된다.
# 업로드 접근 코드(도면을 올리는 사람만 알면 됨). 비우면 누구나 업로드 가능
UPLOAD_CODE=$CODE
# 공유 링크·QR 에 쓸 외부 주소(예: https://firescan.example.org). 비우면 접속한 주소를 그대로 사용
PUBLIC_BASE_URL=
PORT=8000
# 직접 설치(B) 때 대기 주소: 앞단에 nginx 를 두면 127.0.0.1, 바로 열려면 0.0.0.0
HOST=0.0.0.0
MODEL_TTL_DAYS=30
MAX_UPLOAD_MB=40
EOF

rm -f "dist/$NAME.tar.gz" "dist/$NAME.zip"
COPYFILE_DISABLE=1 tar -czf "dist/$NAME.tar.gz" -C "$TMP" "$NAME"
(cd "$TMP" && zip -qrX - "$NAME") > "dist/$NAME.zip"

echo "dist/$NAME.tar.gz  $(du -h "dist/$NAME.tar.gz" | cut -f1)"
echo "dist/$NAME.zip     $(du -h "dist/$NAME.zip" | cut -f1)"
echo "업로드 코드: $CODE  (서버의 .env 에서 바꿀 수 있음)"
