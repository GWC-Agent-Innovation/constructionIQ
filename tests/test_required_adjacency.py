import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import spec_store
from agent.layout_solver import solve_layout
from agent.schemas import HouseSpec, RequiredAdjacency, SessionState
from agent.spec_agent import run_turn
from agent.validator import validate_layout
from backend.api import app


class RequiredAdjacencyTests(unittest.TestCase):
    def setUp(self):
        self.spec = HouseSpec(plot_width_ft=40, plot_length_ft=40, num_bedrooms=2,
                              num_bathrooms=2, attached_bathroom_bedrooms=[1, 2])
        self.relation = RequiredAdjacency(a='Kitchen', b='Dining Room')

    def test_original_separated_kitchen_is_rejected_for_explicit_edit(self):
        old = solve_layout(self.spec)
        self.assertIsNotNone(old)
        self.spec.required_adjacencies = [self.relation]
        issues = validate_layout(old, self.spec)
        self.assertTrue(any('Kitchen must be next to Dining Room' in issue.message for issue in issues))

    def test_fallback_honors_adjacency_instead_of_dropping_it(self):
        self.spec.required_adjacencies = [self.relation]
        layout = solve_layout(self.spec)
        self.assertIsNotNone(layout)
        self.assertEqual(validate_layout(layout, self.spec), [])
        self.assertEqual(len([r for r in layout.rooms if r.ensuite_for]), 2)

    def test_common_bathroom_stack_does_not_separate_kitchen_and_dining(self):
        for width, length, beds, baths, assigned, facing in (
            (40,40,2,3,[1,2],'north'), (40,50,3,2,[1],'east'),
            (45,45,3,3,[1,2],'west'), (30,40,2,2,[],'south'),
        ):
            with self.subTest(width=width, beds=beds, facing=facing):
                spec = HouseSpec(plot_width_ft=width, plot_length_ft=length,
                                 num_bedrooms=beds, num_bathrooms=baths,
                                 attached_bathroom_bedrooms=assigned, entrance_facing=facing,
                                 required_adjacencies=[self.relation])
                layout = solve_layout(spec)
                self.assertIsNotNone(layout)
                self.assertEqual(validate_layout(layout, spec), [])

    def test_exact_request_and_typo_record_real_constraint_offline(self):
        session = SessionState(session_id='test', spec=self.spec)
        with patch('agent.spec_agent.chat', side_effect=AssertionError('Must not call AI')):
            for text in ('attach the ketchen near the dining room', 'put kitchen next to dining room'):
                attempt = []
                run_turn(session, text, True, attempt)
                self.assertEqual(attempt, [True])
        self.assertEqual(session.spec.required_adjacencies, [self.relation])

    def test_edit_api_only_reports_success_with_actual_adjacent_geometry(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            original = solve_layout(self.spec)
            session = SessionState(session_id='test', spec=self.spec, layout=original, layout_history=[original])
            spec_store.save(session)
            with patch('agent.spec_agent.chat', side_effect=AssertionError('Offline test')), \
                    patch('agent.layout_agent.chat', side_effect=TimeoutError('Offline test')):
                response = client.post('/sessions/test/messages', json={
                    'message': 'attach the ketchen near the dining room', 'auto_generate': True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['edit_applied'])
            saved = spec_store.load('test')
            self.assertEqual(saved.spec.required_adjacencies, [self.relation])
            self.assertEqual(validate_layout(saved.layout, saved.spec), [])


if __name__ == '__main__':
    unittest.main()
