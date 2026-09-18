import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import spec_store
from agent.layout_solver import solve_layout
from agent.pipeline import generate_and_save
from agent.schemas import HouseSpec, SessionState
from backend.api import app


class WallPaintTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        directory = patch.object(spec_store, 'DATA_DIR', Path(self.folder.name))
        directory.start()
        self.addCleanup(directory.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        spec = HouseSpec(plot_width_ft=40, plot_length_ft=40, num_bedrooms=2, num_bathrooms=1)
        layout = solve_layout(spec)
        self.session = SessionState(session_id='paint-test', spec=spec, layout=layout,
                                    layout_history=[layout], model_3d_version=layout.version)
        spec_store.save(self.session)
        (Path(self.folder.name) / 'paint-test_3d.html').write_text('old viewer')
        self.room_id = layout.rooms[0].id
        self.body = dict(layout_version=layout.version, room_id=self.room_id, wall='north', color='#A9B8A3')

    def paint(self, **changes):
        return self.client.post('/sessions/paint-test/wall-paint', json={**self.body, **changes})

    def test_color_persists_and_is_embedded_on_reopen(self):
        self.assertEqual(self.paint().status_code, 200)
        key = self.room_id + ':north'
        saved = spec_store.load('paint-test')
        self.assertEqual(saved.layout.wall_colors, {key: '#a9b8a3'})
        self.assertEqual(saved.layout_history[-1].wall_colors, saved.layout.wall_colors)
        self.assertEqual(saved.layout.version, self.session.layout.version)
        self.assertEqual(saved.layout.rooms, self.session.layout.rooms)
        html = self.client.get('/sessions/paint-test/assets/paint-test_3d.html').text
        embedded = re.search(r'<script id="paint-data" type="application/json">(.*?)</script>', html)[1]
        self.assertEqual(json.loads(embedded), {key: '#a9b8a3'})

    def test_independent_walls_and_reset(self):
        self.paint()
        self.paint(wall='south', color='#8daabe')
        self.assertEqual(self.paint(color=None).status_code, 200)
        self.assertEqual(spec_store.load('paint-test').layout.wall_colors, {self.room_id + ':south': '#8daabe'})

    def test_invalid_and_stale_paint_does_not_change_project(self):
        for changes, code in [({'color': 'red'}, 422), ({'color': '#fff<script>'}, 422),
                              ({'wall': 'ceiling'}, 422), ({'room_id': 'unknown'}, 404),
                              ({'layout_version': 100}, 409)]:
            with self.subTest(changes=changes):
                self.assertEqual(self.paint(**changes).status_code, code)
                self.assertEqual(spec_store.load('paint-test').layout.wall_colors, {})

    def test_rebuild_retains_paint_for_matching_rooms_only(self):
        self.paint()
        session = spec_store.load('paint-test')
        session.layout.wall_colors['removed_room:north'] = '#123456'
        with patch('agent.pipeline.generate_layout', return_value=(solve_layout(session.spec), [])):
            output, issues = generate_and_save(session, build_3d=False)
        self.assertIsNotNone(output)
        self.assertEqual(issues, [])
        self.assertEqual(session.layout.wall_colors, {self.room_id + ':north': '#a9b8a3'})


if __name__ == '__main__':
    unittest.main()
