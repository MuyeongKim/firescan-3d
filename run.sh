#!/usr/bin/env bash
# 로컬 실행: 같은 Wi-Fi 의 휴대폰/태블릿에서 http://<이 PC IP>:8000 으로 접속
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
