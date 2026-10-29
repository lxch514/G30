#!/usr/bin/env python3
"""Read-only, bounded live preview and original six-channel observation."""
import argparse
import hashlib
import json
import statistics
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parents[1]


def fetch(base, path):
    with urllib.request.urlopen(base + path, timeout=8) as response:
        return response.read()


def run(base, seconds, report_path):
    state = lambda: json.loads(fetch(base, '/api/state'))
    start = state()
    assert start['running'], start
    project_before = hashlib.sha256((BASE/'config/project.json').read_bytes()).hexdigest()
    decoded = []; payload = 0
    with urllib.request.urlopen(base + '/live.mjpg', timeout=8) as stream:
        while len(decoded) < 20:
            assert stream.readline().strip() == b'--frame'
            headers = {}
            while True:
                line = stream.readline()
                if line == b'\r\n': break
                key, value = line.decode().strip().split(':', 1)
                headers[key.lower()] = value.strip()
            length = int(headers['content-length'])
            assert 0 < length < 8*1024*1024
            data = stream.read(length)
            assert len(data) == length and stream.read(2) == b'\r\n'
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert image is not None
            decoded.append(time.monotonic()); payload += length
    samples = []; began = time.monotonic()
    for i in range(seconds+1):
        time.sleep(max(0, began+i-time.monotonic()))
        mix = state()
        original = json.loads(fetch('http://127.0.0.1:8096', '/api/state'))
        channels = [dict(camera=c['camera'], source_online=c['source']['online'],
                         transcode_online=c['transcode']['online'], source_fps=c['source'].get('captured_fps'),
                         encoded_fps=c['transcode'].get('encoded_fps'), incomplete=c['source'].get('incomplete_frames'),
                         restarts=c['transcode'].get('restarts')) for c in original['cameras']]
        samples.append(dict(elapsed=time.monotonic()-began, mix=mix, original=channels))
        assert mix['running'], mix
        assert all(c['source_online'] and c['transcode_online'] for c in channels), channels
    counters = ['incomplete', 'restarts']
    deltas = [{key: samples[-1]['original'][c][key]-samples[0]['original'][c][key]
               for key in counters} for c in range(6)]
    duration = decoded[-1]-decoded[0]
    report = dict(test='multi-camera live TCP preview', cameras=start['cameras'],
                  decoded_frames=len(decoded), preview_fps=(len(decoded)-1)/duration,
                  preview_payload_mbps=payload*8/(duration+duration/(len(decoded)-1))/1e6,
                  output_fps_median=statistics.median(s['mix']['output_fps'] for s in samples),
                  processing_ms_median=statistics.median(s['mix']['processing_ms'] for s in samples),
                  original_six_online_all_samples=True, original_counter_deltas=deltas,
                  project_file_unchanged=project_before==hashlib.sha256((BASE/'config/project.json').read_bytes()).hexdigest(),
                  optical_latency_measured=False, power_cycle_tested=False, samples=samples)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='samples'}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8097')
    parser.add_argument('--seconds', type=int, default=15, choices=range(3, 61))
    args = parser.parse_args()
    cv2.setNumThreads(1)
    run(args.url.rstrip('/'), args.seconds, BASE/'docs/multicam-live-validation.json')


if __name__ == '__main__': main()
