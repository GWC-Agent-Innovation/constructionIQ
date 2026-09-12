import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import spec_store
from agent.layout_agent import generate_layout
from agent.layout_solver import solve_layout
from agent.pipeline import generate_and_save
from agent.schemas import DesignHints, HouseSpec, SessionState
from agent.spec_agent import run_turn
from agent.validator import validate_layout
from backend.api import app


def spec(assignments=None):
    return HouseSpec(plot_width_ft=40, plot_length_ft=40, num_bedrooms=2,
                     num_bathrooms=3, attached_bathroom_bedrooms=assignments)


class ViewerEditTests(unittest.TestCase):
    def test_40_by_60_south_facing_home_with_spacious_living_and_ensuites(self):
        for assignments, count in (([2], 2), ([1, 2], 3)):
            for living_weight in (1.0, 1.4, 1.6):
                with self.subTest(assignments=assignments, living_weight=living_weight):
                    brief = HouseSpec(plot_width_ft=40, plot_length_ft=60,
                                      num_bedrooms=2, num_bathrooms=count,
                                      entrance_facing='south',
                                      must_have_rooms=['Living Room', 'Kitchen', 'Dining Hall'],
                                      attached_bathroom_bedrooms=assignments)
                    layout = solve_layout(brief, DesignHints(room_weights={'living room': living_weight}))
                    self.assertIsNotNone(layout)
                    self.assertEqual(validate_layout(layout, brief), [])

    def test_each_bathroom_connects_only_to_its_assigned_bedroom(self):
        for assignments in ([1, 2], [2], []):
            for facing in ('north', 'south', 'east', 'west'):
                with self.subTest(assignments=assignments, facing=facing):
                    brief = spec(assignments)
                    brief.entrance_facing = facing
                    layout = solve_layout(brief)
                    self.assertIsNotNone(layout)
                    self.assertEqual(validate_layout(layout, brief), [])
                    baths = [r for r in layout.rooms if r.ensuite_for]
                    self.assertEqual(len(baths), len(assignments))
                    for number in assignments:
                        bath = next(r for r in baths if r.name == f'Attached Bathroom {number}')
                        bedroom = next(r for r in layout.rooms if r.name == f'Bedroom {number}')
                        self.assertEqual(bath.ensuite_for, bedroom.id)
                        self.assertEqual([d.connects_to for d in bath.doors], [bedroom.id])

    def test_hint_count_attaches_to_distinct_bedrooms(self):
        layout = solve_layout(spec(), DesignHints(attached_bathrooms=2))
        baths = [r for r in layout.rooms if r.ensuite_for]
        self.assertEqual(len({r.ensuite_for for r in baths}), 2)

    def test_bedroom2_edit_is_saved_and_survives_hint_service_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)):
            session = SessionState(session_id='edit-test', spec=spec([1]))
            session.layout = solve_layout(session.spec)
            response = {'message': {'content': 'Requested an attached bathroom for Bedroom 2.',
                        'tool_calls': [{'function': {'name': 'update_house_spec',
                        'arguments': {'attached_bathroom_bedrooms': [1, 2]}}}]}}
            with patch('agent.spec_agent.chat', return_value=response) as chat:
                run_turn(session, 'in bedroom2 place a bathroom', auto_generate=True)
            self.assertIn('Current floor plan', chat.call_args.kwargs['messages'][0]['content'])
            spec_store.save(session)
            restored = spec_store.load(session.session_id)
            with patch('agent.layout_agent.chat', side_effect=TimeoutError('offline')):
                layout, issues = generate_layout(restored.spec, restored.layout)
            self.assertEqual(issues, [])
            self.assertEqual(len([r for r in layout.rooms if r.ensuite_for]), 2)

    def test_invalid_assignment_does_not_claim_success_without_retry(self):
        session = SessionState(session_id='test', spec=spec([1]))
        invalid = {'message': {'content': 'Done.', 'tool_calls': [{'function': {
            'name': 'update_house_spec', 'arguments': {'attached_bathroom_bedrooms': [3]}}}]}}
        with patch('agent.spec_agent.chat', side_effect=[invalid, {'message': {'content': 'There are only two bedrooms.'}}]) as chat:
            reply = run_turn(session, 'attach a bathroom to bedroom 3', True)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(session.spec.attached_bathroom_bedrooms, [1])
        self.assertIn('only two', reply)

    def test_impossible_assignments_do_not_silently_fall_back(self):
        self.assertIsNone(solve_layout(spec([3])))
        self.assertIsNone(solve_layout(spec([1, 1])))

    def test_failed_build_never_returns_old_viewer_even_after_reload(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            session = SessionState(session_id='test', spec=spec([1, 2]))
            spec_store.save(session)
            (Path(folder) / 'test_3d.html').write_text('old model')
            with patch('agent.pipeline.generate_layout', return_value=(solve_layout(session.spec), [])), \
                    patch('agent.pipeline.build_3d_assets', side_effect=RuntimeError('Blender failed')):
                response = client.post('/sessions/test/generate', json={'build_3d': True})
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()['viewer_url'])
            self.assertIn('Blender failed', response.json()['warnings'][0])
            self.assertIsNone(client.get('/sessions/test').json()['viewer_url'])

    def test_successful_build_records_revision(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)):
            session = SessionState(session_id='test', spec=spec([2]))
            with patch('agent.pipeline.generate_layout', return_value=(solve_layout(session.spec), [])), \
                    patch('agent.pipeline.build_3d_assets', return_value=Path(folder) / 'test_3d.html'):
                output, warnings = generate_and_save(session)
            self.assertIsNotNone(output)
            self.assertEqual(warnings, [])
            restored = spec_store.load('test')
            self.assertEqual(restored.model_3d_version, restored.layout.version)


if __name__ == '__main__':
    unittest.main()
