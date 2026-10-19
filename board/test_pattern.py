#!/usr/bin/env python3
"""Explicit synthetic camera input for bench tests; never auto-selected."""
import json
import os
import signal
import threading
import time
from pathlib import Path
import gi
gi.require_version('Gst','1.0')
from gi.repository import Gst
Gst.init(None)
PLAYING,NULL=Gst.State.PLAYING,Gst.State.NULL
BASE=Path(__file__).resolve().parents[1]
STOP=threading.Event()

def run(index,config):
    directory=BASE/'run/frames'
    pipeline=Gst.parse_launch(
        'videotestsrc is-live=true pattern=0 '
        f'! video/x-raw,format=YUY2,width={config["width"]},height={config["height"]},framerate={config["fps"]}/1 '
        '! jpegenc quality=80 ! appsink name=jpeg sync=false emit-signals=false max-buffers=1 drop=true')
    sink=pipeline.get_by_name('jpeg')
    pipeline.set_state(PLAYING)
    count=total=0;previous_count=previous_total=0;previous=time.monotonic()
    try:
        while not STOP.is_set():
            sample=sink.emit('try-pull-sample',Gst.SECOND)
            if sample is None:continue
            buffer=sample.get_buffer();data=buffer.extract_dup(0,buffer.get_size())
            temporary=directory/f'cam{index}.tmp'
            temporary.write_bytes(data);temporary.replace(directory/f'cam{index}.jpg')
            count+=1;total+=len(data)
            now=time.monotonic()
            if now-previous>=1:
                elapsed=now-previous
                source=dict(online=True,resolution=dict(width=config['width'],height=config['height']),desired_fps=config['fps'],
                            captured_fps=round((count-previous_count)/elapsed,2),captured_mbps=round((total-previous_total)*8/elapsed/1e6,3),
                            frames=count,bytes=total,bad_headers=0,payload_limit=config['payload_limit'],synthetic=True)
                value=dict(ok=True,result=dict(source=source,configuration=dict(status='idle')))
                temporary=directory/f'cam{index}-state.tmp'
                temporary.write_text(json.dumps(value));temporary.replace(directory/f'cam{index}-state.json')
                previous=now;previous_count=count;previous_total=total
    finally:pipeline.set_state(NULL)

def main():
    signal.signal(signal.SIGTERM,lambda *_:STOP.set())
    signal.signal(signal.SIGINT,lambda *_:STOP.set())
    config=json.loads((BASE/'config/g30.json').read_text())
    threads=[]
    for i in range(6):
        thread=threading.Thread(target=run,args=(i,config),daemon=True);thread.start();threads.append(thread)
    print('SYNTHETIC TEST INPUT: six GStreamer test patterns, not cameras',flush=True)
    STOP.wait()
    for thread in threads:thread.join(2)

if __name__=='__main__':main()
