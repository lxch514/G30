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
from core import Composer, StitchError, atomic_json, estimate_upright, largest_valid_rectangle, project, view_config


class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv2.setNumThreads(2)
        scene = np.random.default_rng(20261020).integers(20, 230, (240, 540, 3), dtype=np.uint8)
        cls.images = [scene[:, 140:].copy(), scene[:, :400].copy()]
        cls.calibration = dict(version=1, source_cameras=[3, 4], sizes=[[400, 240]] * 2,
            H_4_to_3=[[1., 0, -140], [0, 1, 0], [0, 0, 1]], seam_fraction=.66, report={})

    def test_bad_view_values_rejected(self):
        for value in [[], {'projection':'unknown'}, {'rotation_deg':float('nan')},
                      {'rotation_deg':16}, {'rotation_deg':True}, {'crop_black':'true'}]:
            with self.subTest(value=value), self.assertRaises(StitchError):
                view_config(value)

    def test_auto_rotation_cancels_known_roll(self):
        image = np.zeros((480, 640, 3), np.uint8)
        for x in (70, 170, 270, 370, 470, 570):
            cv2.line(image, (x, 40), (x, 440), (255, 255, 255), 3)
        image = cv2.warpAffine(image, cv2.getRotationMatrix2D((320, 240), 4, 1), (640, 480))
        correction, report = estimate_upright(image)
        self.assertLess(abs(correction + 4), .25)
        self.assertFalse(report['lens_calibrated'])

    def test_no_reference_lines_rejected(self):
        with self.assertRaises(StitchError):
            estimate_upright(np.zeros((240, 400, 3), np.uint8))

    def test_largest_rectangle_matches_bruteforce(self):
        mask = np.ones((8, 11), bool); mask[:3, :2] = False; mask[5:, 7:] = False
        rect = largest_valid_rectangle(mask, [3., 6.])
        x, y, width, height = rect
        self.assertTrue(mask[y:y+height, x:x+width].all())
        best = 0
        for left in range(4):
            for right in range(7, 12):
                for top in range(8):
                    for bottom in range(top+1, 9):
                        if mask[top:bottom, left:right].all():
                            best = max(best, (right-left)*(bottom-top))
        self.assertEqual(width*height, best)

    def test_projection_rotation_keep_corresponding_points_aligned(self):
        for mode in ('balanced', 'camera3', 'camera4'):
            composer = Composer(copy.deepcopy(self.calibration), self.images, view=dict(
                projection=mode, rotation_deg=3., crop_black=True))
            a = project(np.array([[60., 90], [210, 160]]), composer.transforms[0])
            b = project(np.array([[200., 90], [350, 160]]), composer.transforms[1])
            self.assertLess(float(np.max(np.abs(a-b))), 1e-5)
            self.assertTrue((composer.masks[0] | composer.masks[1]).all())
            self.assertGreater(composer.retained_valid_fraction, .7)



if __name__ == '__main__':
    unittest.main(verbosity=2)
