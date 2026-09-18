import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import spec_store
from agent.ai_arrangements import compile_proposal, valid_proposals
from agent.layout_agent import _get_design_hints, generate_layout
from agent.pipeline import generate_and_save
from agent.schemas import DesignHints, HouseSpec, LayoutProposal, SessionState
from agent.validator import validate_layout
from backend.api import app


def proposal(mirror=False):
    # No hallway: living and dining provide direct access to the northern rooms.
    nodes = [
        dict(id='root', axis='y', fraction=1/3, first='north', second='south'),
        dict(id='north', axis='x', fraction=.5, first='bed' if not mirror else 'service', second='service' if not mirror else 'bed'),
        dict(id='service', axis='x', fraction=1/3 if not mirror else 2/3,
             first='bath' if not mirror else 'kitchen', second='kitchen' if not mirror else 'bath'),
        dict(id='south', axis='x', fraction=.5, first='living', second='dining'),
        dict(id='bed', room_key='bedroom_0'), dict(id='bath', room_key='bath_0'),
        dict(id='kitchen', room_key='kitchen'), dict(id='living', room_key='living_0'),
        dict(id='dining', room_key='dining_1'),
    ]
    return LayoutProposal(name='West bedroom' if not mirror else 'East bedroom', root='root', nodes=nodes)


class ArrangementTests(unittest.TestCase):
    def setUp(self):
        self.spec = HouseSpec(plot_width_ft=30, plot_length_ft=30, num_bedrooms=1,
                              num_bathrooms=1, entrance_facing='south', attached_bathroom_bedrooms=[])

    def test_ai_controls_two_distinct_valid_arrangements_without_fixed_hallway(self):
        hints = DesignHints(layout_proposals=[proposal(), proposal(True)])
        layouts, errors = valid_proposals(self.spec, hints)
        self.assertEqual(errors, [])
        self.assertEqual(len(layouts), 2)
        for layout in layouts:
            self.assertEqual(validate_layout(layout, self.spec), [])
            self.assertFalse(any('Hallway' in r.name for r in layout.rooms))
            self.assertAlmostEqual(sum(r.width * r.length for r in layout.rooms), 900)
            self.assertEqual(layout.planning_source, 'ai_arrangement')
        west = next(r for r in layouts[0].rooms if r.id == 'bedroom_0')
        east = next(r for r in layouts[1].rooms if r.id == 'bedroom_0')
        self.assertEqual(west.x, 0)
        self.assertEqual(east.x, 15)

    def test_invalid_trees_and_small_rooms_rejected(self):
        for kind in ('cycle', 'duplicate', 'unknown', 'unused', 'missing', 'undersized', 'invalid_split'):
            candidate = proposal()
            if kind == 'cycle': candidate.nodes[0].first = 'root'
            if kind == 'duplicate': candidate.nodes[-1].room_key = 'living_0'
            if kind == 'unknown': candidate.nodes[-1].room_key = 'invented'
            if kind == 'unused': candidate.nodes.append(candidate.nodes[-1].model_copy(update={'id': 'unused'}))
            if kind == 'missing': candidate.nodes[-1].room_key = 'hall_1'
            if kind == 'undersized': candidate.nodes[2].fraction = .1
            if kind == 'invalid_split': candidate.nodes[0].axis = None
            with self.subTest(kind=kind):
                layout, errors = compile_proposal(self.spec, DesignHints(), candidate)
                self.assertIsNone(layout)
                self.assertTrue(errors)

    def test_assignment_cannot_be_dropped_by_ai(self):
        self.spec.attached_bathroom_bedrooms = [1]
        layout, errors = compile_proposal(self.spec, DesignHints(), proposal())
        self.assertIsNone(layout)
        self.assertTrue(errors)

    def test_duplicate_geometries_not_presented_as_alternatives(self):
        layouts, _ = valid_proposals(self.spec, DesignHints(layout_proposals=[proposal(), proposal()]))
        self.assertEqual(len(layouts), 1)

    def test_large_leftover_hallway_is_rejected(self):
        candidate = proposal()
        candidate.root = 'outer'
        candidate.nodes.extend([
            dict(id='outer', axis='x', fraction=.4, first='hall', second='root'),
            dict(id='hall', room_key='hall_1'),
        ])
        candidate = LayoutProposal.model_validate(candidate.model_dump())
        spec = self.spec.model_copy(update={'plot_width_ft': 50})
        layout, errors = compile_proposal(spec, DesignHints(), candidate)
        self.assertIsNone(layout)
        self.assertIn('Hallways use 40.0%', ' '.join(errors))

    def test_all_corridors_count_toward_total_area_limit(self):
        candidate = proposal()
        candidate.root = 'outer'
        candidate.nodes.extend([
            dict(id='outer', axis='y', fraction=.125, first='top_hall', second='lower'),
            dict(id='top_hall', room_key='hall_1'),
            dict(id='lower', axis='x', fraction=.125, first='left_hall', second='root'),
            dict(id='left_hall', room_key='hall_2'),
        ])
        candidate = LayoutProposal.model_validate(candidate.model_dump())
        spec = self.spec.model_copy(update={'plot_width_ft': 40, 'plot_length_ft': 40})
        layout, errors = compile_proposal(spec, DesignHints(), candidate)
        self.assertIsNone(layout)
        self.assertIn('maximum is 15%', ' '.join(errors))

    def test_invalid_proposal_gets_specific_feedback_before_repair(self):
        bad = proposal()
        bad.nodes[0].first = 'root'
        def response(candidate):
            return {'message': {'tool_calls': [{'function': {'name': 'plan_design_hints', 'arguments': {
                'planning_notes': [], 'layout_proposals': [candidate.model_dump(exclude_none=True)]}}}]}}
        with patch('agent.layout_agent.chat', side_effect=[response(bad), response(proposal())]) as chat:
            hints = _get_design_hints(self.spec, None, None)
        self.assertEqual(chat.call_count, 2)
        self.assertIn('cyclic node', chat.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(len(valid_proposals(self.spec, hints)[0]), 1)

    def test_python_repairs_proportions_without_changing_ai_grouping(self):
        bad = proposal()
        bad.nodes[2].fraction = .1
        self.assertIsNone(compile_proposal(self.spec, DesignHints(), bad)[0])
        layouts, errors = valid_proposals(self.spec, DesignHints(layout_proposals=[bad]))
        self.assertEqual(errors, [])
        self.assertEqual(len(layouts), 1)
        self.assertEqual(validate_layout(layouts[0], self.spec), [])
        self.assertEqual(layouts[0].planning_source, 'ai_arrangement')
        self.assertTrue(any('adjusted room proportions' in note for note in layouts[0].planning_notes))

    def test_ai_candidates_precede_standard_solver(self):
        options = []
        with patch('agent.layout_agent._get_design_hints', return_value=DesignHints(layout_proposals=[proposal(), proposal(True)])), \
                patch('agent.layout_agent.solve_layout') as standard:
            layout, errors = generate_layout(self.spec, options_out=options)
        self.assertEqual(errors, [])
        self.assertEqual(len(options), 2)
        self.assertEqual(layout.design_name, 'West bedroom')
        standard.assert_not_called()

    def test_invalid_proposals_fall_back_without_weakening_constraints(self):
        bad = proposal(); bad.nodes[0].first = 'root'
        with patch('agent.layout_agent._get_design_hints', return_value=DesignHints(layout_proposals=[bad])), \
                patch('agent.layout_agent.select_design_references', return_value=[]):
            layout, errors = generate_layout(self.spec)
        self.assertIsNotNone(layout)
        self.assertEqual(errors, [])
        self.assertEqual(validate_layout(layout, self.spec), [])
        self.assertEqual(layout.planning_source, 'standard_solver')

    def test_options_persist_and_selection_revalidates_without_llm(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, 'DATA_DIR', Path(folder)), TestClient(app) as client:
            session = SessionState(session_id='options', spec=self.spec)
            with patch('agent.layout_agent._get_design_hints', return_value=DesignHints(layout_proposals=[proposal(), proposal(True)])):
                output, errors = generate_and_save(session, require_3d_success=True)
            self.assertIsNotNone(output)
            self.assertEqual(errors, [])
            saved = spec_store.load('options')
            self.assertEqual(len(saved.layout_options), 2)
            with patch('agent.layout_agent.chat', side_effect=AssertionError('No new AI request')):
                result = client.post('/sessions/options/layout-choice', json={'layout_version': saved.layout.version, 'option_index': 1})
            self.assertEqual(result.status_code, 200)
            chosen = spec_store.load('options')
            self.assertEqual(chosen.layout.design_name, 'East bedroom')
            self.assertGreater(chosen.layout.version, saved.layout.version)
            self.assertEqual(validate_layout(chosen.layout, self.spec), [])
            stale = client.post('/sessions/options/layout-choice', json={'layout_version': saved.layout.version, 'option_index': 0})
            self.assertEqual(stale.status_code, 409)


if __name__ == '__main__':
    unittest.main()
