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
    def test_wide_plot_after_bedroom_and_common_bathroom_removal(self):
        brief = HouseSpec(plot_width_ft=50, plot_length_ft=50, num_bedrooms=1,
                          num_bathrooms=1, entrance_facing='west',
                          attached_bathroom_bedrooms=[1], must_have_rooms=['pooja room'])
        layout = solve_layout(brief)
        self.assertIsNotNone(layout)
        self.assertEqual(validate_layout(layout, brief), [])
        baths = [r for r in layout.rooms if 'bath' in r.name.lower()]
        self.assertEqual(len(baths), 1)
        self.assertEqual(baths[0].ensuite_for, 'bedroom_0')

    def test_exact_removals_update_counts_without_llm(self):
        session = SessionState(session_id='test', spec=spec([1, 2]))
        with patch('agent.spec_agent.chat') as chat:
            run_turn(session, 'remove the common bathroom', True)
            self.assertEqual(session.spec.num_bathrooms, 2)
            self.assertEqual(session.spec.attached_bathroom_bedrooms, [1, 2])
            run_turn(session, 'remove bedroom 1', True)
            self.assertEqual(session.spec.num_bedrooms, 1)
            self.assertEqual(session.spec.num_bathrooms, 1)
            self.assertEqual(session.spec.attached_bathroom_bedrooms, [1])
            reply = run_turn(session, 'remove the att.bath1', True)
            self.assertIn('last bathroom', reply)
            self.assertEqual(session.spec.num_bathrooms, 1)
            chat.assert_not_called()

    def test_ambiguous_removal_does_not_rebuild(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            session = SessionState(session_id='test', spec=spec([1, 2]))
            spec_store.save(session)
            with patch('backend.api.generate_and_save') as generate:
                result = client.post('/sessions/test/messages', json={
                    'message': 'remove the bathroom', 'auto_generate': True})
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()['edit_applied'])
            self.assertIn('Which bathroom', result.json()['reply'])
            self.assertEqual(spec_store.load('test').spec, session.spec)
            generate.assert_not_called()

    def test_edit_generation_failure_keeps_requirements_and_layout(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            session = SessionState(session_id='test', spec=spec([1, 2]))
            session.layout = solve_layout(session.spec)
            spec_store.save(session)
            with patch('agent.pipeline.generate_layout', return_value=(None, ['No supported arrangement.'])):
                result = client.post('/sessions/test/messages', json={
                    'message': 'remove the common bathroom', 'auto_generate': True})
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()['edit_applied'])
            self.assertIn('previous design has been kept', result.json()['reply'])
            restored = spec_store.load('test')
            self.assertEqual(restored.spec, session.spec)
            self.assertEqual(restored.layout, session.layout)
            self.assertEqual(restored.history[-1].content, result.json()['reply'])

    def test_edit_render_failure_preserves_existing_assets(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            session = SessionState(session_id='test', spec=spec([1, 2]))
            session.layout = solve_layout(session.spec)
            session.model_3d_version = session.layout.version
            spec_store.save(session)
            viewer = Path(folder) / 'test_3d.html'
            viewer.write_text('existing viewer')
            with patch('agent.layout_agent._get_design_hints', return_value=DesignHints()), \
                    patch('agent.pipeline.build_3d_assets', side_effect=RuntimeError('render failed')):
                result = client.post('/sessions/test/messages', json={
                    'message': 'remove the common bathroom', 'auto_generate': True})
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()['edit_applied'])
            self.assertIn('render failed', result.json()['reply'])
            self.assertEqual(viewer.read_text(), 'existing viewer')
            restored = spec_store.load('test')
            self.assertEqual(restored.spec, session.spec)
            self.assertEqual(restored.layout, session.layout)
            self.assertEqual(restored.model_3d_version, session.model_3d_version)

    def test_common_bathroom_edit_builds_and_commits_in_one_request(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            session = SessionState(session_id='test', spec=spec([1, 2]))
            session.layout = solve_layout(session.spec)
            session.layout_history = [session.layout]
            spec_store.save(session)
            with patch('agent.layout_agent._get_design_hints', return_value=DesignHints()):
                result = client.post('/sessions/test/messages', json={
                    'message': 'remove the common bathroom', 'auto_generate': True})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()['edit_applied'])
            self.assertTrue(result.json()['viewer_url'])
            restored = spec_store.load('test')
            self.assertEqual(restored.spec.num_bathrooms, 2)
            self.assertEqual(restored.spec.attached_bathroom_bedrooms, [1, 2])
            self.assertEqual(validate_layout(restored.layout, restored.spec), [])
            self.assertEqual(restored.model_3d_version, restored.layout.version)
            self.assertGreater(restored.layout.version, session.layout.version)
            viewer = (Path(folder) / 'test_3d.html').read_text(encoding='utf-8')
            self.assertIn('if (!chatResult.edit_applied)', viewer)
            self.assertNotIn('/generate`', viewer)

    def test_unusable_soft_hints_fall_back_to_valid_layout(self):
        brief = spec([1, 2])
        valid = solve_layout(brief)
        with patch('agent.layout_agent._get_design_hints', return_value=DesignHints()), \
                patch('agent.layout_agent.solve_layout', side_effect=[None, valid]):
            layout, issues = generate_layout(brief)
        self.assertIs(layout, valid)
        self.assertEqual(issues, [])

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
                        bath = next(r for r in baths if r.name == f'Att. Bath {number}')
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
