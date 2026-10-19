#!/usr/bin/env python3
"""Six FFmpeg RTP receivers against the running real/synthetic G30 service.

Writes only under project run/validation; no camera pictures/videos are saved.
Each receiver decodes 180 frames to the null muxer. It never touches snappy-real.
"""
import json
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
OUT=BASE/'run/validation'
API='http://127.0.0.1:8096'

def request(path,data=None,method=None):
    req=urllib.request.Request(API+path,data=json.dumps(data).encode() if data is not None else None,
                               headers={'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(req,timeout=4) as response:return json.load(response)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    ports=[15700+i*2 for i in range(6)]
    token='g30-ffmpeg-'+uuid.uuid4().hex
    processes=[];handles=[]
    before=request('/api/state')
    start=time.monotonic()
    try:
        for i,port in enumerate(ports):
            sdp=OUT/f'cam{i}.sdp'
            sdp.write_text(f'v=0\no=- 0 0 IN IP4 127.0.0.1\ns=G30 camera{i}\nc=IN IP4 127.0.0.1\nt=0 0\nm=video {port} RTP/AVP 96\na=rtpmap:96 H264/90000\na=fmtp:96 packetization-mode=1\n')
            progress=OUT/f'cam{i}-progress.txt';progress.unlink(missing_ok=True)
            log=(OUT/f'cam{i}-decode.log').open('w');handles.append(log)
            command=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-protocol_whitelist','file,udp,rtp',
                     '-analyzeduration','1000000','-probesize','1000000','-i',str(sdp),'-map','0:v:0',
                     '-frames:v','180','-an','-progress',str(progress),'-f','null','-']
            processes.append(subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log))
        time.sleep(.6)
        deadline=time.monotonic()+35
        while time.monotonic()<deadline and any(p.poll() is None for p in processes):
            request('/api/session',dict(id=token,ports=ports))
            time.sleep(1)
        after=request('/api/state')
        duration=time.monotonic()-start
        results=[]
        for i,process in enumerate(processes):
            if process.poll() is None:process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            handles[i].close()
            progress=(OUT/f'cam{i}-progress.txt').read_text() if (OUT/f'cam{i}-progress.txt').exists() else ''
            values=dict(line.split('=',1) for line in progress.splitlines() if '=' in line)
            error=(OUT/f'cam{i}-decode.log').read_text()
            previous=before['cameras'][i]['transcode'];last=after['cameras'][i]['transcode']
            result=dict(camera=i,decoded_frames=int(values.get('frame','0')),progress=values.get('progress'),
                        exit_code=process.returncode,errors=error,
                        encoded_fps_delta=(last['frames']-previous['frames'])/duration,
                        encoded_mbps_delta=(last['bytes']-previous['bytes'])*8/duration/1e6,
                        restarts_delta=last['restarts']-previous['restarts'],
                        source_online=after['cameras'][i]['source']['online'])
            result['passed']=result['decoded_frames']==180 and result['progress']=='end' and process.returncode==0 and not error
            results.append(result)
        report=dict(scope='Six simultaneous RTP/H264 receivers decoded by FFmpeg, not G30 display acceptance',
                    test_pattern=after['test_pattern'],duration_seconds=duration,
                    bitrate_per_camera=after['bitrate_per_camera'],mode=after['mode'],
                    results=results,passed=all(r['passed'] for r in results),before=before,after=after)
        (OUT/'live-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in ('before','after')},ensure_ascii=False,indent=2),flush=True)
        if not report['passed']:raise SystemExit(1)
    finally:
        try:request('/api/session/'+token,method='DELETE')
        except Exception:pass
        for process in processes:
            if process.poll() is None:process.terminate()
        for handle in handles:
            if not handle.closed:handle.close()

if __name__=='__main__':main()
