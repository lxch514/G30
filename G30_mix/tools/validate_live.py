#!/usr/bin/env python3
"""Bounded integration observation; never restarts or writes the original G30."""
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8097'


def get(path):
    with urllib.request.urlopen(URL + path, timeout=5) as response:
        return response.read()


def state():
    return json.loads(get('/api/state'))


def post(path, body):
    request = urllib.request.Request(URL + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def original():
    with urllib.request.urlopen('http://127.0.0.1:8096/api/state', timeout=5) as response:
        data = json.load(response)
    return [dict(camera=c['camera'], source_online=c['source']['online'], transcode_online=c['transcode']['online'],
                 source_fps=c['source'].get('captured_fps'), encoded_fps=c['transcode'].get('encoded_fps'),
                 incomplete=c['source'].get('incomplete_frames'), restarts=c['transcode'].get('restarts'))
            for c in data['cameras']]


def process(pid):
    raw = Path('/proc/%d/stat' % pid).read_text().split(') ', 1)[1].split()
    ticks = int(raw[11]) + int(raw[12])
    rss = next(line for line in Path('/proc/%d/status' % pid).read_text().splitlines() if line.startswith('VmRSS:'))
    return ticks, int(rss.split()[1])


def main():
    cv2.setNumThreads(1)
    start = state(); assert start['running'], start
    before = hashlib.sha256((BASE / 'config/calibration.json').read_bytes()).hexdigest()
    frozen = post('/api/capture', {})
    try:
        post('/api/manual', dict(token=frozen['token'], points3=[[.5, .5]] * 4, points4=[[.5, .5]] * 4))
        raise AssertionError('Degenerate manual calibration was accepted')
    except urllib.error.HTTPError as error:
        assert error.code == 400; rejected_message = error.read().decode()
    assert before == hashlib.sha256((BASE / 'config/calibration.json').read_bytes()).hexdigest()
    jpeg = get('/snapshot.jpg'); image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and list(image.shape[1::-1]) == start['output_size']
    (BASE / 'samples/live-verified.jpg').write_bytes(jpeg)
    frames = []; received = 0
    with urllib.request.urlopen(URL + '/live.mjpg', timeout=5) as stream:
        while len(frames) < 20:
            boundary = stream.readline()
            assert boundary.strip() == b'--frame', boundary
            headers = {}
            while True:
                line = stream.readline()
                if line == b'\r\n': break
                key, value = line.decode().strip().split(':', 1); headers[key.lower()] = value.strip()
            length = int(headers['content-length']); assert 0 < length < 4 * 1024 * 1024
            data = stream.read(length); assert len(data) == length
            assert stream.read(2) == b'\r\n'
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert image is not None and list(image.shape[1::-1]) == start['output_size']
            frames.append(time.monotonic()); received += length
    stream_result = dict(decoded_frames=len(frames), seconds=frames[-1]-frames[0],
                         fps=(len(frames)-1)/(frames[-1]-frames[0]), payload_bytes=received)
    samples = []; began = time.monotonic(); ticks, _ = process(start['pid'])
    for i in range(31):
        target = began + i
        if time.monotonic() < target: time.sleep(target - time.monotonic())
        item = dict(elapsed=round(time.monotonic()-began, 3), mix=state(), original=original())
        samples.append(item)
        assert item['mix']['running'], item['mix']
        assert all(c['source_online'] and c['transcode_online'] for c in item['original']), item['original']
    elapsed = time.monotonic()-began; final_ticks, rss = process(start['pid'])
    report = dict(test='camera3+camera4 live integration', observe_seconds=elapsed,
                  invalid_manual_rejected=True, rejection=rejected_message, accepted_calibration_unchanged=True,
                  multipart_tcp=stream_result, mix_fps=dict(min=min(s['mix']['output_fps'] for s in samples),
                    median=statistics.median(s['mix']['output_fps'] for s in samples), max=max(s['mix']['output_fps'] for s in samples)),
                  processing_ms_median=statistics.median(s['mix']['processing_ms'] for s in samples),
                  mix_cpu_percent_one_core=(final_ticks-ticks)/os.sysconf('SC_CLK_TCK')/elapsed*100,
                  mix_rss_kib=rss, six_original_channels_online_all_samples=True,
                  samples=samples, optical_latency_measured=False, power_cycle_tested=False)
    (BASE / 'docs').mkdir(exist_ok=True)
    (BASE / 'docs/live-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    summary = {k:v for k,v in report.items() if k!='samples'}
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__':
    main()
