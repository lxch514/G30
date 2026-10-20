#!/usr/bin/env bash
set -euo pipefail
BASE=$(cd -- "$(dirname -- "$0")/.." && pwd)
PIDFILE="$BASE/run/server.pid"
test -f "$PIDFILE" || { echo 'G30_mix is not running';exit 0; }
PID=$(cat "$PIDFILE")
case "$PID" in ''|*[!0-9]*) echo 'Invalid G30_mix PID file';exit 1;;esac
if test -r "/proc/$PID/cmdline";then
  COMMAND=$(tr '\0' ' ' < "/proc/$PID/cmdline")
  case "$COMMAND" in *"$BASE/server.py"*) ;;*) echo 'Unrelated process left untouched';exit 1;;esac
  kill -TERM "$PID"
  for attempt in $(seq 1 40);do
    test ! -r "/proc/$PID/stat" && break
    test "$(awk '{print $3}' "/proc/$PID/stat" 2>/dev/null || true)" = Z && break
    sleep .2
  done
  # The process can disappear between checking /proc and reading its state.
  FINAL_STATE=$(awk '{print $3}' "/proc/$PID/stat" 2>/dev/null || true)
  if test -n "$FINAL_STATE" && test "$FINAL_STATE" != Z;then
    echo 'G30_mix did not exit; inspect before retrying';exit 1
  fi
fi
rm -f -- "$PIDFILE"
echo 'G30_mix stopped'
