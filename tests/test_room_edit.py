import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import spec_store
from agent.ai_arrangements import compile_proposal
from agent.schemas import DesignHints, HouseSpec, LayoutProposal, SessionState
from agent.room_edit import resize_room, swap_rooms
from agent.validator import validate_layout
from backend.api import app


def _fixture_layout():
    # 30x30 plot: bedroom_0 (0,0,15x10), bath_0 (15,0,5x10), kitchen (20,0,10x10),
    # living_0 (0,10,15x20), dining_1 (15,10,15x20). No hallway.
    spec = HouseSpec(plot_width_ft=30, plot_length_ft=30, num_bedrooms=1,
                     num_bathrooms=1, attached_bathroom_bedrooms=[])
    nodes = [
        dict(id='root', axis='y', fraction=1 / 3, first='north', second='south'),
        dict(id='north', axis='x', fraction=.5, first='bed', second='service'),
        dict(id='service', axis='x', fraction=1 / 3, first='bath', second='kitchen'),
        dict(id='south', axis='x', fraction=.5, first='living', second='dining'),
        dict(id='bed', room_key='bedroom_0'), dict(id='bath', room_key='bath_0'),
        dict(id='kitchen', room_key='kitchen'), dict(id='living', room_key='living_0'),
        dict(id='dining', room_key='dining_1'),
    ]
    proposal = LayoutProposal(name='Fixture', root='root', nodes=nodes)
    layout, issues = compile_proposal(spec, DesignHints(), proposal)
    assert layout is not None, issues
    return spec, layout


class ResizeRoomTests(unittest.TestCase):
    def test_shrinking_one_room_grows_its_neighbor_and_stays_valid(self):
        spec, layout = _fixture_layout()
        result, issues = resize_room(layout, spec, 'bedroom_0', 'south', -1)
        self.assertEqual(issues, [])
        self.assertIsNotNone(result)
        bedroom = next(r for r in result.rooms if r.id == 'bedroom_0')
        living = next(r for r in result.rooms if r.id == 'living_0')
        self.assertAlmostEqual(bedroom.width * bedroom.length, 15 * 9)
        self.assertAlmostEqual(living.width * living.length, 15 * 21)
        self.assertEqual(validate_layout(result, spec), [])
        # Only the two rooms sharing that wall changed; everything else is untouched.
        untouched = {r.id: r for r in layout.rooms if r.id not in ('bedroom_0', 'living_0')}
        for room in result.rooms:
            if room.id in untouched:
                self.assertEqual(room.x, untouched[room.id].x)
                self.assertEqual(room.y, untouched[room.id].y)
                self.assertEqual(room.width, untouched[room.id].width)
                self.assertEqual(room.length, untouched[room.id].length)

    def test_resize_below_catalog_minimum_is_rejected(self):
        spec, layout = _fixture_layout()
        result, issues = resize_room(layout, spec, 'bedroom_0', 'south', -5)
        self.assertIsNone(result)
        self.assertTrue(issues)
        self.assertIn('minimum', issues[0])

    def test_resize_on_exterior_wall_with_no_neighbor_is_rejected(self):
        spec, layout = _fixture_layout()
        result, issues = resize_room(layout, spec, 'bedroom_0', 'north', 1)
        self.assertIsNone(result)
        self.assertTrue(issues)

    def test_unknown_room_is_rejected(self):
        spec, layout = _fixture_layout()
        result, issues = resize_room(layout, spec, 'not_a_room', 'south', -1)
        self.assertIsNone(result)
        self.assertTrue(issues)


class SwapRoomsTests(unittest.TestCase):
    def test_swap_keeps_identity_and_validates(self):
        spec, layout = _fixture_layout()
        result, issues = swap_rooms(layout, spec, 'living_0', 'dining_1')
        self.assertEqual(issues, [])
        self.assertIsNotNone(result)
        living = next(r for r in result.rooms if r.id == 'living_0')
        dining = next(r for r in result.rooms if r.id == 'dining_1')
        self.assertEqual((living.x, living.width, living.length), (15, 15, 20))
        self.assertEqual((dining.x, dining.width, dining.length), (0, 15, 20))
        self.assertEqual(validate_layout(result, spec), [])

    def test_swap_below_catalog_minimum_is_rejected(self):
        spec, layout = _fixture_layout()
        result, issues = swap_rooms(layout, spec, 'kitchen', 'bath_0')
        self.assertIsNone(result)
        self.assertTrue(issues)
        self.assertIn('sq ft', issues[0])

    def test_swap_same_room_is_rejected(self):
        spec, layout = _fixture_layout()
        result, issues = swap_rooms(layout, spec, 'kitchen', 'kitchen')
        self.assertIsNone(result)
        self.assertTrue(issues)


class RoomEditApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        directory = patch.object(spec_store, 'DATA_DIR', Path(self.folder.name))
        directory.start()
        self.addCleanup(directory.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.spec, self.layout = _fixture_layout()
        self.session = SessionState(session_id='edit-test', spec=self.spec, layout=self.layout,
                                    layout_history=[self.layout], model_3d_version=self.layout.version)
        spec_store.save(self.session)

    def edit(self, **body):
        return self.client.post('/sessions/edit-test/room-edit',
                                json={'layout_version': self.layout.version, **body})

    def test_resize_persists_new_revision(self):
        response = self.edit(action='resize', room_id='bedroom_0', edge='south', delta_ft=-1)
        self.assertEqual(response.status_code, 200, response.text)
        saved = spec_store.load('edit-test')
        self.assertEqual(saved.layout.version, self.layout.version + 1)
        bedroom = next(r for r in saved.layout.rooms if r.id == 'bedroom_0')
        self.assertAlmostEqual(bedroom.width * bedroom.length, 15 * 9)

    def test_swap_persists_new_revision(self):
        response = self.edit(action='swap', room_id='living_0', other_room_id='dining_1')
        self.assertEqual(response.status_code, 200, response.text)
        saved = spec_store.load('edit-test')
        living = next(r for r in saved.layout.rooms if r.id == 'living_0')
        self.assertEqual(living.x, 15)

    def test_invalid_and_stale_edits_do_not_change_project(self):
        cases = [
            (dict(action='resize', room_id='bedroom_0', edge='north', delta_ft=1), 422),
            (dict(action='resize', room_id='bedroom_0', edge='south'), 422),
            (dict(action='swap', room_id='kitchen', other_room_id='bath_0'), 422),
            (dict(action='swap', room_id='kitchen'), 422),
            (dict(action='resize', room_id='unknown', edge='south', delta_ft=-1), 404),
            (dict(action='swap', room_id='kitchen', other_room_id='unknown'), 404),
            (dict(action='resize', room_id='bedroom_0', edge='south', delta_ft=-1, layout_version=99), 409),
        ]
        for body, code in cases:
            with self.subTest(body=body):
                response = self.client.post('/sessions/edit-test/room-edit',
                                            json={'layout_version': self.layout.version, **body})
                self.assertEqual(response.status_code, code, response.text)
        self.assertEqual(spec_store.load('edit-test').layout.version, self.layout.version)


if __name__ == '__main__':
    unittest.main()
