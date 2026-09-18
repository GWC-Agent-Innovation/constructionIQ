import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import spec_store
from agent.layout_solver import solve_layout
from agent.schemas import HouseSpec, SessionState, Room
from agent.project_undo import is_undo_request
from agent.validator import validate_requirements
from backend.api import app


class ProjectUndoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        directory = patch.object(spec_store, 'DATA_DIR', Path(self.folder.name))
        directory.start(); self.addCleanup(directory.stop)
        self.client = TestClient(app); self.addCleanup(self.client.close)
        self.spec = HouseSpec(plot_width_ft=40, plot_length_ft=40, num_bedrooms=2,
                              num_bathrooms=2, attached_bathroom_bedrooms=[1, 2], style_notes='Original preference')
        first = solve_layout(self.spec)
        first.version = 1
        first.wall_colors = {'bedroom_0:north': '#123456'}
        second = first.model_copy(deep=True)
        second.version = 2
        second.wall_colors = {'bedroom_0:north': '#abcdef'}
        changed = self.spec.model_copy(update={'style_notes': 'Changed preference'})
        self.session = SessionState(session_id='undo', spec=changed, layout=second,
                                    layout_history=[first, second], model_3d_version=2,
                                    revision_specs={1: self.spec, 2: changed}, revision_parents={1: None, 2: 1})
        spec_store.save(self.session)

    def test_revoke_restores_exact_geometry_paint_and_requirements_without_ai(self):
        with patch('backend.api.run_turn', side_effect=AssertionError('Undo must not call AI')):
            response = self.client.post('/sessions/undo/messages', json={'message': 'revoke the changes', 'auto_generate': True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['edit_applied'])
        saved = spec_store.load('undo')
        self.assertEqual(saved.spec, self.spec)
        self.assertEqual(saved.layout.rooms, self.session.layout_history[0].rooms)
        self.assertEqual(saved.layout.wall_colors, {'bedroom_0:north': '#123456'})
        self.assertEqual(saved.layout.version, 3)
        self.assertEqual(saved.model_3d_version, 3)
        again = self.client.post('/sessions/undo/messages', json={'message': 'undo', 'auto_generate': True})
        self.assertFalse(again.json()['edit_applied'])
        self.assertEqual(spec_store.load('undo').layout.version, 3)

    def test_failed_undo_keeps_current_design(self):
        with patch('agent.pipeline.build_3d_assets', side_effect=RuntimeError('render failed')):
            response = self.client.post('/sessions/undo/messages', json={'message': 'undo last edit', 'auto_generate': True})
        self.assertFalse(response.json()['edit_applied'])
        saved = spec_store.load('undo')
        self.assertEqual(saved.spec, self.session.spec)
        self.assertEqual(saved.layout, self.session.layout)

    def test_legacy_undo_recovers_room_list(self):
        self.session.revision_specs = {}
        self.session.revision_parents = {}
        self.session.spec.must_have_rooms = ['Parking']
        spec_store.save(self.session)
        result = self.client.post('/sessions/undo/messages', json={'message': 'undo', 'auto_generate': True})
        self.assertTrue(result.json()['edit_applied'])
        saved = spec_store.load('undo')
        self.assertEqual(saved.spec.must_have_rooms, [])
        self.assertEqual(saved.layout.rooms, self.session.layout_history[0].rooms)

    def test_specific_undo_requests_are_not_misread_as_undo_last(self):
        self.assertTrue(is_undo_request('Please revoke the changes!'))
        self.assertFalse(is_undo_request('undo the kitchen change but keep parking'))

    def test_oversized_parking_is_rejected_for_any_planning_source(self):
        layout = self.session.layout.model_copy(deep=True)
        layout.rooms.append(Room(id='parking', name='Parking', x=0, y=0, width=20, length=40))
        for source in ('ai_arrangement', 'standard_solver'):
            layout.planning_source = source
            issues = validate_requirements(layout, self.spec)
            self.assertTrue(any('parking limit is 250' in issue.message for issue in issues))


if __name__ == '__main__':
    unittest.main()
