"""Configuration regression checks; no cameras are opened by these tests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from g30_service import load_config

class ConfigTests(unittest.TestCase):
    def config(self, value):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            path.write_text(json.dumps(value))
            return load_config(path)

    def test_existing_config_and_display_rotations(self):
        value=json.loads((Path(__file__).resolve().parents[2]/'config/g30.json').read_text())
        value.pop('display_rotation_degrees', None)
        self.assertEqual(self.config(value)['lease_seconds'], 12)
        self.assertEqual(self.config(value)['display_rotation_degrees'], [0]*6)
        value['display_rotation_degrees']=[180,0,180,0,180,0]
        self.assertEqual(self.config(value)['display_rotation_degrees'], value['display_rotation_degrees'])

    def test_reject_malformed_display_rotation(self):
        value=json.loads((Path(__file__).resolve().parents[2]/'config/g30.json').read_text())
        for angles in ([180]*5,[90]*6,[True]*6,['180']*6,None,180):
            with self.subTest(angles=angles):
                with self.assertRaises(ValueError):
                    self.config(dict(value,display_rotation_degrees=angles))

if __name__=='__main__':unittest.main()
