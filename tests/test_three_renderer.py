import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import spec_store
from agent.layout_solver import solve_layout
from agent.model3d import build_scene
from agent.pipeline import build_3d_assets
from agent.schemas import HouseSpec, SessionState
from agent.viewer_html import build_viewer_html
from backend.api import app


class ThreeRendererTests(unittest.TestCase):
    def setUp(self):
        self.spec = HouseSpec(plot_width_ft=40, plot_length_ft=60,
                              num_bedrooms=2, num_bathrooms=1,
                              entrance_facing='south')
        self.layout = solve_layout(self.spec)
        self.assertIsNotNone(self.layout)

    def test_embedded_geometry_matches_validated_scene_and_escapes_names(self):
        self.layout.rooms[0].name = '</script><script>alert(1)</script>'
        html = build_viewer_html(self.layout, self.spec)
        embedded = re.search(r'<script id="scene-data" type="application/json">(.*?)</script>', html).group(1)
        self.assertEqual(json.loads(embedded), build_scene(self.layout).to_dict())
        self.assertNotIn('</script>', embedded)
        self.assertNotIn('GLTFLoader', html)
        self.assertIn('function buildHouse(data)', html)

    def test_build_needs_no_blender_assets(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)):
            with patch('agent.blender_runner.run_blender', side_effect=AssertionError('Blender must not run')):
                viewer = build_3d_assets('three-test', self.layout, self.spec)
            self.assertTrue(viewer.is_file())
            self.assertTrue((Path(folder) / 'three-test_scene.json').is_file())
            self.assertFalse(list(Path(folder).glob('*.glb')))

    def test_saved_viewer_uses_three_and_stale_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            session = SessionState(session_id='three-test', spec=self.spec, layout=self.layout)
            spec_store.save(session)
            (Path(folder) / 'three-test_3d.html').write_text('legacy Blender viewer')
            url = '/sessions/three-test/assets/three-test_3d.html'
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('function buildHouse(data)', response.text)
            self.assertNotIn('legacy Blender viewer', response.text)
            session.model_3d_version = 0
            spec_store.save(session)
            self.assertEqual(client.get(url).status_code, 404)


if __name__ == '__main__':
    unittest.main()
