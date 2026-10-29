import copy
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import StitchError, atomic_json, project
from mosaic import MultiComposer, automatic_graph, graph_transforms, migrate_legacy, normalize_project, orient, signature
from project_app import App


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv2.setNumThreads(2)
        cls.scene = cv2.GaussianBlur(np.random.default_rng(2801).integers(15, 235, (360, 960, 3), np.uint8), (3, 3), 0)
        cls.images = {0: cls.scene[:, :520].copy(), 2: cls.scene[:, 240:760].copy(), 4: cls.scene[:, 440:].copy()}
        cls.settings = normalize_project(dict(cameras=[0, 2, 4], anchor=0, rotations=[0]*6, blend_width=0))
        cls.settings['calibration'] = dict(signature(cls.settings, cls.images), edges=[cls.edge(0, 2, 240, 0), cls.edge(2, 4, 200, 0)])

    @staticmethod
    def edge(a, b, x, y):
        return dict(a=a, b=b, H_b_to_a=[[1.,0,x],[0,1.,y],[0,0,1]], report=dict(method='manual',inliers=5,median_error_px=0))

    def test_arbitrary_camera_ids_and_three_view_union(self):
        engine = MultiComposer(copy.deepcopy(self.settings), self.images)
        result = engine.render(self.images)
        self.assertEqual(result.shape, self.scene.shape)
        self.assertLess(float(np.abs(result.astype(float)-self.scene).mean()), .5)
        self.assertTrue(np.logical_or.reduce(list(engine.masks.values())).all())

    def test_automatic_chain_recovers_known_geometry(self):
        settings = copy.deepcopy(self.settings)
        settings['calibration'], _ = automatic_graph(settings, self.images)
        transforms, _ = graph_transforms(settings, settings['calibration'])
        point = project(np.array([[40.,100],[200,220]]), transforms[4])
        self.assertLess(float(np.max(abs(point-[[480,100],[640,220]]))), 1.)

    def test_vertical_overlap_fuses_above_and_below(self):
        scene = cv2.resize(self.scene, (400, 720))
        images = {1:scene[:440].copy(), 5:scene[280:].copy()}
        settings = normalize_project(dict(cameras=[1,5],anchor=1,rotations=[0]*6,blend_width=12))
        settings['calibration'] = dict(signature(settings,images),edges=[self.edge(1,5,0,280)])
        engine = MultiComposer(settings, images)
        self.assertEqual(engine.seam_axis,'vertical')
        self.assertLess(float(np.abs(engine.render(images).astype(float)-scene).mean()), .5)

    def test_disconnected_selection_and_excessive_move_rejected(self):
        settings = copy.deepcopy(self.settings)
        settings['calibration']['edges'].pop()
        with self.assertRaisesRegex(StitchError,'camera4'):
            MultiComposer(settings,self.images)
        settings=copy.deepcopy(self.settings);settings['offsets']['4']=[2000,0]
        with self.assertRaisesRegex(StitchError,'重叠'):
            MultiComposer(settings,self.images)

    def test_invalid_project_and_changed_rotation_rejected(self):
        for value in [dict(cameras=[]),dict(cameras=[0,0]),dict(cameras=[True]),dict(cameras=[6]),
                      dict(rotations=[180]*5),dict(rotations=[90]*6),dict(offsets={'0':[float('nan'),0]})]:
            with self.subTest(value=value),self.assertRaises(StitchError):normalize_project(value)
        settings=copy.deepcopy(self.settings);settings['rotations'][4]=180
        with self.assertRaisesRegex(StitchError,'转正角度'):
            MultiComposer(settings,self.images)

    def test_legacy_correspondence_is_conjugated_after_180_turn(self):
        old=dict(sizes=[[520,360],[520,360]],H_4_to_3=[[1.,0,240],[0,1,0],[0,0,1]],seam_fraction=.3,report={})
        settings=migrate_legacy(old,dict(projection='camera3',rotation_deg=0,crop_black=False))
        transform=settings['calibration']['edges'][0]['H_b_to_a']
        p=project(np.array([[519-100.,359-80]]),np.array(transform))
        self.assertLess(float(abs(p-[[519-340,359-80]]).max()),1e-6)
        self.assertAlmostEqual(settings['seam_percent'],70)

    def app(self,root,settings=None):
        frames=root/'frames';frames.mkdir()
        for c,im in self.images.items():cv2.imwrite(str(frames/('cam%d.jpg'%c)),im)
        atomic_json(root/'config/project.json',self.settings if settings is None else settings)
        config=dict(source_dir=str(frames),max_input_age_seconds=3,max_publication_skew_ms=80,
                    max_width=1600,max_height=1000,jpeg_quality=82,fps=8)
        return App(config,root)

    def test_saved_position_and_view_are_reloaded(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);app=self.app(root)
            app.update_geometry(dict(camera=2,x=3,y=0),'position')
            app.update_geometry(dict(rotation_deg=1.2,crop_black=False),'view')
            reloaded=App(app.config,root)
            self.assertEqual(reloaded.project['offsets']['2'],[3.,0.])
            self.assertEqual(reloaded.project['view']['rotation_deg'],1.2)
            self.assertEqual(reloaded.project['calibration'],self.settings['calibration'])

    def test_disk_write_failure_preserves_running_engine_and_file(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);app=self.app(root);app.engine=app.composer(app.project,self.images)
            before=app.path.read_bytes();old=app.engine
            with patch('project_app.atomic_json',side_effect=OSError('simulated write failure')):
                with self.assertRaises(OSError):app.update_geometry(dict(camera=2,x=3,y=0),'position')
            self.assertEqual(app.path.read_bytes(),before)
            self.assertIs(app.engine,old)

    def test_failed_sample_save_preserves_accepted_calibration(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);app=self.app(root);before=app.path.read_bytes()
            with patch('project_app.cv2.imwrite',return_value=False):
                with self.assertRaises(StitchError):app.accept_calibration(copy.deepcopy(app.project),copy.deepcopy(app.project['calibration']),self.images,self.scene)
            self.assertEqual(app.path.read_bytes(),before)
            self.assertIsNone(app.engine)

    def test_manual_connections_accumulate_until_all_sources_connected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);settings=copy.deepcopy(self.settings);settings['calibration']=None
            app=self.app(root,settings);capture=app.capture()
            points=np.array([[280,40],[480,40],[480,320],[280,320],[380,180]],float)
            result=app.calibrate(dict(token=capture['token'],a=0,b=2,pointsA=(points/[520,360]).tolist(),pointsB=((points-[240,0])/[520,360]).tolist()),True)
            self.assertFalse(result['complete'])
            points=np.array([[240,40],[480,40],[480,320],[240,320],[360,180]],float)
            result=app.calibrate(dict(token=capture['token'],a=2,b=4,pointsA=(points/[520,360]).tolist(),pointsB=((points-[200,0])/[520,360]).tolist()),True)
            self.assertTrue(result['complete'])
            self.assertEqual(len(app.project['calibration']['edges']),2)

    def test_single_source_preview_and_expired_manual_token(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);app=self.app(root);capture=app.capture()
            app.save_project(dict(cameras=[4],anchor=4,rotations=[0]*6))
            self.assertIsNotNone(app.engine)
            with self.assertRaisesRegex(StitchError,'过期'):
                app.calibrate(dict(token=capture['token'],a=0,b=2,pointsA=[],pointsB=[]),True)

    def test_frozen_and_live_source_previews_use_the_same_rotation(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);settings=copy.deepcopy(self.settings);settings['rotations']=[180]*6;settings['calibration']=None
            app=self.app(root,settings);app.capture()
            frozen=cv2.imdecode(np.frombuffer(app.source(0,True),np.uint8),cv2.IMREAD_COLOR)
            live=cv2.imdecode(np.frombuffer(app.source(0,False),np.uint8),cv2.IMREAD_COLOR)
            self.assertTrue(np.array_equal(frozen,live))
            original=cv2.imread(str(root/'frames/cam0.jpg'))
            expected=orient(original,180)
            self.assertLess(float(np.abs(frozen.astype(float)-expected).mean()),10)


if __name__=='__main__':unittest.main(verbosity=2)
