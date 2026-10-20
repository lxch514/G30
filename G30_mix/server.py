#!/usr/bin/env python3
"""Independent camera3+camera4 experiment, read-only input and HTTP/TCP preview."""
import argparse
import collections
import json
import os
import signal
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import cv2

from core import Composer, StitchError, atomic_json, automatic, decode_pair, estimate_upright, manual, read_frame, read_pair, view_config

BASE = Path(__file__).resolve().parent


def load_config():
    config = json.loads((BASE / 'config/mix.json').read_text())
    for key, low, high in [('port', 1024, 65535), ('fps', 1, 20), ('max_width', 640, 1920),
                           ('max_height', 360, 1200), ('jpeg_quality', 30, 95),
                           ('blend_width_pixels', 0, 40), ('opencv_threads', 1, 4)]:
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError('Invalid configuration: ' + key)
    if not .1 <= config['max_input_age_seconds'] <= 3 or not 1 <= config['max_publication_skew_ms'] <= 150:
        raise ValueError('Invalid input freshness configuration')
    config['source_dir'] = str(Path(config['source_dir']).resolve())
    return config


class App:
    def __init__(self, config):
        self.config = config
        self.instance = uuid.uuid4().hex
        view_path = BASE / 'config/view.json'
        self.view = view_config(json.loads(view_path.read_text()) if view_path.exists() else {})
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.job = threading.Lock()
        self.stop = threading.Event()
        self.viewers = threading.BoundedSemaphore(4)
        self.engine = None
        self.calibration = None
        self.load_error = ''
        path = BASE / 'config/calibration.json'
        if path.exists():
            try:
                self.calibration = json.loads(path.read_text())
                if self.calibration.get('source_cameras') != [3, 4] or self.calibration.get('version') != 1:
                    raise ValueError('Unsupported calibration')
            except (OSError, ValueError, TypeError) as error:
                self.load_error = '标定文件无效：' + str(error)
                self.calibration = None
        self.raw = None
        self.frozen = None
        self.token = None
        self.jpeg = None
        self.sequence = 0
        self.output_at = 0.
        self.processing_ms = 0.
        self.error = self.load_error or '尚未标定，请点击自动对齐或手动选点'
        self.history = collections.deque(maxlen=120)
        self.skew_ms = None
        self.output_size = None

    def pair(self):
        return read_pair(self.config['source_dir'], self.config['max_input_age_seconds'],
                         self.config['max_publication_skew_ms'])

    def composer(self, calibration, images, view=None):
        return Composer(calibration, images, self.config['max_width'], self.config['max_height'],
                        self.config['blend_width_pixels'], self.view if view is None else view)

    def freeze(self, frames):
        token = uuid.uuid4().hex
        with self.lock:
            self.frozen, self.token = frames, token
        return token

    def capture(self):
        frames, skew = self.pair()
        images = decode_pair(frames)
        token = self.freeze(frames)
        return dict(ok=True, token=token, sizes=[[im.shape[1], im.shape[0]] for im in images], skew_ms=round(skew, 2))

    def apply(self, calibration, images, frames, diagnostic):
        engine = self.composer(calibration, images)
        preview = engine.render(images)
        if not cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 90])[0]:
            raise StitchError('拼接结果编码失败')
        calibration = engine.calibration
        calibration['publication_timestamps_ns'] = [s for _, s in frames]
        calibration['source_note'] = 'Input file publication times, not synchronized camera exposures'
        calibration['report']['output_size'] = list(map(int, engine.size))
        calibration['report']['overlap_fraction'] = round(engine.overlap_fraction, 4)
        # A complete validated candidate replaces the last accepted calibration.
        samples = BASE / 'samples'; samples.mkdir(exist_ok=True)
        for i, (data, _) in zip((3, 4), frames):
            (samples / ('camera%d.jpg' % i)).write_bytes(data)
        atomic_json(samples / 'pair.json', dict(timestamps_ns=[s for _, s in frames],
                    skew_ms=abs(frames[0][1] - frames[1][1]) / 1e6))
        if not cv2.imwrite(str(samples / 'matches.jpg'), diagnostic) or not cv2.imwrite(str(samples / 'stitched.jpg'), preview):
            raise StitchError('标定图保存失败')
        atomic_json(BASE / 'config/calibration.json', calibration)
        with self.lock:
            self.calibration, self.engine = calibration, engine
            self.error = ''; self.load_error = ''
        self.freeze(frames)
        return dict(ok=True, report=calibration['report'], seam_percent=round(engine.seam_fraction * 100, 1))

    def calibrate(self, body, manual_mode=False):
        if not self.job.acquire(blocking=False):
            raise StitchError('另一项标定操作正在进行，请稍后重试')
        try:
            if manual_mode:
                with self.lock:
                    if not self.token or body.get('token') != self.token or self.frozen is None:
                        raise StitchError('选点图已更新，请重新拍摄标定图并选点')
                    frames = self.frozen
                images = decode_pair(frames)
                calibration, diagnostic = manual(images, body.get('points3', []), body.get('points4', []))
            else:
                frames, _ = self.pair(); images = decode_pair(frames)
                calibration, diagnostic = automatic(images, float(body.get('fraction', .65)))
            return self.apply(calibration, images, frames, diagnostic)
        finally:
            self.job.release()

    def seam(self, body):
        fraction = float(body['percent']) / 100
        if not 0 <= fraction <= 1:
            raise StitchError('接缝位置范围为0～100%')
        if not self.job.acquire(blocking=False):
            raise StitchError('标定操作正在进行')
        try:
            frames, _ = self.pair(); images = decode_pair(frames)
            with self.lock:
                if self.calibration is None:
                    raise StitchError('请先完成对齐')
                candidate = dict(self.calibration, seam_fraction=fraction)
            engine = self.composer(candidate, images)
            engine.render(images)
            atomic_json(BASE / 'config/calibration.json', candidate)
            with self.lock:
                self.calibration, self.engine = candidate, engine
            return dict(ok=True, seam_percent=round(fraction * 100, 1))
        finally:
            self.job.release()

    def update_view(self, body):
        if not self.job.acquire(blocking=False):
            raise StitchError('另一项设置正在应用，请稍后重试')
        try:
            frames, _ = self.pair(); images = decode_pair(frames)
            with self.lock:
                if self.calibration is None:
                    raise StitchError('请先完成两路画面对齐')
                calibration = self.calibration
                value = dict(self.view)
            value.update({key: body[key] for key in ('projection', 'rotation_deg', 'crop_black') if key in body})
            value = view_config(value)
            estimate = None
            if body.get('auto') is True:
                if value['projection'] == 'balanced':
                    baseline = self.composer(calibration, images, dict(value, rotation_deg=0., crop_black=False)).render(images)
                else:
                    baseline = images[0 if value['projection'] == 'camera3' else 1]
                value['rotation_deg'], estimate = estimate_upright(baseline)
            engine = self.composer(calibration, images, value)
            if not cv2.imencode('.jpg', engine.render(images))[0]:
                raise StitchError('拉正后的画面无法编码')
            atomic_json(BASE / 'config/view.json', value)
            with self.lock:
                self.view, self.engine = value, engine
            return dict(ok=True, view=value, auto_report=estimate, output_size=list(map(int, engine.size)),
                        retained_valid_percent=round(engine.retained_valid_fraction * 100, 1))
        finally:
            self.job.release()

    def state(self):
        now = time.monotonic()
        with self.lock:
            records = [r for r in self.history if now - r[0] <= 5]
            fps = (len(records) - 1) / (records[-1][0] - records[0][0]) if len(records) > 1 else 0.
            age = now - self.output_at if self.output_at else None
            inputs = [dict(camera=i, age_ms=round((time.time_ns() - stamp) / 1e6, 1))
                      for i, (_, stamp) in zip((3, 4), self.raw)] if self.raw else []
            fresh = len(inputs) == 2 and all(-1000 < r['age_ms'] < self.config['max_input_age_seconds'] * 1000 for r in inputs)
            return dict(project='G30_mix', instance=self.instance, pid=os.getpid(), cameras=[3, 4], calibrated=self.calibration is not None,
                        running=bool(self.engine and age is not None and age < 1.5 and fresh and not self.error),
                        error=self.error, inputs=inputs, publication_skew_ms=round(self.skew_ms, 2) if self.skew_ms is not None else None,
                        output_size=self.output_size, output_fps=round(fps, 2) if age is not None and age < 1.5 else 0,
                        processing_ms=round(self.processing_ms, 2), output_age_ms=round(age * 1000, 1) if age is not None else None,
                        frames=self.sequence, source_dir=self.config['source_dir'], transport='MJPEG over HTTP/TCP',
                        view=self.view, lens_calibrated=False,
                        view_geometry=dict(canvas_size=list(map(int, self.engine.canvas_size)), crop_rect=list(map(int, self.engine.crop_rect)),
                            retained_valid_percent=round(self.engine.retained_valid_fraction * 100, 1)) if self.engine else None,
                        seam_percent=round(self.engine.seam_fraction * 100, 1) if self.engine else None,
                        calibration_report=self.calibration.get('report') if self.calibration else None)

    def run(self):
        last_pair = None
        while not self.stop.is_set():
            began = time.monotonic()
            try:
                frames, skew = self.pair()
                stamps = tuple(stamp for _, stamp in frames)
                with self.lock:
                    self.raw, self.skew_ms = frames, skew
                    engine, calibration, current_view = self.engine, self.calibration, self.view
                if engine is None and calibration is not None:
                    images = decode_pair(frames)
                    candidate = self.composer(calibration, images, current_view)
                    with self.lock:
                        if self.calibration is calibration and self.view is current_view and self.engine is None:
                            engine = self.engine = candidate
                        else:
                            engine = self.engine
                if engine is not None and stamps != last_pair:
                    images = decode_pair(frames)
                    result = engine.render(images)
                    ok, jpeg = cv2.imencode('.jpg', result, [cv2.IMWRITE_JPEG_QUALITY, self.config['jpeg_quality']])
                    if not ok:
                        raise StitchError('输出JPEG编码失败')
                    data = jpeg.tobytes(); now = time.monotonic()
                    with self.changed:
                        if self.engine is engine:
                            self.jpeg = data; self.sequence += 1; self.output_at = now
                            self.processing_ms = (now - began) * 1000
                            self.output_size = list(map(int, engine.size))
                            self.history.append((now, len(data))); self.error = ''
                            self.changed.notify_all()
                    last_pair = stamps
            except Exception as error:
                with self.lock:
                    self.error = str(error)
            self.stop.wait(max(.005, 1 / self.config['fps'] - (time.monotonic() - began)))


def main():
    config = load_config(); cv2.setNumThreads(config['opencv_threads'])
    app = App(config)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def send(self, data, mime='application/json; charset=utf-8', status=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status); self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path == '/':
                    self.send((BASE / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
                elif path == '/api/state':
                    self.send(app.state())
                elif path == '/live.mjpg':
                    self.live()
                elif path == '/snapshot.jpg':
                    with app.lock:
                        if not app.jpeg or time.monotonic() - app.output_at > 1.5:
                            raise StitchError('尚无实时拼接画面')
                        data = app.jpeg
                    self.send(data, 'image/jpeg')
                elif path in ('/source/3.jpg', '/source/4.jpg'):
                    data, _ = read_frame(config['source_dir'], int(path[-5]), config['max_input_age_seconds'])
                    self.send(data, 'image/jpeg')
                elif path in ('/calibration/3.jpg', '/calibration/4.jpg'):
                    with app.lock:
                        if app.frozen is None:
                            raise StitchError('请先拍摄标定图')
                        data = app.frozen[int(path[-5]) - 3][0]
                    self.send(data, 'image/jpeg')
                elif path == '/matches.jpg' and (BASE / 'samples/matches.jpg').exists():
                    self.send((BASE / 'samples/matches.jpg').read_bytes(), 'image/jpeg')
                else:
                    self.send(dict(error='Not found'), status=404)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.send(dict(error=str(error)), status=503)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise StitchError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise StitchError('请求格式无效')
                if self.path == '/api/capture':
                    data = app.capture()
                elif self.path == '/api/calibrate':
                    data = app.calibrate(body)
                elif self.path == '/api/manual':
                    data = app.calibrate(body, True)
                elif self.path == '/api/seam':
                    data = app.seam(body)
                elif self.path == '/api/view':
                    data = app.update_view(body)
                else:
                    self.send(dict(error='Not found'), status=404); return
                self.send(data)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.send(dict(error=str(error)), status=400)

        def live(self):
            if not app.viewers.acquire(blocking=False):
                self.send(dict(error='预览连接数已满'), status=503); return
            self.close_connection = True
            try:
                self.connection.settimeout(3)
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close'); self.end_headers()
                sequence = -1; last_send = time.monotonic()
                while not app.stop.is_set():
                    with app.changed:
                        if app.sequence == sequence or app.jpeg is None:
                            app.changed.wait(.5)
                        if app.jpeg is None or app.sequence == sequence or time.monotonic() - app.output_at > 1.5:
                            if time.monotonic() - last_send > 5:
                                break
                            continue
                        data, sequence = app.jpeg, app.sequence
                    # Each viewer takes only the newest frame; there is no unbounded queue.
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data + b'\r\n')
                    self.wfile.flush(); last_send = time.monotonic()
            except (OSError, socket.timeout):
                pass
            finally:
                app.viewers.release()

        def log_message(self, fmt, *args):
            if self.command == 'POST':
                super().log_message(fmt, *args)

    server = ThreadingHTTPServer((config['listen'], config['port']), Handler)
    server.daemon_threads = True
    def stop(*_):
        app.stop.set()
        with app.changed:
            app.changed.notify_all()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    threading.Thread(target=app.run, daemon=True, name='mix-worker').start()
    print('G30_mix listening on TCP %d; read-only inputs: %s' % (config['port'], config['source_dir']), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        app.stop.set(); server.server_close()


if __name__ == '__main__':
    main()
