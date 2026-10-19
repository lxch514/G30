"""Bounded six-camera MJPEG -> Rockchip hardware H.264 pipelines.

Encoded pictures stay ordered. Slow TCP subscribers discard the stale queue and
resume each channel at SPS/PPS + IDR without reconnecting; legacy RTP subscribers
are disconnected on overload. A P-picture is never resumed across a reference gap.
"""
import collections
import json
import os
import queue
import re
import struct
import threading
import time
from pathlib import Path

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
# PyGObject lazily constructs enum wrappers. Resolve them on the main thread
# before six channels touch GI concurrently (observed on this board's GI build).
PLAYING, NULL = Gst.State.PLAYING, Gst.State.NULL
FLOW_OK, FLOW_ERROR, FLOW_FLUSHING = Gst.FlowReturn.OK, Gst.FlowReturn.ERROR, Gst.FlowReturn.FLUSHING
STATE_FAILURE = Gst.StateChangeReturn.FAILURE
MESSAGE_ERROR, MESSAGE_EOS = Gst.MessageType.ERROR, Gst.MessageType.EOS
Gst.Buffer.new_allocate

START_CODES = re.compile(b'\x00\x00(?:\x00)?\x01')

def nal_types(data):
    return [data[m.end()] & 31 for m in START_CODES.finditer(data) if m.end() < len(data)]

class Subscription:
    def __init__(self, cameras, recover=False):
        self.queue = queue.Queue(maxsize=120)
        self.wait_key = [True] * cameras
        self.closed = threading.Event()
        self.recover = recover
        self.resyncs = 0

class H264Hub:
    def __init__(self, directory, cameras, stopping, bitrate=2000000):
        self.directory, self.stopping, self.bitrate = Path(directory), stopping, bitrate
        self.lock = threading.RLock()
        self.pipeline_lock = threading.Lock()
        self.subscribers = set()
        self.tcp_resyncs = 0
        self.channels = [Channel(self, i) for i in range(cameras)]
        for channel in self.channels:
            threading.Thread(target=channel.run, daemon=True, name=f'h264-{channel.index}').start()

    def subscribe(self, recover=False):
        subscriber = Subscription(len(self.channels),recover)
        with self.lock:self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        subscriber.closed.set()
        with self.lock:self.subscribers.discard(subscriber)

    def publish(self, camera, data, key, capture_us):
        packet = struct.pack('!BBHQI', camera, int(key), 0, capture_us, len(data)) + data
        with self.lock:
            for subscriber in tuple(self.subscribers):
                if subscriber.closed.is_set():continue
                if subscriber.wait_key[camera]:
                    if not key:continue
                    subscriber.wait_key[camera] = False
                try:subscriber.queue.put_nowait(packet)
                except queue.Full:
                    # An H.264 P-picture cannot be skipped without breaking references.
                    if not subscriber.recover:
                        subscriber.closed.set();continue
                    # TCP retains its congestion window while resuming each channel
                    # at a complete IDR. Bound latency without reconnecting the socket
                    # on every short network burst; never emit P-pictures across a gap.
                    while True:
                        try:subscriber.queue.get_nowait()
                        except queue.Empty:break
                    subscriber.wait_key[:]=[True]*len(subscriber.wait_key)
                    subscriber.resyncs+=1;self.tcp_resyncs+=1
                    if key:
                        subscriber.queue.put_nowait(packet);subscriber.wait_key[camera]=False

    def describe(self, camera):
        return self.channels[camera].describe()

class Channel:
    def __init__(self, hub, index):
        self.hub, self.index = hub, index
        self.lock = threading.Lock()
        self.pipeline = self.source = self.sink = None
        self.mode = None
        self.frames = self.bytes = self.submitted = self.restarts = 0
        self.last_output = 0
        self.last_capture_us = 0
        self.error = ''
        self.history = collections.deque(maxlen=300)
        self.latency = collections.deque(maxlen=120)
        self.wall_start_ns = 0
        self.pending = collections.deque()
        self.input_dropped = 0
        self.pipeline_sequence = 0
        self.active_bitrate = 0
        self.started_at = 0

    def describe(self):
        with self.lock:
            now = time.monotonic()
            online = now-self.last_output < 2 and self.last_output > 0
            recent = [p for p in self.history if p[0] >= now-3]
            elapsed = recent[-1][0]-recent[0][0] if len(recent)>1 else 0
            fps = (recent[-1][1]-recent[0][1])/elapsed if elapsed>.1 else 0
            mbps = (recent[-1][2]-recent[0][2])*8/elapsed/1e6 if elapsed>.1 else 0
            delays = sorted(self.latency)
            return dict(type='H264_HW',decoder='mppjpegdec',encoder='mpph264enc',online=online,
                        target_bps=self.hub.bitrate,encoded_fps=round(fps,2) if online else 0,
                        encoded_mbps=round(mbps,3) if online else 0,frames=self.frames,bytes=self.bytes,
                        submitted=self.submitted,input_dropped=self.input_dropped,restarts=self.restarts,error=self.error,mode=self.mode,
                        encode_latency_ms=round(delays[len(delays)//2],2) if delays else None,
                        last_capture_us=self.last_capture_us)

    def output(self, sink):
        sample = sink.emit('pull-sample')
        if sample is None:return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        data = buffer.extract_dup(0,buffer.get_size())
        types = nal_types(data)
        if not any(kind in (1,5) for kind in types):return Gst.FlowReturn.OK
        key = 5 in types
        if key and not (7 in types and 8 in types):
            with self.lock:self.error='IDR 缺少 SPS/PPS'
            return Gst.FlowReturn.ERROR
        now = time.monotonic()
        with self.lock:
            # Baseline H.264 produces one ordered picture per JPEG. Track the
            # original file timestamp independently of parser/encoder PTS rewrites.
            if not self.pending:
                self.error='编码输出缺少输入时间戳';return Gst.FlowReturn.ERROR
            capture_us=self.pending.popleft()
            self.frames += 1;self.bytes += len(data);self.last_output = now
            self.last_capture_us = capture_us
            self.history.append((now,self.frames,self.bytes))
            self.latency.append(max(0,(time.time_ns()/1000-capture_us)/1000))
            self.error = ''
        self.hub.publish(self.index,data,key,capture_us)
        return Gst.FlowReturn.OK

    def start(self, mode):
        # Serialize initial plugin/MPP construction; frame processing stays parallel.
        with self.hub.pipeline_lock:
            self._start(mode)

    def _start(self, mode):
        width,height,fps = mode
        pipeline = Gst.parse_launch(
            'appsrc name=jpeg is-live=true format=time block=false max-buffers=2 max-bytes=0 leaky-type=none '
            f'caps="image/jpeg,width={width},height={height},framerate={fps}/1" '
            '! jpegparse ! mppjpegdec format=NV12 ignore-error=true '
            '! video/x-raw,format=NV12 '
            f'! mpph264enc bps={self.hub.bitrate} bps-min={self.hub.bitrate*9//10} bps-max={self.hub.bitrate*11//10} '
            f'rc-mode=cbr gop={fps} profile=baseline level=4 header-mode=each-idr max-pending=2 max-reenc=0 '
            '! h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au '
            '! appsink name=encoded emit-signals=true sync=false max-buffers=4 drop=false')
        self.pipeline = pipeline
        self.source = pipeline.get_by_name('jpeg')
        self.sink = pipeline.get_by_name('encoded')
        self.sink.connect('new-sample',self.output)
        self.wall_start_ns = time.time_ns()
        self.started_at = time.monotonic()
        self.active_bitrate = self.hub.bitrate
        self.pipeline_sequence = 0
        with self.lock:self.pending.clear()
        if pipeline.set_state(PLAYING)==STATE_FAILURE:
            raise RuntimeError('硬件编解码管线启动失败')
        with self.lock:
            self.mode = dict(width=width,height=height,fps=fps)
            self.restarts += 1
        print(f'H264 camera {self.index+1}: {width}x{height}@{fps}, target {self.hub.bitrate}bps, hardware decoder + encoder',flush=True)

    def stop(self):
        if self.pipeline:self.pipeline.set_state(NULL)
        self.pipeline = self.source = self.sink = None
        with self.lock:self.pending.clear()

    def run(self):
        jpeg = self.hub.directory/f'cam{self.index}.jpg'
        state = self.hub.directory/f'cam{self.index}-state.json'
        last_stamp = 0;mode = None;next_state = 0
        while not self.hub.stopping.is_set():
            try:
                now=time.monotonic()
                if now>=next_state:
                    next_state=now+.2
                    source=json.loads(state.read_text())['result']['source']
                    configuration=json.loads(state.read_text())['result'].get('configuration',{})
                    new_mode=(source['resolution']['width'],source['resolution']['height'],source['desired_fps'])
                    if not source.get('online', False):
                        self.stop();mode=None;self.hub.stopping.wait(.1);continue
                    if configuration.get('status')=='applying':
                        self.hub.stopping.wait(.02);continue
                    if mode!=new_mode or not self.pipeline or self.active_bitrate!=self.hub.bitrate:
                        self.stop();self.start(new_mode);mode=new_mode;last_stamp=0
                if self.pipeline:
                    if now-self.started_at>3 and now-self.last_output>2:
                        raise RuntimeError('No encoded output for 2 seconds; rebuilding pipeline')
                    message=self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR|Gst.MessageType.EOS)
                    if message:
                        if message.type==Gst.MessageType.ERROR:
                            error,debug=message.parse_error();raise RuntimeError(f'{error.message}: {debug}')
                        raise RuntimeError('硬件编码管线结束')
                if self.pipeline and jpeg.stat().st_mtime_ns!=last_stamp:
                    with jpeg.open('rb') as handle:
                        stat=os.fstat(handle.fileno());data=handle.read()
                    if stat.st_mtime_ns==last_stamp or time.time()-stat.st_mtime>1:
                        self.hub.stopping.wait(.003);continue
                    last_stamp=stat.st_mtime_ns
                    with self.lock:
                        full=len(self.pending)>=3
                    if full or self.source.get_property('current-level-buffers')>=1:
                        with self.lock:self.input_dropped+=1
                        continue
                    buffer=Gst.Buffer.new_allocate(None,len(data),None);buffer.fill(0,data)
                    buffer.pts=buffer.dts=self.pipeline_sequence*Gst.SECOND//mode[2]
                    self.pipeline_sequence+=1
                    with self.lock:self.pending.append(stat.st_mtime_ns//1000)
                    buffer.duration=Gst.SECOND//mode[2]
                    result=self.source.emit('push-buffer',buffer)
                    if result==Gst.FlowReturn.FLUSHING:
                        with self.lock:
                            if self.pending:self.pending.pop()
                        self.hub.stopping.wait(.01);continue
                    if result!=Gst.FlowReturn.OK:raise RuntimeError(f'JPEG 输入失败: {result}')
                    with self.lock:self.submitted+=1
                self.hub.stopping.wait(.003)
            except (FileNotFoundError,KeyError,json.JSONDecodeError):
                self.hub.stopping.wait(.1)
            except Exception as error:
                with self.lock:self.error=str(error)[:400]
                print(f'H264 camera {self.index+1}: {error}',flush=True)
                self.stop();mode=None;self.hub.stopping.wait(1)
        self.stop()
