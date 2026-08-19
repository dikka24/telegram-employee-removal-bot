#!/usr/bin/env bash
set -u

APP_DIR="/home/botadmin/bot_administrator/BOT"
FAIL_FILE="$APP_DIR/data/watchdog_polling_failures"
LOG_FILE="$APP_DIR/data/watchdog_polling.log"
HEARTBEAT_FILE="$APP_DIR/data/bot_heartbeat"
HEARTBEAT_MAX_AGE=180
MAX_FAILS=3

cd "$APP_DIR" || exit 1

ts() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

restart_bot() {
  local reason="$1"
  echo "$(ts) restarting bot reason=$reason" >> "$LOG_FILE"
  docker compose restart bot >> "$LOG_FILE" 2>&1
  echo 0 > "$FAIL_FILE"
}

now_epoch=$(date +%s)
heartbeat_epoch=0
if [ -f "$HEARTBEAT_FILE" ]; then
  heartbeat_epoch=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
fi
case "$heartbeat_epoch" in
  ''|*[!0-9]*) heartbeat_epoch=0 ;;
esac
heartbeat_age=$((now_epoch - heartbeat_epoch))

if [ "$heartbeat_epoch" -eq 0 ] || [ "$heartbeat_age" -gt "$HEARTBEAT_MAX_AGE" ]; then
  echo "$(ts) unhealthy stale_heartbeat age=${heartbeat_age}s" >> "$LOG_FILE"
  restart_bot "stale_heartbeat_${heartbeat_age}s"
  exit 0
fi

result=$(docker compose exec -T bot sh -lc 'python - <<"PY"
import json
import os
import urllib.request

token = os.environ.get("BOT_TOKEN")
if not token:
    print("TOKEN_MISSING")
    raise SystemExit

url = f"https://api.telegram.org/bot{token}/getMe"

try:
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = json.load(response)
    print("BOT_HEALTHY" if payload.get("ok") else "API_NOT_OK")
except Exception as e:
    print(f"CHECK_ERROR_{type(e).__name__}")
PY')

case "$result" in
  *BOT_HEALTHY*)
    echo 0 > "$FAIL_FILE"
    echo "$(ts) healthy heartbeat_age=${heartbeat_age}s result=$result" >> "$LOG_FILE"
    exit 0
    ;;
esac

fails=0
if [ -f "$FAIL_FILE" ]; then
  fails=$(cat "$FAIL_FILE" 2>/dev/null || echo 0)
fi
case "$fails" in
  ''|*[!0-9]*) fails=0 ;;
esac

fails=$((fails + 1))
echo "$fails" > "$FAIL_FILE"
echo "$(ts) unhealthy fails=$fails result=$result" >> "$LOG_FILE"

if [ "$fails" -ge "$MAX_FAILS" ]; then
  restart_bot "api_health_failed_${fails}_times"
fi
