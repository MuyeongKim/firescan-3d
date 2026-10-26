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
cp deploy/docker-up.sh deploy/install.sh deploy/ensure-env.sh deploy/nginx.conf.example "$STAGE/deploy/"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$STAGE" \( -name .DS_Store -o -name '*.pyc' \) -delete
chmod +x "$STAGE"/deploy/*.sh

CODE="$(python3 -c "import secrets; a='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; print(''.join(secrets.choice(a) for _ in range(8)))")"
sed "s/^UPLOAD_CODE=\$/UPLOAD_CODE=$CODE/" .env.example > "$STAGE/.env.example"
grep -q "^UPLOAD_CODE=$CODE\$" "$STAGE/.env.example" || { echo ".env.example 에 UPLOAD_CODE= 줄이 없습니다"; exit 1; }

rm -f "dist/$NAME.tar.gz" "dist/$NAME.zip"
COPYFILE_DISABLE=1 tar -czf "dist/$NAME.tar.gz" -C "$TMP" "$NAME"
(cd "$TMP" && zip -qrX - "$NAME") > "dist/$NAME.zip"

echo "dist/$NAME.tar.gz  $(du -h "dist/$NAME.tar.gz" | cut -f1)"
echo "dist/$NAME.zip     $(du -h "dist/$NAME.zip" | cut -f1)"
echo "업로드 코드: $CODE  (서버의 .env 에서 바꿀 수 있음)"
