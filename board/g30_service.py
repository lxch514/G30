#!/usr/bin/env python3
"""Six independently encoded cameras, RTP unicast, small HTTP session API.

No actuator, serial, CAN, MQTT or snappy-real control interfaces are accessed.
Destinations are the HTTP client's own address; session ports are explicit.
"""
import argparse
import json
import queue
import re
import signal
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from h264_encoder import H264Hub
from rtp_h264 import RtpPacketizer

BASE = Path(__file__).resolve().parents[1]

def load_config(path):
    value = json.loads(path.read_text())
    for key, low, high in [('cameras',6,6),('width',160,3840),('height',120,2160),
                           ('fps',20,30),('bitrate',100000,20000000),('http_port',1024,65535),
                           ('mtu',256,1400),('lease_seconds',6,60)]:
        if type(value.get(key)) is not int or not low <= value[key] <= high:
            raise ValueError('Invalid configuration: '+key)
    if value['fps'] not in (20,25,30):
        raise ValueError('FPS must be 20, 25 or 30')
    return value

class Session:
    def __init__(self, manager, token, host, ports):
        self.manager, self.token, self.host, self.ports = manager, token, host, ports
        self.last_seen = time.monotonic()                               
        self.stop = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024*1024)
        self.socket.settimeout(.2)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.connect((host,ports[0]))
            self.source_ip=probe.getsockname()[0]
        self.packetizers = [RtpPacketizer(manager.config['mtu']) for _ in range(6)]
        self.packets = [0]*6
        self.bytes = [0]*6
        self.clock_anchor = [None]*6
        self.clock_frames = [0]*6
        self.rejoins = 0
        self.error = ''
        self.subscription = None
        threading.Thread(target=self.run,daemon=True,name='rtp-'+token[:8]).start()

    def close(self):
        self.stop.set()

    def run(self):
        try:
            while not self.stop.is_set() and not self.manager.stopping.is_set():
                if time.monotonic()-self.last_seen > self.manager.config['lease_seconds']:
                    break
                if self.subscription is None or self.subscription.closed.is_set():
                    if self.subscription is not None:   
                        self.manager.hub.unsubscribe(self.subscription)
                        self.rejoins += 1
                    self.subscription = self.manager.hub.subscribe()
                try:
                    frame = self.subscription.queue.get(timeout=.3)
                except queue.Empty:
                    continue
                camera, key, _, capture_us, length = struct.unpack('!BBHQI',frame[:16])
                if camera >= 6 or len(frame) != 16+length:
                    continue
                try:
                    # RTP follows the encoder's fixed-rate presentation clock, not
                    # filesystem timestamp jitter. The native client renders live.
                    if self.clock_anchor[camera] is None:self.clock_anchor[camera]=capture_us
                    presentation_us=self.clock_anchor[camera]+self.clock_frames[camera]*1000000//self.manager.config['fps']
                    self.clock_frames[camera]+=1
                    for packet in self.packetizers[camera].access_unit(frame[16:],presentation_us):
                        sent = self.socket.sendto(packet,(self.host,self.ports[camera]))
                        self.packets[camera] += 1
                        self.bytes[camera] += sent
                    self.error = ''
                except OSError as error:
                    self.error = str(error)
                    # Rejoin at keyframes, preserving reference-picture integrity.
                    self.manager.hub.unsubscribe(self.subscription)
                    self.subscription = None
                    self.stop.wait(.2)
        finally:
            if self.subscription is not None:
                self.manager.hub.unsubscribe(self.subscription)
            self.socket.close()
            self.manager.remove(self)

    def describe(self):
        return dict(id=self.token,host=self.host,rtp_source=self.source_ip,ports=self.ports,packets=self.packets[:],
                    bytes=self.bytes[:],rejoins=self.rejoins,error=self.error,
                    lease_remaining=round(max(0,self.manager.config['lease_seconds']-(time.monotonic()-self.last_seen)),1))

class Manager:
    def __init__(self, directory, config, stopping):
        self.directory, self.config, self.stopping = directory, config, stopping
        self.lock = threading.RLock()
        self.sessions = {}
        self.http_viewers = 0
        self.hub = H264Hub(directory,6,stopping,config['bitrate'])

    def remove(self, session):
        with self.lock:
            if self.sessions.get(session.token) is session:
                self.sessions.pop(session.token,None)

    def register(self, token, host, ports):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,80}',token):
            raise ValueError('Invalid session id')
        if not isinstance(ports,list) or len(ports)!=6 or len(set(ports))!=6 or any(type(p) is not int or not 1024<=p<=65535 for p in ports):
            raise ValueError('Six distinct UDP ports are required')
        with self.lock:
            old = self.sessions.get(token)
            if old and not old.stop.is_set() and old.host==host and old.ports==ports:
                old.last_seen = time.monotonic()
                return old
            if old:
                old.close()
            if len(self.sessions)>=4 and token not in self.sessions:
                raise ValueError('Maximum four viewing sessions')
            session = Session(self,token,host,ports)
            self.sessions[token] = session
            return session

    def state(self):
        sources = []
        for index in range(6):
            try:
                source=json.loads((self.directory/f'cam{index}-state.json').read_text())['result']['source']
                recent=time.time()-(self.directory/f'cam{index}.jpg').stat().st_mtime<2
                source['online']=bool(source.get('online')) and recent
                if not source['online']:source['captured_fps']=0
            except (OSError,ValueError,KeyError):
                source=dict(online=False,captured_fps=0,resolution=dict(width=self.config['width'],height=self.config['height']),desired_fps=self.config['fps'])
            sources.append(dict(camera=index,label=f'camera{index}',source=source,transcode=self.hub.describe(index)))
        with self.lock:
            sessions=[s.describe() for s in self.sessions.values()]
        return dict(ok=True,project='G30-six-camera',codec='H264',transport='HTTP/TCP or RTP/UDP',
                    mode=dict(width=self.config['width'],height=self.config['height'],fps=self.config['fps']),
                    bitrate_per_camera=self.hub.bitrate,nominal_total_mbps=6*self.hub.bitrate/1e6,
                    cameras=sources,sessions=sessions,http_viewers=self.http_viewers,
                    all_online=all(s['source']['online'] and s['transcode']['online'] for s in sources),
                    test_pattern=(BASE/'run/test-pattern.flag').exists())

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=BASE/'config/g30.json')
    parser.add_argument('--directory',type=Path,default=BASE/'run/frames')
    args=parser.parse_args()
    config=load_config(args.config)
    stopping=threading.Event()
    manager=Manager(args.directory,config,stopping)
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        disable_nagle_algorithm=True
        def reply(self,data,status=200):
            payload=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(payload)
        def do_GET(self):
            try:
                if self.path=='/api/feed':
                    self.feed()
                elif self.path in ('/','/api/state','/api/health'):
                    self.reply(manager.state())
                elif self.path=='/api/config':
                    self.reply(config)
                else:self.reply(dict(error='Not found'),404)
            except (BrokenPipeError,ConnectionResetError):pass
        def feed(self):
            subscription=manager.hub.subscribe()
            with manager.lock:manager.http_viewers+=1
            self.connection.settimeout(3)
            self.close_connection=True
            try:
                self.send_response(200)
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Accel-Buffering','no')
                self.send_header('Connection','close')
                self.end_headers()
                last_packet=time.monotonic()
                while not stopping.is_set() and not subscription.closed.is_set():
                    try:packet=subscription.queue.get(timeout=.5)
                    except queue.Empty:
                        if time.monotonic()-last_packet>3:break
                        continue
                    self.wfile.write(packet);self.wfile.flush()
                    last_packet=time.monotonic()
            except (OSError,TimeoutError):pass
            finally:
                manager.hub.unsubscribe(subscription)
                with manager.lock:manager.http_viewers-=1
        def do_POST(self):
            try:
                if self.path!='/api/session':
                    self.reply(dict(error='Not found'),404);return
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:
                    raise ValueError('Invalid body size')
                value=json.loads(self.rfile.read(length))
                if not isinstance(value,dict):raise ValueError('JSON object required')
                session=manager.register(value.get('id'),self.client_address[0],value.get('ports'))
                self.reply(dict(ok=True,session=session.describe(),mode=manager.state()['mode'],
                                bitrate_per_camera=manager.hub.bitrate,lease_seconds=config['lease_seconds']))
            except (ValueError,TypeError,KeyError) as error:
                self.reply(dict(error=str(error)),400)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_DELETE(self):
            try:
                prefix='/api/session/'
                if not self.path.startswith(prefix):self.reply(dict(error='Not found'),404);return
                token=self.path[len(prefix):]
                with manager.lock:
                    session=manager.sessions.get(token)
                    if session and session.host==self.client_address[0]:session.close()
                self.reply(dict(ok=True))
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,format,*values):
            if self.command!='GET' and self.command!='POST':super().log_message(format,*values)
    server=ThreadingHTTPServer((config['listen'],config['http_port']),Handler)
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    signal.signal(signal.SIGTERM,lambda *_:stopping.set())
    signal.signal(signal.SIGINT,lambda *_:stopping.set())
    print(f'G30: six H264 encoders at {config["bitrate"]}bps/camera; API {config["listen"]}:{config["http_port"]}',flush=True)
    try:stopping.wait()
    finally:
        stopping.set()
        with manager.lock:
            for session in list(manager.sessions.values()):session.close()
        server.shutdown();server.server_close()

if __name__=='__main__':main()
