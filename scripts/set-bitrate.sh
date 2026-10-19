#!/usr/bin/env bash
set -euo pipefail
BASE=$(cd -- "$(dirname -- "$0")/.." && pwd)
case "${1:-}" in 2|6|8) ;; *) echo 'Usage: set-bitrate.sh 2|6|8 (Mbps per camera)';exit 1;;esac
/usr/bin/python3 - "$BASE/config/g30.json" "$1" <<'PY'
import json,sys
from pathlib import Path
path=Path(sys.argv[1]);value=json.loads(path.read_text())
value['bitrate']=int(sys.argv[2])*1000000
temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)
print('Configured bitrate:',value['bitrate'],'bps per camera')
PY
if test -f "$BASE/run/service.pid";then
  PID=$(cat "$BASE/run/service.pid")
  if test -r "/proc/$PID/cmdline";then
    CMD=$(tr '\0' ' ' < "/proc/$PID/cmdline")
    case "$CMD" in *"$BASE/board/g30_service.py"*) kill -TERM "$PID";;*) echo 'Unrelated PID; stop manually';exit 1;;esac
    for attempt in $(seq 1 60);do
      test ! -r "/proc/$PID/stat" && break
      test "$(awk '{print $3}' "/proc/$PID/stat" 2>/dev/null || true)" = Z && break
      sleep .2
    done
    if test -r "/proc/$PID/stat" && test "$(awk '{print $3}' "/proc/$PID/stat")" != Z;then echo 'Service did not stop';exit 1;fi
  fi
  nohup /usr/bin/python3 "$BASE/board/g30_service.py" < /dev/null > "$BASE/logs/service.log" 2>&1 &
  printf '%s\n' "$!" > "$BASE/run/service.pid"
  echo 'G30 H264 service restarted; capture and snappy-real preserved'
fi
