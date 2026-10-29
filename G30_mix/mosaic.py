"""Connected, planar multi-camera registration and narrow-seam compositing."""
import copy
import itertools
import time

import cv2
import numpy as np

from core import StitchError, automatic, corners, decode_pair, largest_valid_rectangle, project, read_frame


def normalize_project(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise StitchError('项目设置必须是对象')
    cameras = value.get('cameras', [3, 4])
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= 6 or any(type(c) is not int or c not in range(6) for c in cameras) or len(set(cameras)) != len(cameras):
        raise StitchError('请选择1～6台不同的摄像头，编号范围camera0～5')
    anchor = value.get('anchor', cameras[0])
    if type(anchor) is not int or anchor not in cameras:
        raise StitchError('基准相机必须包含在所选画面中')
    rotations = value.get('rotations', [180] * 6)
    if not isinstance(rotations, list) or len(rotations) != 6 or any(type(r) is not int or r not in (0, 180) for r in rotations):
        raise StitchError('六路转正角度只能为0或180')
    offsets = value.get('offsets', {})
    if not isinstance(offsets, dict):
        raise StitchError('位置设置无效')
    normalized = {}
    for c in range(6):
        pair = offsets.get(str(c), [0, 0])
        if not isinstance(pair, list) or len(pair) != 2 or any(type(v) not in (int, float) or not np.isfinite(v) or abs(v) > 4096 for v in pair):
            raise StitchError('X/Y微调范围为-4096～4096原图像素')
        normalized[str(c)] = [float(v) for v in pair]
    view = value.get('view', {})
    if not isinstance(view, dict):
        raise StitchError('拉正参数无效')
    angle = view.get('rotation_deg', 0.)
    crop = view.get('crop_black', False)
    if type(angle) not in (int, float) or not np.isfinite(angle) or abs(angle) > 15 or type(crop) is not bool:
        raise StitchError('水平微调范围为-15～15度，裁黑边须为开关值')
    blend = value.get('blend_width', 12)
    seam = value.get('seam_percent', 50.)
    if type(blend) is not int or not 0 <= blend <= 40 or type(seam) not in (int, float) or not np.isfinite(seam) or not 0 <= seam <= 100:
        raise StitchError('融合宽度范围0～40，双路接缝位置范围0～100%')
    calibration = value.get('calibration')
    if calibration is not None and not isinstance(calibration, dict):
        raise StitchError('对应关系格式无效')
    return dict(version=2, cameras=list(cameras), anchor=anchor, rotations=list(rotations), offsets=normalized,
                view=dict(rotation_deg=float(angle), crop_black=crop), blend_width=blend,
                seam_percent=float(seam), calibration=copy.deepcopy(calibration))


def read_selected(config, settings):
    cameras = settings['cameras']
    best = None
    for attempt in range(4):
        frames = [read_frame(config['source_dir'], c, config['max_input_age_seconds']) for c in cameras]
        skew = (max(s for _, s in frames) - min(s for _, s in frames)) / 1e6
        if best is None or skew < best[1]:
            best = frames, skew
        if skew <= config['max_publication_skew_ms']:
            images = decode_pair(frames)
            return frames, {c: orient(im, settings['rotations'][c]) for c, im in zip(cameras, images)}, skew
        if attempt < 3:
            time.sleep(.006)
    raise StitchError('所选画面文件到达差过大：%.1fms' % best[1])


def orient(image, degrees):
    return cv2.rotate(image, cv2.ROTATE_180) if degrees == 180 else image


def signature(settings, images):
    return dict(sizes={str(c): [images[c].shape[1], images[c].shape[0]] for c in settings['cameras']},
                rotations={str(c): settings['rotations'][c] for c in settings['cameras']})


def edge_from_pair(a, b, pair):
    return dict(a=a, b=b, H_b_to_a=pair['H_4_to_3'], report=pair['report'])


def check_matrix(matrix):
    matrix = np.asarray(matrix, np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-10:
        raise StitchError('对应关系矩阵无效或退化')
    return matrix


def graph_transforms(settings, calibration):
    cameras, anchor = settings['cameras'], settings['anchor']
    if len(cameras) == 1:
        return {anchor: np.eye(3)}, []
    if not calibration:
        raise StitchError('尚未对齐，请自动匹配或手动选择对应点')
    transforms, tree = {anchor: np.eye(3)}, []
    edges = calibration.get('edges', [])
    if not isinstance(edges, list):
        raise StitchError('对应关系文件无效')
    def quality(edge):
        report = edge.get('report', {})
        return (report.get('method') == 'manual', report.get('inliers', 0), -report.get('median_error_px', 999))
    ordered = sorted(edges, key=quality, reverse=True)
    while len(transforms) < len(cameras):
        chosen = None
        for edge in ordered:
            a, b = edge['a'], edge['b']
            if a not in cameras or b not in cameras or a == b:
                raise StitchError('对应关系包含未选择的相机')
            if (a in transforms) != (b in transforms):
                chosen = edge; break
        if chosen is None:
            missing = ['camera%d' % c for c in cameras if c not in transforms]
            raise StitchError('无法连接到基准画面：' + '、'.join(missing) + '；需要共同重叠，或手动为这些相机添加对应点')
        a, b = chosen['a'], chosen['b']
        matrix = check_matrix(chosen['H_b_to_a'])
        if a in transforms:
            transforms[b] = transforms[a] @ matrix
        else:
            transforms[a] = transforms[b] @ np.linalg.inv(matrix)
        tree.append(chosen)
    return transforms, tree


def migrate_legacy(calibration, view):
    settings = normalize_project()
    if not calibration:
        return settings
    sizes = calibration['sizes']
    turns = [np.array([[-1., 0, w-1], [0, -1., h-1], [0, 0, 1.]]) for w, h in sizes]
    matrix = turns[0] @ check_matrix(calibration['H_4_to_3']) @ np.linalg.inv(turns[1])
    settings['anchor'] = 4 if view.get('projection') == 'camera4' else 3
    settings['view'] = dict(rotation_deg=float(view.get('rotation_deg', 0)), crop_black=bool(view.get('crop_black', False)))
    settings['seam_percent'] = 100 * (1 - calibration.get('seam_fraction', .5))
    settings['calibration'] = dict(sizes={str(c): s for c, s in zip((3, 4), sizes)}, rotations={'3': 180, '4': 180},
        edges=[dict(a=3, b=4, H_b_to_a=matrix.tolist(), report=copy.deepcopy(calibration.get('report', {})))],
        note='Legacy correspondence conjugated into 180-degree input coordinates')
    return normalize_project(settings)


def automatic_graph(settings, images, fraction=1., progress=None):
    if type(fraction) not in (int, float) or not .3 <= fraction <= 1:
        raise StitchError('特征区域范围无效')
    calibration = dict(signature(settings, images), edges=[], rejected=[])
    diagnostic = None
    for a, b in itertools.combinations(settings['cameras'], 2):
        if progress:
            progress('正在匹配camera%d与camera%d' % (a, b))
        try:
            pair, picture = automatic([images[a], images[b]], fraction)
            calibration['edges'].append(edge_from_pair(a, b, pair))
            diagnostic = picture
        except StitchError as error:
            calibration['rejected'].append(dict(a=a, b=b, reason=str(error)))
    return calibration, diagnostic


class MultiComposer:
    def __init__(self, settings, images, max_width=1600, max_height=1000):
        self.settings = settings
        self.cameras = settings['cameras']
        self.sizes = {c: [images[c].shape[1], images[c].shape[0]] for c in self.cameras}
        calibration = settings['calibration']
        if len(self.cameras) > 1:
            expected = signature(settings, images)
            if not calibration or any(calibration.get(key) != expected[key] for key in expected):
                raise StitchError('相机选择、原图尺寸或转正角度已变化，需要重新对齐')
        raw, self.tree = graph_transforms(settings, calibration)
        rotation = np.eye(3)
        rotation[:2] = cv2.getRotationMatrix2D((0, 0), settings['view']['rotation_deg'], 1.)
        prepared = {}
        polygons = []
        for c in self.cameras:
            dx, dy = settings['offsets'][str(c)]
            shift = np.array([[1., 0, dx], [0, 1., dy], [0, 0, 1.]])
            transform = rotation @ shift @ check_matrix(raw[c])
            poly = project(corners(self.sizes[c]), transform)
            area = abs(cv2.contourArea(poly.astype(np.float32)))
            if not cv2.isContourConvex(poly.astype(np.float32)) or not .05 < area / np.prod(self.sizes[c]) < 20:
                raise StitchError('camera%d变形过大，请重新在同一平面选点' % c)
            prepared[c] = transform; polygons.append(poly)
        points = np.concatenate(polygons)
        low, high = np.floor(points.min(axis=0)+1e-7), np.ceil(points.max(axis=0)-1e-7)
        extent = high-low
        if np.any(extent <= 0) or extent[0] > 8*max(s[0] for s in self.sizes.values()) or extent[1] > 6*max(s[1] for s in self.sizes.values()):
            raise StitchError('输出画布过大，请减少位移或重新对齐')
        self.scale = float(min(1., max_width/extent[0], max_height/extent[1]))
        self.offset_to_output = self.scale*rotation[:2, :2]
        canvas = np.array([[self.scale, 0, -low[0]*self.scale], [0, self.scale, -low[1]*self.scale], [0, 0, 1.]])
        self.size = tuple(np.ceil(extent*self.scale-1e-7).astype(int))
        self.transforms = {c: canvas @ prepared[c] for c in self.cameras}
        self.masks = {c: cv2.warpPerspective(np.full(images[c].shape[:2], 255, np.uint8), self.transforms[c], self.size,
                      flags=cv2.INTER_NEAREST)>0 for c in self.cameras}
        self.canvas_size = self.size
        self.crop_rect = (0, 0, *self.size)
        covered = np.logical_or.reduce(list(self.masks.values()))
        self.retained_valid_fraction = 1.
        if settings['view']['crop_black']:
            centers = [project(np.array([np.array(self.sizes[c])/2]), self.transforms[c])[0] for c in self.cameras]
            inset = cv2.erode(covered.astype(np.uint8), np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT)>0
            x, y, w, h = largest_valid_rectangle(inset, [p[0] for p in centers], [p[1] for p in centers])
            if w*h < .25*covered.sum():
                raise StitchError('裁黑边会丢失太多视野，请关闭裁黑边')
            self.crop_rect = (x, y, w, h)
            move = np.array([[1., 0, -x], [0, 1., -y], [0, 0, 1.]])
            self.transforms = {c: move @ self.transforms[c] for c in self.cameras}
            self.masks = {c: m[y:y+h, x:x+w] for c, m in self.masks.items()}
            self.size = (w, h)
            self.retained_valid_fraction = float(w*h/covered.sum())
        self._check_overlap()
        warped = self.warp(images)
        self.gains = {c: np.ones(3, np.float32) for c in self.cameras}
        self.gains[settings['anchor']] = np.ones(3, np.float32)
        known = {settings['anchor']}
        for _ in self.cameras:
            for edge in self.tree:
                a, b = edge['a'], edge['b']
                if a in known and b not in known or b in known and a not in known:
                    if b in known: a, b = b, a
                    overlap = (self.masks[a] & self.masks[b])[::6, ::6]
                    if overlap.sum() < 10: continue
                    med_a = np.median(warped[a][::6, ::6][overlap], axis=0)
                    med_b = np.median(warped[b][::6, ::6][overlap], axis=0)
                    self.gains[b] = np.clip(self.gains[a]*med_a/np.maximum(med_b, 1), .8, 1.25).astype(np.float32)
                    known.add(b)
        self.seam_axis = None
        self.weights = self._weights()

    def _check_overlap(self):
        reached = {self.cameras[0]}
        for _ in self.cameras:
            for a, b in itertools.combinations(self.cameras, 2):
                if (a in reached) != (b in reached):
                    shared = (self.masks[a] & self.masks[b]).sum()
                    if shared >= max(32, .03*min(self.masks[a].sum(), self.masks[b].sum())):
                        reached.update((a, b))
        if len(reached) != len(self.cameras):
            raise StitchError('位移后画面失去有效重叠，请减少X/Y微调或重新建立对应点')

    def warp(self, images):
        if any([images[c].shape[1], images[c].shape[0]] != self.sizes[c] for c in self.cameras):
            raise StitchError('输入尺寸已改变，请重新对齐')
        return {c: cv2.warpPerspective(images[c], self.transforms[c], self.size, flags=cv2.INTER_LINEAR) for c in self.cameras}

    def _weights(self):
        width = self.settings['blend_width']
        if len(self.cameras) == 1:
            c = self.cameras[0]; return {c: self.masks[c].astype(np.float32)}
        if len(self.cameras) == 2:
            a, b = self.cameras
            centers = [project(np.array([np.array(self.sizes[c])/2]), self.transforms[c])[0] for c in (a, b)]
            vertical = abs(centers[0][1]-centers[1][1]) > abs(centers[0][0]-centers[1][0])
            self.seam_axis = 'vertical' if vertical else 'horizontal'
            ma, mb = self.masks[a], self.masks[b]
            if vertical: ma, mb = ma.T, mb.T
            both = ma & mb
            left = np.argmax(both, axis=1)
            right = both.shape[1]-1-np.argmax(both[:, ::-1], axis=1)
            seam = left+(right-left)*self.settings['seam_percent']/100
            x = np.arange(both.shape[1], dtype=np.float32)[None, :]
            w = np.clip((x-seam[:, None])/max(1, width)+.5, 0, 1)
            if width == 0: w = (x >= seam[:, None]).astype(np.float32)
            axis = 1 if vertical else 0
            if centers[0][axis] < centers[1][axis]: w = 1-w
            w[ma & ~mb] = 1; w[~ma] = 0
            if vertical: w = w.T
            wa = w.astype(np.float32)
            return {a: wa, b: self.masks[b].astype(np.float32)*(1-wa)}
        distances = []
        for c in self.cameras:
            padded = np.pad(self.masks[c].astype(np.uint8), 1)
            distances.append(cv2.distanceTransform(padded, cv2.DIST_L2, 3)[1:-1, 1:-1])
        owner = np.argmax(np.stack(distances), axis=0)
        weights = {}
        for i, c in enumerate(self.cameras):
            weight = (owner == i).astype(np.float32)
            if width:
                weight = cv2.GaussianBlur(weight, (2*width+1, 2*width+1), max(1, width/3))
            weights[c] = weight*self.masks[c]
        total = np.maximum(sum(weights.values()), 1e-6)
        return {c: weight/total for c, weight in weights.items()}

    def render(self, images):
        warped = self.warp(images)
        result = np.zeros((self.size[1], self.size[0], 3), np.float32)
        for c in self.cameras:
            result += warped[c].astype(np.float32)*self.gains[c]*self.weights[c][:, :, None]
        return np.clip(result, 0, 255).astype(np.uint8)

    def geometry(self):
        return dict(scale=self.scale, offset_to_output=self.offset_to_output.tolist(), canvas_size=list(map(int, self.canvas_size)), crop_rect=list(map(int, self.crop_rect)),
                    retained_valid_percent=round(self.retained_valid_fraction*100, 1), seam_axis=self.seam_axis,
                    cameras=[dict(camera=c, polygon=project(corners(self.sizes[c]), self.transforms[c]).round(2).tolist(),
                                  center=project(np.array([np.array(self.sizes[c])/2]), self.transforms[c])[0].round(2).tolist()) for c in self.cameras])
