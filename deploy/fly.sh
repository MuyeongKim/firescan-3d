#!/usr/bin/env bash
# Fly.io(도쿄)에 상시 배포한다. Mac 을 꺼도 https://<앱이름>.fly.dev 로 계속 열린다.
#
# 처음 한 번(본인이 직접): fly.io 가입 → 결제 카드 등록 → 이 Mac 에서 `fly auth login`
# 사용법: ./deploy/fly.sh [앱이름]      (기본 firescan-3d, 이미 쓰인 이름이면 다른 이름)
# 코드를 고친 뒤 다시 실행하면 같은 주소로 새 버전이 올라간다(저장된 모델은 유지).
set -euo pipefail
cd "$(dirname "$0")/.."

APP="${1:-${FLY_APP:-firescan-3d}}"
REGION="$(grep -E '^primary_region' fly.toml | cut -d'"' -f2)"
URL="https://$APP.fly.dev"

command -v fly >/dev/null 2>&1 || { echo "flyctl 이 없습니다: brew install flyctl"; exit 1; }
fly auth whoami >/dev/null 2>&1 || { echo "먼저 로그인하세요: fly auth login"; exit 1; }

# 1) 앱(=공개 주소) — 이미 있으면 그대로 쓴다
if ! fly status -a "$APP" >/dev/null 2>&1; then
  fly apps create "$APP" -o personal -y \
    || { echo "앱 이름 '$APP' 을 쓸 수 없습니다(이미 사용 중). 다른 이름으로: ./deploy/fly.sh <이름>"; exit 1; }
fi

# 2) 모델 저장용 볼륨 1GB — 없을 때만 만든다
if ! fly volumes list -a "$APP" 2>/dev/null | grep -q firescan_data; then
  fly volumes create firescan_data -r "$REGION" -s 1 -a "$APP" -y
fi

# 3) 비밀값: 업로드 코드는 이 PC 것과 같게(없으면 새로 만듦), 링크·QR 기준 주소
CODE="${UPLOAD_CODE:-$(cat data/upload_code 2>/dev/null || true)}"
if [ -z "$CODE" ]; then
  CODE="$(python3 -c "import secrets; a='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; print(''.join(secrets.choice(a) for _ in range(8)))")"
  mkdir -p data; (umask 077; echo "$CODE" > data/upload_code)
fi
fly secrets set --stage -a "$APP" UPLOAD_CODE="$CODE" PUBLIC_BASE_URL="$URL" >/dev/null

# 4) 배포: Fly 원격 빌더에서 이미지를 만든다(이 Mac 에 Docker 불필요). 볼륨 때문에 1대만
fly deploy -a "$APP" --remote-only --ha=false

# 5) 외부에서 실제로 열리는지 확인
printf "· 외부 접속 확인 중"
OK=""
for _ in $(seq 30); do
  curl -fs --max-time 5 "$URL/healthz" >/dev/null 2>&1 && { OK=1; break; }; printf "."; sleep 2
done
[ -n "$OK" ] && echo " 정상" || echo " 아직 응답 없음(fly logs -a $APP 로 확인)"

cat <<EOF

  ┌─────────────────────────────────────────────────────────────
  │ 상시 주소   $URL
  │ 업로드 코드 $CODE
  │ 지역        $REGION (도쿄) · 잠들지 않음 · 모델 30일 보관
  └─────────────────────────────────────────────────────────────
  이제 Mac 을 꺼도 이 주소에서 업로드·열람이 됩니다.
  로그: fly logs -a $APP    상태: fly status -a $APP

EOF
if [ -x .venv/bin/python ]; then
  .venv/bin/python -c "import segno, sys; segno.make(sys.argv[1], error='m').terminal(compact=True)" "$URL" || true
fi
