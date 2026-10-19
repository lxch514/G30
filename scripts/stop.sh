#!/usr/bin/env bash
set -euo pipefail
BASE=$(cd -- "$(dirname -- "$0")/.." && pwd)
for kind in service capture-supervisor test-pattern; do
  PIDFILE="$BASE/run/$kind.pid"
  test -f "$PIDFILE" || continue
  PID=$(cat "$PIDFILE")
  case "$PID" in ''|*[!0-9]*) echo "Invalid PID: $PIDFILE";exit 1;;esac
  if test -r "/proc/$PID/cmdline";then
    CMD=$(tr '\0' ' ' < "/proc/$PID/cmdline")
    case "$CMD" in *"$BASE/board/"*) ;; *) echo "Unrelated PID $PID left untouched";exit 1;;esac
    kill -TERM "$PID" 2>/dev/null || true
    for attempt in $(seq 1 60);do
      test ! -r "/proc/$PID/stat" && break
      test "$(awk '{print $3}' "/proc/$PID/stat" 2>/dev/null || true)" = Z && break
      sleep .2
    done
    if test -r "/proc/$PID/stat" && test "$(awk '{print $3}' "/proc/$PID/stat")" != Z;then
      echo "G30 process $PID did not exit; inspect before unmounting";exit 1
    fi
  fi
  rm -f -- "$PIDFILE"
done
rm -f -- "$BASE/run/test-pattern.flag"
if mountpoint -q "$BASE/run/frames";then
  SOURCE=$(findmnt -rn -o SOURCE -T "$BASE/run/frames")
  TYPE=$(findmnt -rn -o FSTYPE -T "$BASE/run/frames")
  test "$SOURCE:$TYPE" = 'g30-frames:tmpfs' || { echo 'Unrelated frame mount left untouched';exit 1; }
  umount "$BASE/run/frames"
fi
echo 'G30 stopped; snappy-real untouched'
