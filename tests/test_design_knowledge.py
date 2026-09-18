import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.ai_arrangements import compile_proposal
from agent.design_knowledge import (
    DesignReference, KNOWLEDGE_DIR, MAX_CONTEXT_CHARS, build_design_context,
)
from agent.layout_agent import _get_design_hints, generate_layout
from agent.schemas import DesignHints, HouseSpec, RequiredAdjacency
from agent.validator import validate_layout


class DesignKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.spec = HouseSpec(plot_width_ft=26, plot_length_ft=30,
                              num_bedrooms=2, num_bathrooms=1,
                              entrance_facing="west", attached_bathroom_bedrooms=[])

    def test_bundled_references_compile_without_sizing_repair(self):
        root = KNOWLEDGE_DIR / "references"
        for filename in json.loads((root / "index.json").read_text()):
            with self.subTest(filename=filename):
                reference = DesignReference.model_validate_json((root / filename).read_text())
                layout, issues = compile_proposal(reference.spec, DesignHints(), reference.proposal)
                self.assertIsNotNone(layout, issues)
                self.assertEqual(issues, [])
                self.assertIn(reference.review_status, ("synthetic_unreviewed", "reviewed"))

    def test_matches_brief_without_mutating_requirements(self):
        before = self.spec.model_dump_json()
        context = build_design_context(self.spec)
        self.assertIn("two_bed_public_middle", context)
        self.assertNotIn("one_bed_public_south", context)
        self.assertIn("Compact circulation", context)
        self.assertLessEqual(len(context), MAX_CONTEXT_CHARS)
        self.assertEqual(self.spec.model_dump_json(), before)

    def test_incompatible_references_are_not_used(self):
        for changes in ({"attached_bathroom_bedrooms": [1]},
                        {"num_bedrooms": 3}, {"include_dining_room": False},
                        {"must_have_rooms": ["Parking"]},
                        {"plot_width_ft": 80, "plot_length_ft": 80}):
            with self.subTest(changes=changes):
                context = build_design_context(self.spec.model_copy(update=changes))
                self.assertNotIn("Reference example", context)
                self.assertIn("Compact circulation", context)

    def test_missing_corrupt_and_oversized_files_are_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(build_design_context(self.spec, root), "")
            (root / "instructions").mkdir()
            (root / "references").mkdir()
            (root / "instructions/circulation.md").write_text("Keep routes compact.")
            (root / "instructions/space_allocation.md").write_bytes(b"x" * 32001)
            index = root / "references/index.json"
            index.write_text("{broken")
            self.assertIn("Keep routes compact", build_design_context(self.spec, root))
            index.write_text(json.dumps(["broken.json", "../outside.json", "C:\\outside.json", 7]))
            (root / "references/broken.json").write_text('{"id": false}')
            self.assertNotIn("Reference example", build_design_context(self.spec, root))

    def test_planner_receives_knowledge_in_existing_call(self):
        response = {"message": {"tool_calls": [{"function": {
            "name": "plan_design_hints", "arguments": {
                "planning_notes": [], "layout_proposals": []}}}]}}
        with patch("agent.layout_agent.chat", return_value=response) as chat:
            hints = _get_design_hints(self.spec, "Keep the bedrooms balanced", None)
        self.assertEqual(chat.call_count, 1)
        content = chat.call_args.kwargs["messages"][1]["content"]
        self.assertIn("two_bed_public_middle", content)
        self.assertIn("Keep the bedrooms balanced", content)
        self.assertIn("Required room catalog", content)
        self.assertEqual(hints.layout_proposals, [])

    def test_missing_knowledge_keeps_existing_planner_available(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("agent.design_knowledge.KNOWLEDGE_DIR", Path(folder)), \
                patch("agent.layout_agent.chat", side_effect=RuntimeError("offline")):
            self.assertEqual(_get_design_hints(self.spec, None, None), DesignHints())

    def test_reported_brief_recovers_when_ai_and_standard_solver_fail(self):
        self.spec.required_adjacencies = [RequiredAdjacency(a="Kitchen", b="Dining Room")]
        before = self.spec.model_dump_json()
        options = []
        # Run the real standard solver and compiler, without any cloud call.
        with patch("agent.layout_agent._get_design_hints", return_value=DesignHints()):
            layout, issues = generate_layout(self.spec, options_out=options)
        self.assertIsNotNone(layout, issues)
        self.assertEqual(issues, [])
        self.assertEqual(validate_layout(layout, self.spec), [])
        self.assertTrue(layout.design_name.startswith("Reference:"))
        self.assertFalse(any(room.id.startswith("hall_") for room in layout.rooms))
        bedrooms = [r for r in layout.rooms if r.id.startswith("bedroom_")]
        self.assertEqual(bedrooms[0].width * bedrooms[0].length,
                         bedrooms[1].width * bedrooms[1].length)
        self.assertEqual(len(options), 1)
        self.assertEqual(self.spec.model_dump_json(), before)

    def test_reference_recovery_cannot_bypass_current_constraints(self):
        for changes in (
            {"max_bathroom_area_sqft": 35},
            {"entrance_facing": "south"},
            {"required_adjacencies": [RequiredAdjacency(a="Bedroom 1", b="Bathroom")]},
            {"attached_bathroom_bedrooms": [1]},
        ):
            with self.subTest(changes=changes), \
                    patch("agent.layout_agent._get_design_hints", return_value=DesignHints()), \
                    patch("agent.layout_agent.solve_layout", return_value=None), \
                    patch("agent.layout_agent._infeasible_message", return_value="No supported arrangement"):
                options = []
                layout, issues = generate_layout(self.spec.model_copy(update=changes), options_out=options)
                self.assertIsNone(layout)
                self.assertTrue(issues)
                self.assertEqual(options, [])

    def test_low_circulation_standard_result_does_not_use_reference_recovery(self):
        reference = DesignReference.model_validate_json(
            (KNOWLEDGE_DIR / 'references/two_bed_public_middle.json').read_text())
        standard, issues = compile_proposal(self.spec, DesignHints(), reference.proposal)
        with patch("agent.layout_agent._get_design_hints", return_value=DesignHints()), \
                patch("agent.layout_agent.solve_layout", return_value=standard), \
                patch("agent.layout_agent.select_design_references") as references:
            layout, issues = generate_layout(self.spec)
        self.assertIsNotNone(layout, issues)
        references.assert_not_called()

    def test_base_rooms_in_must_have_list_do_not_hide_matching_reference(self):
        self.spec.must_have_rooms = ['Living Room', 'Dining Room', 'Kitchen']
        self.assertIn('two_bed_public_middle', build_design_context(self.spec))

    def test_reference_improves_oversized_fallback_circulation(self):
        spec = HouseSpec(plot_width_ft=30, plot_length_ft=30, num_bedrooms=1,
                         num_bathrooms=1, entrance_facing='south', attached_bathroom_bedrooms=[])
        with patch('agent.layout_agent._get_design_hints', return_value=DesignHints()):
            layout, issues = generate_layout(spec)
        self.assertEqual(issues, [])
        self.assertTrue(layout.design_name.startswith('Reference:'))
        self.assertFalse(any('hallway' in r.name.lower() for r in layout.rooms))


if __name__ == "__main__":
    unittest.main()
