#!/usr/bin/env python3
"""Restart only G30's capture process after USB disconnect / stale frames."""
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
STOP=threading.Event()

def devices():
    matches=[]
    for device in Path('/sys/bus/usb/devices').glob('*'):
        try:
            if (device/'idVendor').read_text().strip()=='0bda' and (device/'idProduct').read_text().strip()=='2292':
                matches.append(device.name)
        except OSError:pass
    return sorted(matches)

def main():
    config=json.loads((BASE/'config/g30.json').read_text())
    frames=BASE/'run/frames'
    command=[str(BASE/'board/uvc_cap_test'),str(config['width']),str(config['height']),str(config['fps']),
             str(config['payload_limit']),'0','6',str(frames),'publish']
    payloads=config.get('payload_limits',[config['payload_limit']]*6)
    if not isinstance(payloads,list) or len(payloads)!=6 or any(type(p) is not int or not 128<=p<=3072 for p in payloads):
        raise ValueError('payload_limits must contain six integers in 128..3072')
    environment=dict(os.environ,G30_UVC_PAYLOADS=','.join(map(str,payloads)))
    signal.signal(signal.SIGTERM,lambda *_:STOP.set())
    signal.signal(signal.SIGINT,lambda *_:STOP.set())
    previous=None
    while not STOP.is_set():
        found=devices()
        if len(found)<6:
            if found!=previous:print(f'Waiting for six 0bda:2292 cameras: {len(found)}/6 {found}',flush=True)
            previous=found
            STOP.wait(2);continue
        print('Starting six UVC cameras: '+', '.join(found),flush=True)
        for pattern in ['cam*.jpg','cam*-state.json','cam*-formats.json','cam*-control.txt']:
            for file in frames.glob(pattern):file.unlink(missing_ok=True)
        process=subprocess.Popen(command,cwd=BASE,stdin=subprocess.DEVNULL,env=environment)
        started=time.monotonic()
        try:
            while not STOP.wait(1):
                if process.poll() is not None:
                    print(f'Capture exited: {process.returncode}; retrying',flush=True);break
                if time.monotonic()-started<8:continue
                try:
                    live=all(time.time()-(frames/f'cam{i}.jpg').stat().st_mtime<5 for i in range(6))
                except OSError:live=False
                if not live:
                    print('One or more cameras stale: re-enumerating all six',flush=True);break
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        previous=None
        STOP.wait(3)

if __name__=='__main__':main()
