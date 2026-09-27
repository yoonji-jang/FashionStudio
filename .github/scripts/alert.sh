#!/usr/bin/env bash
# 워크플로 실패를 텔레그램으로 알린다. 환경 변수 TEXT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
# 파이썬 설치 전에 실패해도 보낼 수 있게 curl 만 쓴다. 토큰이 주소에 들어가서 출력은 버린다.
[ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ] || { echo "텔레그램 설정 없음"; exit 0; }
curl -sS -o /dev/null -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=⚠ ${TEXT}" || true
