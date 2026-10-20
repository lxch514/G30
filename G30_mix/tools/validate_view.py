#!/usr/bin/env python3
"""Read-only HTTP verification of the current view and original six channels."""
import json
import statistics
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8097'


def state(port=8097):
    with urllib.request.urlopen('http://127.0.0.1:%d/api/state' % port, timeout=5) as r:
        return json.load(r)


def main():
    cv2.setNumThreads(1)
    before = state(); assert before['running'] and before['lens_calibrated'] is False
    shapes = []; sizes = []; times = []
    with urllib.request.urlopen(URL+'/live.mjpg', timeout=5) as stream:
        for _ in range(20):
            assert stream.readline().strip() == b'--frame'
            headers = {}
            while True:
                line = stream.readline()
                if line == b'\r\n': break
                key, value = line.decode().strip().split(':', 1); headers[key.lower()] = value.strip()
            size = int(headers['content-length']); assert 0 < size < 4*1024*1024
            data = stream.read(size); assert len(data) == size and stream.read(2) == b'\r\n'
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert image is not None
            shapes.append(list(image.shape[1::-1])); sizes.append(size); times.append(time.monotonic())
    samples = []; began = time.monotonic()
    for index in range(16):
        delay = began+index-time.monotonic()
        if delay > 0: time.sleep(delay)
        mix, original = state(), state(8096)
        assert mix['running'], mix
        assert original['all_online'], original
        channels = [dict(camera=c['camera'], source_fps=c['source']['captured_fps'],
                         encoded_fps=c['transcode']['encoded_fps'], incomplete=c['source'].get('incomplete_frames'),
                         restarts=c['transcode'].get('restarts')) for c in original['cameras']]
        samples.append(dict(elapsed=round(time.monotonic()-began, 3), mix=mix, original=channels))
    after = samples[-1]['mix']
    with urllib.request.urlopen(URL+'/snapshot.jpg', timeout=5) as r:
        (BASE/'samples/level-input/verified-live.jpg').write_bytes(r.read())
    report = dict(test='view correction HTTP validation', seconds=time.monotonic()-began,
                  decoded_preview_frames=20, decoded_sizes=shapes, before_view=before['view'], after_view=after['view'],
                  settings_unchanged=before['view']==after['view'], lens_calibrated=False,
                  output_fps_median=statistics.median(s['mix']['output_fps'] for s in samples),
                  processing_ms_median=statistics.median(s['mix']['processing_ms'] for s in samples),
                  one_viewer_jpeg_mbps=sum(sizes[1:])*8/(times[-1]-times[0])/1e6,
                  original_all_online_all_samples=True, samples=samples, full_power_cycle_tested=False)
    (BASE/'docs/view-live-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},ensure_ascii=False))


if __name__ == '__main__':
    main()
