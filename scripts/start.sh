#!/usr/bin/env bash
set -euo pipefail
BASE=$(cd -- "$(dirname -- "$0")/.." && pwd)
test "$(id -u)" = 0 || { echo 'Run as root';exit 1; }
test -x "$BASE/board/uvc_cap_test" || { echo 'Run scripts/setup.sh first';exit 1; }
MODE=${1:-cameras}
case "$MODE" in cameras|--test-pattern) ;;*) echo 'Usage: start.sh [--test-pattern]';exit 1;;esac
mkdir -p "$BASE/run" "$BASE/logs"
exec 9>"$BASE/run/start.lock"
flock -n 9 || { echo 'Another G30 start is running';exit 1; }
bash "$BASE/scripts/stop.sh"
mkdir -p "$BASE/run/frames"
mount -t tmpfs -o size=256m,mode=0700 g30-frames "$BASE/run/frames"
cleanup(){ bash "$BASE/scripts/stop.sh" || true; }
trap 'STATUS=$?; if test "$STATUS" -ne 0;then cleanup;fi' EXIT
if test "$MODE" = --test-pattern;then
  touch "$BASE/run/test-pattern.flag"
  nohup /usr/bin/python3 "$BASE/board/test_pattern.py" < /dev/null > "$BASE/logs/capture.log" 2>&1 9>&- &
  printf '%s\n' "$!" > "$BASE/run/test-pattern.pid"
else
  bash "$BASE/scripts/prepare-usb-host.sh"
  USBFS=/sys/module/usbcore/parameters/usbfs_memory_mb
  CURRENT=$(cat "$USBFS")
  if test "$CURRENT" -ne 0 && test "$CURRENT" -lt 64;then printf '64\n' > "$USBFS";fi
  nohup /usr/bin/python3 "$BASE/board/capture_supervisor.py" < /dev/null > "$BASE/logs/capture.log" 2>&1 9>&- &
  printf '%s\n' "$!" > "$BASE/run/capture-supervisor.pid"
fi
nohup /usr/bin/python3 "$BASE/board/g30_service.py" < /dev/null > "$BASE/logs/service.log" 2>&1 9>&- &
printf '%s\n' "$!" > "$BASE/run/service.pid"
PORT=$(/usr/bin/python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["http_port"])' "$BASE/config/g30.json")
for attempt in $(seq 1 40);do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" > "$BASE/run/health.json" 2>/dev/null;then
    echo "G30 API ready: http://BOARD_IP:$PORT/api/state"
    echo 'Camera readiness is shown per channel; waiting cameras reconnect automatically.'
    test "$MODE" != --test-pattern || echo 'WARNING: explicitly using SYNTHETIC TEST PATTERNS'
    trap - EXIT;exit 0
  fi
  kill -0 "$(cat "$BASE/run/service.pid")" 2>/dev/null || { cat "$BASE/logs/service.log";exit 1; }
  sleep .5
done
echo 'G30 API startup timed out';exit 1
