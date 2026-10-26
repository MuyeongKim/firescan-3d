#!/usr/bin/env bash
# .env 가 없으면 .env.example 을 복사해 만들고, 업로드 코드가 비어 있으면 8자리를 새로 만든다(처음 한 번만).
# 이미 있는 .env 는 건드리지 않는다. docker-up.sh, install.sh 가 호출한다.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && exit 0
[ -f .env.example ] || { echo ".env.example 이 없습니다"; exit 1; }
cp .env.example .env
if grep -qE '^UPLOAD_CODE=$' .env; then
  CODE="$(LC_ALL=C tr -dc 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789' </dev/urandom 2>/dev/null | head -c 8 || true)"
  awk -v c="$CODE" '/^UPLOAD_CODE=/ { print "UPLOAD_CODE=" c; next } { print }' .env > .env.tmp && mv .env.tmp .env
fi
chmod 600 .env
echo "· .env 생성(.env.example 복사)"
