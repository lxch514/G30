#!/usr/bin/env bash
set -euo pipefail
BASE=$(cd -- "$(dirname -- "$0")/.." && pwd)
test "$(id -u)" = 0 || { echo 'Run as root'; exit 1; }
if test -f "$BASE/run/capture-supervisor.pid"; then
  PID=$(cat "$BASE/run/capture-supervisor.pid")
  if kill -0 "$PID" 2>/dev/null; then echo 'Stop G30 first: bash scripts/stop.sh';exit 1;fi
fi
mkdir -p "$BASE/board/root" "$BASE/run" "$BASE/logs"
for deb in "$BASE"/board/debs/*.deb; do dpkg-deb -x "$deb" "$BASE/board/root"; done
gcc -O2 -Wall -Wextra -I"$BASE/board/root/usr/include" "$BASE/board/uvc_cap_test.c" \
  -L"$BASE/board/root/usr/lib/aarch64-linux-gnu" \
  -Wl,-rpath,"$BASE/board/root/usr/lib/aarch64-linux-gnu" \
  -luvc -l:libusb-1.0.so.0 -lpthread -o "$BASE/board/uvc_cap_test"
/usr/bin/python3 -m compileall -q "$BASE/board"
gcc -std=c99 -Wall -Wextra -Werror "$BASE/board/tests/test_jpeg_frame.c" -o "$BASE/run/test-jpeg-frame"
"$BASE/run/test-jpeg-frame"
/usr/bin/python3 - <<'PY'
import gi
gi.require_version('Gst','1.0')
from gi.repository import Gst
Gst.init(None)
for name in ('appsrc','appsink','jpegparse','mppjpegdec','mpph264enc','h264parse'):
    if Gst.ElementFactory.find(name) is None:raise SystemExit('Missing GStreamer plugin: '+name)
print('Rockchip H264 hardware plugins available')
PY
echo "Setup complete: bash $BASE/scripts/start.sh"
