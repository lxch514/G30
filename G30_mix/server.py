#!/usr/bin/env python3
"""Independent multi-camera planar fusion, read-only input, HTTP/TCP preview."""
import json
import os
import re
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import cv2

from core import StitchError
from project_app import App

BASE = Path(__file__).resolve().parent


def load_config():
    config = json.loads((BASE/'config/mix.json').read_text())
    for key, low, high in [('port', 1024, 65535), ('fps', 1, 20), ('max_width', 640, 1920),
                           ('max_height', 360, 1200), ('jpeg_quality', 30, 95),
                           ('blend_width_pixels', 0, 40), ('opencv_threads', 1, 4)]:
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError('Invalid configuration: '+key)
    if not .1 <= config['max_input_age_seconds'] <= 3 or not 1 <= config['max_publication_skew_ms'] <= 150:
        raise ValueError('Invalid input freshness configuration')
    config['source_dir'] = str(Path(config['source_dir']).resolve())
    return config


def main():
    config = load_config(); cv2.setNumThreads(config['opencv_threads'])
    app = App(config, BASE)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def send(self, data, mime='application/json; charset=utf-8', status=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                source = re.fullmatch(r'/(source|calibration)/([0-5])\.jpg', path)
                if path == '/':
                    self.send((BASE/'web/index.html').read_bytes(), 'text/html; charset=utf-8')
                elif path == '/api/state':
                    self.send(app.state())
                elif path == '/live.mjpg':
                    self.live()
                elif path == '/snapshot.jpg':
                    with app.lock:
                        if not app.jpeg or time.monotonic()-app.output_at > 1.5:
                            raise StitchError('尚无实时拼接画面，请先完成当前选择的对齐')
                        data = app.jpeg
                    self.send(data, 'image/jpeg')
                elif source:
                    self.send(app.source(int(source[2]), source[1]=='calibration'), 'image/jpeg')
                elif path == '/matches.jpg' and (BASE/'samples/matches.jpg').exists():
                    self.send((BASE/'samples/matches.jpg').read_bytes(), 'image/jpeg')
                else:
                    self.send(dict(error='Not found'), status=404)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.send(dict(error=str(error)), status=503)

        def do_POST(self):
            try:
                self.connection.settimeout(10)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    self.close_connection = True
                    raise StitchError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise StitchError('请求格式无效')
                path = urlsplit(self.path).path
                if path == '/api/project':
                    data = app.save_project(body)
                elif path == '/api/capture':
                    data = app.capture()
                elif path in ('/api/calibrate', '/api/manual'):
                    data = app.calibrate(body, path=='/api/manual')
                elif path in ('/api/position', '/api/reset_positions', '/api/seam', '/api/view'):
                    data = app.update_geometry(body, path.removeprefix('/api/'))
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
                        if app.jpeg is None or app.sequence == sequence or time.monotonic()-app.output_at > 1.5:
                            if time.monotonic()-last_send > 5: break
                            continue
                        data, sequence = app.jpeg, app.sequence
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data+b'\r\n')
                    self.wfile.flush(); last_send = time.monotonic()
            except (OSError, socket.timeout):
                pass
            finally:
                app.viewers.release()

        def log_message(self, fmt, *args):
            if self.command == 'POST': super().log_message(fmt, *args)

    server = ThreadingHTTPServer((config['listen'], config['port']), Handler)
    server.daemon_threads = True
    def stop(*_):
        app.stop.set()
        with app.changed: app.changed.notify_all()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    threading.Thread(target=app.run, daemon=True, name='mix-worker').start()
    print('G30_mix multi-camera fusion on TCP %d; read-only %s' % (config['port'], config['source_dir']), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        app.stop.set(); server.server_close()


if __name__ == '__main__':
    main()
