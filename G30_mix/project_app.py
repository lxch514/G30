"""Persistent multi-camera project, calibration jobs and latest-frame preview."""
import collections
import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path

import cv2

from core import StitchError, atomic_json, decode_pair, estimate_upright, manual, read_frame
from mosaic import MultiComposer, automatic_graph, edge_from_pair, graph_transforms, migrate_legacy, normalize_project, orient, read_selected, signature


class App:
    def __init__(self, config, root):
        self.config, self.root = config, Path(root)
        self.path = self.root/'config/project.json'
        self.instance = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.job = threading.Lock()
        self.stop = threading.Event()
        self.viewers = threading.BoundedSemaphore(4)
        self.progress = ''
        if self.path.exists():
            self.project = normalize_project(json.loads(self.path.read_text()))
        else:
            old, view = self.root/'config/calibration.json', self.root/'config/view.json'
            try:
                self.project = migrate_legacy(json.loads(old.read_text()) if old.exists() else None,
                                              json.loads(view.read_text()) if view.exists() else {})
            except (ValueError, KeyError, OSError):
                self.project = normalize_project()
            atomic_json(self.path, self.project)
        self.revision = 0
        self.engine = None
        self.raw = None
        self.frozen = None
        self.token = None
        self.jpeg = None
        self.sequence = 0
        self.output_at = 0.
        self.processing_ms = 0.
        self.error = '正在读取画面并加载对应关系'
        self.history = collections.deque(maxlen=120)
        self.skew_ms = None
        self.output_size = None

    def composer(self, settings, images):
        return MultiComposer(settings, images, self.config['max_width'], self.config['max_height'])

    def snapshot_project(self):
        with self.lock:
            return copy.deepcopy(self.project)

    def begin(self):
        if not self.job.acquire(blocking=False):
            raise StitchError('另一项设置正在处理，请稍候')

    def finish(self):
        with self.lock: self.progress = ''
        self.job.release()

    def commit(self, candidate, engine, error='', invalidate_frozen=False):
        # Failed storage never changes the accepted running project.
        candidate = normalize_project(candidate)
        atomic_json(self.path, candidate)
        with self.changed:
            self.project, self.engine = candidate, engine
            self.revision += 1
            self.error = error
            self.jpeg = None; self.output_at = 0.
            self.raw = None; self.skew_ms = None
            self.history.clear(); self.output_size = None
            if invalidate_frozen:
                self.frozen = None; self.token = None
            self.changed.notify_all()

    def save_project(self, body):
        self.begin()
        try:
            current = self.snapshot_project()
            proposal = dict(current)
            for key in ('cameras', 'anchor', 'rotations'):
                if key in body: proposal[key] = body[key]
            candidate = normalize_project(proposal)
            changed = current['cameras'] != candidate['cameras'] or any(current['rotations'][c] != candidate['rotations'][c] for c in candidate['cameras'])
            if changed:
                candidate['calibration'] = None
                candidate['offsets'] = normalize_project()['offsets']
            engine = None
            error = '选择已保存，请自动对齐或手动选点'
            if (not changed and candidate['calibration']) or len(candidate['cameras']) == 1:
                _, images, _ = read_selected(self.config, candidate)
                engine = self.composer(candidate, images)
                error = ''
            self.commit(candidate, engine, error, invalidate_frozen=True)
            return dict(ok=True, complete=engine is not None, cameras=candidate['cameras'])
        finally:
            self.finish()

    def capture(self):
        self.begin()
        try:
            settings = self.snapshot_project()
            frames, images, skew = read_selected(self.config, settings)
            token = uuid.uuid4().hex
            with self.lock:
                self.frozen = dict(images=images, settings=settings, frames=frames, revision=self.revision)
                self.token = token
            return dict(ok=True, token=token, cameras=settings['cameras'], sizes=signature(settings, images)['sizes'], skew_ms=round(skew, 2))
        finally:
            self.finish()

    def save_samples(self, images, diagnostic):
        samples = self.root/'samples'; samples.mkdir(exist_ok=True)
        for c, image in images.items():
            if not cv2.imwrite(str(samples/('camera%d.jpg' % c)), image):
                raise StitchError('标定参考图保存失败，旧参数保持')
        if diagnostic is not None and not cv2.imwrite(str(samples/'matches.jpg'), diagnostic):
            raise StitchError('对应点示意图保存失败，旧参数保持')

    def accept_calibration(self, settings, calibration, images, diagnostic):
        settings['calibration'] = calibration
        engine = None
        try:
            graph_transforms(settings, calibration)
        except StitchError as error:
            with self.lock: previously_running = self.engine is not None
            if previously_running:
                raise
            self.save_samples(images, diagnostic)
            self.commit(settings, None, str(error))
            return dict(ok=True, complete=False, message=str(error), edges=len(calibration.get('edges', [])))
        engine = self.composer(settings, images)
        if not cv2.imencode('.jpg', engine.render(images))[0]:
            raise StitchError('拼接结果无法编码，旧参数保持')
        self.save_samples(images, diagnostic)
        self.commit(settings, engine)
        return dict(ok=True, complete=True, output_size=list(map(int, engine.size)), edges=len(calibration.get('edges', [])),
                    tree=[dict(a=e['a'], b=e['b']) for e in engine.tree])

    def calibrate(self, body, manual_mode=False):
        self.begin()
        try:
            settings = self.snapshot_project()
            if manual_mode:
                with self.lock:
                    if not self.token or body.get('token') != self.token or self.frozen is None or self.frozen['revision'] != self.revision:
                        raise StitchError('选点图已过期，请重新拍摄标定图')
                    frozen = self.frozen
                images = frozen['images']
                a, b = body.get('a'), body.get('b')
                if type(a) is not int or type(b) is not int or a == b or a not in settings['cameras'] or b not in settings['cameras']:
                    raise StitchError('请选择所选画面中两台不同的相机')
                pair, diagnostic = manual([images[a], images[b]], body.get('pointsA', []), body.get('pointsB', []))
                calibration = settings['calibration'] or dict(signature(settings, images), edges=[])
                calibration = copy.deepcopy(calibration)
                calibration['edges'] = [e for e in calibration.get('edges', []) if {e['a'], e['b']} != {a, b}]
                calibration['edges'].append(edge_from_pair(a, b, pair))
                # Manual connections accumulate until every selected source is connected.
                result = self.accept_calibration(settings, calibration, images, diagnostic)
                with self.lock:
                    if self.frozen is frozen:
                        self.frozen = dict(frozen, revision=self.revision)
                return result
            _, images, _ = read_selected(self.config, settings)
            def progress(message):
                with self.lock: self.progress = message
            calibration, diagnostic = automatic_graph(settings, images, body.get('fraction', 1.), progress)
            if len(settings['cameras']) > 1 and not calibration['edges']:
                raise StitchError('未找到可靠的共同纹理，请拍摄标定图并手动建立对应点')
            settings['offsets'] = normalize_project()['offsets']
            result = self.accept_calibration(settings, calibration, images, diagnostic)
            with self.lock: self.frozen = None; self.token = None
            return result
        finally:
            self.finish()

    def update_geometry(self, body, kind):
        self.begin()
        try:
            candidate = self.snapshot_project()
            if kind == 'position':
                c = body.get('camera')
                if type(c) is not int or c not in candidate['cameras']:
                    raise StitchError('请选择要移动的相机')
                candidate['offsets'][str(c)] = [body.get('x'), body.get('y')]
            elif kind == 'reset_positions':
                candidate['offsets'] = normalize_project()['offsets']
            elif kind == 'seam':
                for source, target in (('percent', 'seam_percent'), ('blend_width', 'blend_width')):
                    if source in body: candidate[target] = body[source]
            else:
                candidate['view'].update({k: body[k] for k in ('rotation_deg', 'crop_black') if k in body})
            candidate = normalize_project(candidate)
            _, images, _ = read_selected(self.config, candidate)
            auto_report = None
            if kind == 'view' and body.get('auto') is True:
                candidate['view']['rotation_deg'], auto_report = estimate_upright(images[candidate['anchor']])
            engine = self.composer(candidate, images)
            if not cv2.imencode('.jpg', engine.render(images))[0]:
                raise StitchError('画面无法编码')
            self.commit(candidate, engine)
            return dict(ok=True, output_size=list(map(int, engine.size)), auto_report=auto_report)
        finally:
            self.finish()

    def source(self, camera, frozen=False):
        if camera not in range(6):
            raise StitchError('摄像头编号无效')
        if frozen:
            with self.lock:
                if self.frozen is None or camera not in self.frozen['images']:
                    raise StitchError('请先拍摄当前所选相机的标定图')
                image = self.frozen['images'][camera]
        else:
            with self.lock: rotation = self.project['rotations'][camera]
            data, stamp = read_frame(self.config['source_dir'], camera, self.config['max_input_age_seconds'])
            image = orient(decode_pair([(data, stamp)])[0], rotation)
        if not frozen and image.shape[1] > 640:
            image = cv2.resize(image, (640, round(image.shape[0]*640/image.shape[1])), interpolation=cv2.INTER_AREA)
        ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok: raise StitchError('画面编码失败')
        return data.tobytes()

    def state(self):
        now = time.monotonic()
        with self.lock:
            settings = self.project
            recent = [r for r in self.history if now-r[0] <= 5]
            fps = (len(recent)-1)/(recent[-1][0]-recent[0][0]) if len(recent)>1 else 0.
            age = now-self.output_at if self.output_at else None
            inputs = [dict(camera=c, age_ms=round((time.time_ns()-stamp)/1e6, 1)) for c, (_, stamp) in zip(settings['cameras'], self.raw)] if self.raw else []
            fresh = len(inputs)==len(settings['cameras']) and all(-1000 < r['age_ms'] < self.config['max_input_age_seconds']*1000 for r in inputs)
            public = {k: copy.deepcopy(v) for k, v in settings.items() if k != 'calibration'}
            calibration = settings['calibration'] or {}
            return dict(project='G30_mix', instance=self.instance, pid=os.getpid(), revision=self.revision,
                        cameras=list(settings['cameras']), settings=public, calibrated=self.engine is not None,
                        running=bool(self.engine and age is not None and age<1.5 and fresh and not self.error),
                        error=self.error, busy=self.job.locked(), progress=self.progress,
                        inputs=inputs, publication_skew_ms=round(self.skew_ms, 2) if self.skew_ms is not None else None,
                        output_size=self.output_size, output_fps=round(fps, 2) if age is not None and age<1.5 else 0,
                        processing_ms=round(self.processing_ms, 2), frames=self.sequence,
                        output_age_ms=round(age*1000, 1) if age is not None else None,
                        geometry=self.engine.geometry() if self.engine else None, transport='MJPEG over HTTP/TCP',
                        edges=[dict(a=e['a'], b=e['b'], report=e.get('report', {})) for e in calibration.get('edges', [])],
                        lens_calibrated=False, source_dir=self.config['source_dir'])

    def run(self):
        last_stamps = None
        while not self.stop.is_set():
            began = time.monotonic()
            try:
                with self.lock:
                    settings, engine, revision = self.project, self.engine, self.revision
                frames, images, skew = read_selected(self.config, settings)
                with self.lock:
                    if self.revision != revision: continue
                    self.raw, self.skew_ms = frames, skew
                if engine is None:
                    candidate = self.composer(settings, images)
                    with self.lock:
                        if self.revision != revision: continue
                        engine = self.engine = candidate
                stamps = (revision, tuple(s for _, s in frames))
                if stamps != last_stamps:
                    result = engine.render(images)
                    ok, jpeg = cv2.imencode('.jpg', result, [cv2.IMWRITE_JPEG_QUALITY, self.config['jpeg_quality']])
                    if not ok: raise StitchError('输出JPEG编码失败')
                    data = jpeg.tobytes(); now = time.monotonic()
                    with self.changed:
                        if self.revision == revision and self.engine is engine:
                            self.jpeg = data; self.sequence += 1; self.output_at = now
                            self.processing_ms = (now-began)*1000
                            self.output_size = list(map(int, engine.size))
                            self.history.append((now, len(data))); self.error = ''
                            self.changed.notify_all()
                    last_stamps = stamps
            except Exception as error:
                with self.lock:
                    if self.revision == revision: self.error = str(error)
            self.stop.wait(max(.005, 1/self.config['fps']-(time.monotonic()-began)))
