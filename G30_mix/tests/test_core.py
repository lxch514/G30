import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import copy
import json
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Composer, StitchError, automatic, decode_pair, manual, project, read_frame


class RegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv2.setNumThreads(2)
        rng = np.random.default_rng(20261020)
        cls.scene = cv2.GaussianBlur(rng.integers(0, 256, (360, 840, 3), dtype=np.uint8), (3, 3), 0)
        for x in range(40, 800, 60):
            cv2.circle(cls.scene, (x, 70 + x % 220), 16, (20, 240, 80), 2)
        cls.images = [cls.scene[:, 200:].copy(), cls.scene[:, :640].copy()]
        cls.H = np.array([[1., 0, -200], [0, 1., 0], [0, 0, 1.]])
        cls.calibration = dict(version=1, source_cameras=[3, 4], sizes=[[640, 360]] * 2,
                               H_4_to_3=cls.H.tolist(), report={})

    def test_automatic_known_overlap(self):
        calibration, _ = automatic(self.images, 1.)
        expected = np.array([[150., 100], [350, 240]])
        actual = project(expected + [200, 0], np.array(calibration['H_4_to_3']))
        self.assertLess(float(np.max(np.abs(actual - expected))), 1.)

    def test_real_union_content_is_preserved(self):
        result = Composer(self.calibration, self.images, 1600, 1000).render(self.images)
        self.assertEqual(result.shape, self.scene.shape)
        self.assertLess(float(np.abs(result.astype(float) - self.scene).mean()), .5)

    def test_manual_known_overlap(self):
        p4 = np.array([[240, 30], [600, 35], [600, 320], [240, 320], [430, 180]], float)
        p3 = p4 - [200, 0]
        calibration, _ = manual(self.images, p3 / [640, 360], p4 / [640, 360])
        self.assertLess(np.max(np.abs(project(p4, np.array(calibration['H_4_to_3'])) - p3)), .01)

    def test_degenerate_manual_points_rejected(self):
        with self.assertRaises(StitchError):
            manual(self.images, [[.5, .5]] * 4, [[.5, .5]] * 4)

    def test_unrelated_blank_images_rejected(self):
        with self.assertRaises(StitchError):
            automatic([np.zeros_like(im) for im in self.images])

    def test_nonoverlapping_transform_rejected(self):
        candidate = dict(self.calibration, H_4_to_3=[[1, 0, 1500], [0, 1, 0], [0, 0, 1]])
        with self.assertRaises(StitchError):
            Composer(candidate, self.images)

    def test_changed_resolution_requires_new_calibration(self):
        with self.assertRaises(StitchError):
            Composer(self.calibration, [im[::2, ::2] for im in self.images])

    def test_projective_pole_rejected(self):
        with self.assertRaises(StitchError):
            project(np.array([[0, 0], [640, 0], [640, 360], [0, 360]]),
                    np.array([[1., 0, 0], [0, 1, 0], [-.003, 0, 1]]))

    def test_stale_input_rejected(self):
        root = Path(__file__).resolve().parents[1] / 'run'; root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as name:
            path = Path(name) / 'cam3.jpg'
            cv2.imwrite(str(path), self.images[0]); os.utime(path, (time.time() - 10,) * 2)
            with self.assertRaises(StitchError):
                read_frame(name, 3)



if __name__ == '__main__':
    unittest.main(verbosity=2)
