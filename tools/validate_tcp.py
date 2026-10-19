#!/usr/bin/env python3
"""Receive six real channels on ONE TCP connection, then verify each with FFmpeg.
No G30 installation/touch/decoder-capacity acceptance is implied.
Temporary H264 clips are deleted on exit; only the JSON report is retained.
"""
import json
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
OUT=BASE/'run/validation'
API='http://127.0.0.1:8096'

def exact(stream,size):
    parts=[]
    while size:
        data=stream.read(size)
        if not data:raise EOFError('TCP feed ended mid-packet')
        parts.append(data);size-=len(data)
    return b''.join(parts)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(API+'/api/state',timeout=5) as response:state=json.load(response)
    if state['test_pattern'] or not state['all_online']:
        raise SystemExit('Six REAL cameras must be online before TCP validation')
    counts=[0]*6;keyframes=[0]*6;sizes=[0]*6
    started=time.monotonic()
    results=[]
    with tempfile.TemporaryDirectory(prefix='tcp-',dir=OUT) as temporary:
        root=Path(temporary)
        handles=[(root/f'cam{i}.h264').open('wb') for i in range(6)]
        try:
            with urllib.request.urlopen(API+'/api/feed',timeout=10) as response:
                while min(counts)<300:
                    if time.monotonic()-started>25:raise TimeoutError('Six-channel TCP capture timed out')
                    camera,key,_,timestamp,size=struct.unpack('!BBHQI',exact(response,16))
                    if camera>=6 or not 4<=size<=4*1024*1024:raise ValueError('Invalid feed framing')
                    data=exact(response,size)
                    if counts[camera]>=300:continue
                    if counts[camera]==0 and not key:raise AssertionError('Missing initial keyframe')
                    handles[camera].write(data);counts[camera]+=1;keyframes[camera]+=bool(key);sizes[camera]+=size
        finally:
            for handle in handles:handle.close()
        capture_seconds=time.monotonic()-started
        for i in range(6):
            probe=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height',
                                  '-of','json',str(root/f'cam{i}.h264')],capture_output=True,text=True,timeout=10)
            dimensions=json.loads(probe.stdout)['streams'][0]
            process=subprocess.run(['ffmpeg','-nostdin','-v','error','-f','h264','-i',str(root/f'cam{i}.h264'),
                                    '-frames:v','300','-progress','pipe:1','-f','null','-'],capture_output=True,text=True,timeout=20)
            values=dict(line.split('=',1) for line in process.stdout.splitlines() if '=' in line)
            results.append(dict(camera=i,received_frames=counts[i],keyframes=keyframes[i],bytes=sizes[i],
                                decoded_frames=int(values.get('frame','0')),decoded_width=dimensions['width'],decoded_height=dimensions['height'],
                                exit_code=process.returncode,errors=process.stderr,probe_exit_code=probe.returncode,probe_errors=probe.stderr))
    report=dict(scope='Six real cameras received simultaneously over one local TCP connection; clips decoded by FFmpeg. Not G30 display acceptance.',
                test_pattern=False,bitrate_per_camera=state['bitrate_per_camera'],mode=state['mode'],capture_seconds=capture_seconds,
                results=results,passed=all(x['decoded_frames']==300 and x['exit_code']==0 and not x['errors'] and
                    x['probe_exit_code']==0 and not x['probe_errors'] and x['decoded_width']==state['mode']['width'] and
                    x['decoded_height']==state['mode']['height'] for x in results))
    (OUT/'tcp-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
