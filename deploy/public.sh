#!/usr/bin/env bash
# 외부망(LTE/5G) 휴대폰·태블릿에서도 링크가 열리도록 HTTPS 공개 주소로 서버를 띄운다.
#
#   기본  Cloudflare 임시 터널 : https://<무작위>.trycloudflare.com  서울 엣지, 빠름. 재시작마다 주소 바뀜
#   TUNNEL=tailscale          : https://<기기>.<tailnet>.ts.net     고정 주소. 해외 릴레이 경유라 느림(첫 접속 ~5초)
#   CF_TUNNEL=<이름> PUBLIC_BASE_URL=https://<도메인>
#                             : Cloudflare 고정 터널(본인 계정·도메인). 빠르고 주소 고정 — 운영 권장(README)
#
# 이 창을 닫거나 Ctrl+C 하면 공개도 함께 끝난다(설정이 남지 않음).
# 공개 중에는 업로드 접근 코드가 있어야 도면을 올릴 수 있고, 생성된 링크는 누구나 열 수 있다.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
export DATA_DIR="${DATA_DIR:-$PWD/data}"
mkdir -p "$DATA_DIR"
PY=.venv/bin/python
[ -x "$PY" ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }

# ---------------------------------------------------------------- 업로드 접근 코드
CODE_FILE="$DATA_DIR/upload_code"
if [ -z "${UPLOAD_CODE:-}" ] && [ ! -s "$CODE_FILE" ]; then
  (umask 077; "$PY" -c "import secrets; a='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; print(''.join(secrets.choice(a) for _ in range(8)))" > "$CODE_FILE")
fi
CODE="${UPLOAD_CODE:-$(cat "$CODE_FILE")}"

# ---------------------------------------------------------------- 정리
PIDS=()
MODE=""
cleanup() {
  rm -f "$DATA_DIR/public_url" "${LOG:-}"
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
  [ "$MODE" = tailscale ] && tailscale funnel --https=443 off >/dev/null 2>&1 || true
  echo; echo "공개 종료: 외부에서 더 이상 접속할 수 없습니다."
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# ---------------------------------------------------------------- 서버
if curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "· 이미 실행 중인 서버 사용 (포트 $PORT)"
else
  "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning &
  PIDS+=($!)
  for _ in $(seq 40); do curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.25; done
  curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null || { echo "서버를 시작하지 못했습니다"; exit 1; }
  echo "· 서버 시작 (127.0.0.1:$PORT)"
fi

# ---------------------------------------------------------------- 공개 터널
ts_host() {
  command -v tailscale >/dev/null 2>&1 || return 1
  tailscale status --json 2>/dev/null | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
s = d.get("Self") or {}
caps = list(s.get("CapMap") or {}) + list(s.get("Capabilities") or [])
ok = d.get("BackendState") == "Running" and d.get("CertDomains") and any("funnel" in c for c in caps)
print(s["DNSName"].rstrip(".") if ok else "")' | grep .
}

URL=""
TUNNEL="${TUNNEL:-auto}"
LOG="$(mktemp)"
if [ -n "${CF_TUNNEL:-}" ]; then
  [ -n "${PUBLIC_BASE_URL:-}" ] || { echo "CF_TUNNEL 을 쓰면 PUBLIC_BASE_URL(예: https://firescan.example.org)도 지정하세요"; exit 1; }
  MODE=cloudflare-named
  cloudflared tunnel --no-autoupdate run --url "http://127.0.0.1:$PORT" "$CF_TUNNEL" >"$LOG" 2>&1 &
  PIDS+=($!)
  URL="${PUBLIC_BASE_URL%/}"
elif [ "$TUNNEL" != tailscale ] && command -v cloudflared >/dev/null 2>&1; then
  MODE=cloudflare
  cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOG" 2>&1 &
  PIDS+=($!)
  for _ in $(seq 60); do
    URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
    [ -n "$URL" ] && break; sleep 0.5
  done
elif [ "$TUNNEL" != cloudflare ] && HOST="$(ts_host)"; then
  if ! tailscale funnel status 2>&1 | grep -q "No serve config"; then
    echo "Tailscale serve/funnel 에 다른 설정이 있어 덮어쓰지 않습니다:"; tailscale funnel status; exit 1
  fi
  MODE=tailscale
  tailscale funnel "$PORT" >"$LOG" 2>&1 &
  PIDS+=($!)
  URL="https://$HOST"
fi
if [ -z "$URL" ]; then
  echo "공개 터널을 만들 수 없습니다. 다음 중 하나를 준비하세요:"
  echo "  - brew install cloudflared            (기본, 계정 불필요)"
  echo "  - Tailscale 로그인 + 관리 콘솔에서 Funnel 허용 (TUNNEL=tailscale)"
  [ -s "$LOG" ] && tail -5 "$LOG"
  exit 1
fi
echo "$URL" > "$DATA_DIR/public_url"

# 휴대폰이 보는 경로(공용 DNS → 인터넷)로 실제 응답하는지 확인. 새 주소는 DNS 반영에 수십 초 걸릴 수 있다.
# (이 PC의 DNS 는 '없는 주소'를 잠시 기억하거나 tailnet 내부 경로를 쓰므로 공용 DNS 로 직접 조회)
HOST_ONLY="${URL#https://}"; HOST_ONLY="${HOST_ONLY%%/*}"
printf "· 외부 접속 확인 중"
OK=""
for _ in $(seq 30); do
  if command -v dig >/dev/null 2>&1; then
    IP="$(dig +short @1.1.1.1 "$HOST_ONLY" A 2>/dev/null | grep -E '^[0-9.]+$' | head -1 || true)"
    [ -n "$IP" ] && curl -fs --max-time 4 --resolve "$HOST_ONLY:443:$IP" "$URL/healthz" >/dev/null 2>&1 && { OK=1; break; }
  else
    curl -fs --max-time 4 "$URL/healthz" >/dev/null 2>&1 && { OK=1; break; }
  fi
  printf "."; sleep 2
done
[ -n "$OK" ] && echo " 정상" || echo " 아직 응답 없음(잠시 후 다시 열어보세요)"

cat <<EOF

  ┌─────────────────────────────────────────────────────────────
  │ 공개 주소   $URL
  │ 업로드 코드 $CODE
  │ 방식        $MODE $( [ "$MODE" = cloudflare ] && echo "(임시: 재시작하면 주소와 이전 링크가 바뀜)" )
  └─────────────────────────────────────────────────────────────
  이 주소에서 도면을 올리면 외부망 휴대폰에서도 열리는 링크가 만들어집니다.
  (이 PC의 http://localhost:$PORT 에서 올려도 링크는 공개 주소로 발급)

EOF
"$PY" -c "import segno, sys; segno.make(sys.argv[1], error='m').terminal(compact=True)" "$URL" || true
echo "  ↑ 휴대폰 카메라로 스캔하면 업로드 화면이 열립니다.  종료: Ctrl+C"

wait "${PIDS[@]}"
