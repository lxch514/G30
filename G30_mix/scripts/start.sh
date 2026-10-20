#!/usr/bin/env bash
set -euo pipefail
BASE=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$BASE/run" "$BASE/logs"
exec 9>"$BASE/run/start.lock"
flock -n 9 || { echo 'Another G30_mix start is active';exit 1; }
if test -f "$BASE/run/server.pid";then
  PID=$(cat "$BASE/run/server.pid")
  case "$PID" in ''|*[!0-9]*) echo 'Invalid G30_mix PID file';exit 1;;esac
  if test -r "/proc/$PID/cmdline";then
    COMMAND=$(tr '\0' ' ' < "/proc/$PID/cmdline")
    case "$COMMAND" in *"$BASE/server.py"*) echo "G30_mix already running: $PID";exit 0;;*) echo 'Stale PID; unrelated process left untouched';;esac
  fi
fi
PYTHON=${G30_MIX_PYTHON:-/usr/bin/python3}
"$PYTHON" -c 'import cv2,numpy; print("OpenCV",cv2.__version__,"NumPy",numpy.__version__)'
nohup "$PYTHON" "$BASE/server.py" < /dev/null > "$BASE/logs/server.log" 2>&1 9>&- &
PID=$!
printf '%s\n' "$PID" > "$BASE/run/server.pid"
PORT=$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["port"])' "$BASE/config/mix.json")
for attempt in $(seq 1 40);do
  kill -0 "$PID" 2>/dev/null || { cat "$BASE/logs/server.log";exit 1; }
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/state" 2>/dev/null | "$PYTHON" -c 'import json,sys;v=json.load(sys.stdin);sys.exit(0 if v.get("project")=="G30_mix" and v.get("pid")==int(sys.argv[1]) else 1)' "$PID" 2>/dev/null;then
    echo "G30_mix ready: http://BOARD_IP:$PORT/";exit 0
  fi
  sleep .2
done
echo 'G30_mix startup timed out; inspect logs/server.log';exit 1
