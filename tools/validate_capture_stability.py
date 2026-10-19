#!/usr/bin/env python3
"""Two-minute read-only monitoring of valid JPEGs and encoder recovery counters."""
import json,time,urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
def state():
    with urllib.request.urlopen('http://127.0.0.1:8096/api/state',timeout=4) as response:return json.load(response)
def summary(s):
    return dict(all_online=s['all_online'],mode=s['mode'],channels=[dict(camera=c['camera'],
       captured_fps=c['source'].get('captured_fps',0),frames=c['source'].get('frames',0),
       received=c['source'].get('received_frames',0),incomplete=c['source'].get('incomplete_frames',0),
       bad_headers=c['source'].get('bad_headers',0),encoded_frames=c['transcode']['frames'],
       encoded_fps=c['transcode']['encoded_fps'],restarts=c['transcode']['restarts'],error=c['transcode']['error']) for c in s['cameras']])
def main():
    samples=[];began=time.monotonic()
    for i in range(25):
        samples.append(summary(state()))
        if i<24:time.sleep(5)
    elapsed=time.monotonic()-began;before=samples[0]['channels'];after=samples[-1]['channels']
    deltas=[dict(camera=b['camera'],valid_frames=b['frames']-a['frames'],incomplete=b['incomplete']-a['incomplete'],
                 bad_headers=b['bad_headers']-a['bad_headers'],encoded_frames=b['encoded_frames']-a['encoded_frames'],
                 restarts=b['restarts']-a['restarts']) for a,b in zip(before,after)]
    passed=all(s['all_online'] for s in samples) and all(d['valid_frames']>elapsed*28 and d['encoded_frames']>elapsed*27 and
               d['incomplete']==0 and d['bad_headers']==0 and d['restarts']==0 for d in deltas)
    report=dict(scope='Two-minute live capture and encoding counter observation; not long-run reliability acceptance',
                duration_seconds=elapsed,deltas=deltas,passed=passed,samples=samples)
    path=BASE/'run/validation/capture-stability.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2),flush=True)
    if not passed:raise SystemExit(1)
if __name__=='__main__':main()
