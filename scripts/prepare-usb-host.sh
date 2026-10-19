#!/usr/bin/env bash
# TQ3588's two Type-C DWC3 controllers boot as devices on this image.
# Runtime role only: no device-tree, boot service or persistent config changes.
set -euo pipefail
for controller in fc000000.usb fc400000.usb;do
  MODE=/sys/kernel/debug/usb/$controller/mode
  test -r "$MODE" || { echo "Missing TQ3588 USB role interface: $MODE";exit 1; }
  CURRENT=$(cat "$MODE")
  if test "$CURRENT" != host;then
    STATE=/sys/class/udc/$controller/state
    if test -r "$STATE" && test "$(cat "$STATE")" != 'not attached';then
      echo "$controller has an active USB device connection; cannot use it for camera Hub";exit 1
    fi
    printf 'host\n' > "$MODE"
    for attempt in $(seq 1 30);do
      test "$(cat "$MODE")" != host || break
      sleep .1
    done
    test "$(cat "$MODE")" = host || { echo "$controller did not enter Host mode";exit 1; }
  fi
  echo "$controller: Host mode for camera Hub"
done
