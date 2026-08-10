#!/usr/bin/env bash
set -u

APP_DIR="/home/botadmin/bot_administrator/BOT"
FAIL_FILE="$APP_DIR/data/watchdog_polling_failures"
LOG_FILE="$APP_DIR/data/watchdog_polling.log"
MAX_FAILS=3

cd "$APP_DIR" || exit 1

ts() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

result=$(docker compose exec -T bot sh -lc 'python - <<"PY"
import os
import urllib.error
import urllib.request

token = os.environ.get("BOT_TOKEN")
url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=10"

try:
    urllib.request.urlopen(url, timeout=15).read()
    print("POLLING_NOT_HELD")
except urllib.error.HTTPError as e:
    if e.code == 409:
        print("POLLING_HEALTHY")
    else:
        print(f"HTTP_ERROR_{e.code}")
except Exception as e:
    print(f"CHECK_ERROR_{type(e).__name__}")
PY')

case "$result" in
  *POLLING_HEALTHY*)
    echo 0 > "$FAIL_FILE"
    echo "$(ts) healthy result=$result" >> "$LOG_FILE"
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
  echo "$(ts) restarting bot after $fails failed polling checks" >> "$LOG_FILE"
  docker compose restart bot >> "$LOG_FILE" 2>&1
  echo 0 > "$FAIL_FILE"
fi
