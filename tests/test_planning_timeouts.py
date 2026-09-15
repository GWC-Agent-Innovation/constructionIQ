"""Offline regression checks; no cloud requests or saved-session changes."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import ollama

from agent import layout_agent, llm_client
from agent.demo_plan import example
from agent.layout_solver import solve_layout
from agent.schemas import DesignHints, HouseSpec
from agent.validator import normalized_room_name, validate_layout


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"OLLAMA_API_KEY": "test-only", "OLLAMA_MODEL": "gpt-oss:120b-cloud"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def transport(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://ollama.com")
        return patch.object(llm_client.httpx, "AsyncClient", return_value=client)

    def test_stream_assembles_tool_calls_and_uses_runtime_model(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "gpt-oss:120b")
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["think"], "low")
            chunks = [
                {"message": {"content": "A", "thinking": "x"}},
                {"message": {"content": "B", "tool_calls": [{"function": {"name": "propose_rooms", "arguments": {"rooms": []}}}]}},
                {"done": True, "message": {}},
            ]
            return httpx.Response(200, text="\n".join(map(json.dumps, chunks)))
        with self.transport(handler):
            result = llm_client.chat([])
        self.assertEqual(result["message"]["content"], "AB")
        self.assertEqual(len(result["message"]["tool_calls"]), 1)

    def test_read_timeout_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("test")
        with self.transport(handler), self.assertRaisesRegex(TimeoutError, "read timeout"):
            llm_client.chat([])
        self.assertEqual(len(calls), 1)

    def test_auth_error_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(401, text="private upstream body")
        with self.transport(handler), self.assertRaisesRegex(ollama.ResponseError, "HTTP 401"):
            llm_client.chat([])
        self.assertEqual(len(calls), 1)

    def test_server_error_has_only_one_retry(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503)
        with self.transport(handler), patch.object(llm_client.asyncio, "sleep", new=AsyncMock()), self.assertRaises(ollama.ResponseError):
            llm_client.chat([])
        self.assertEqual(len(calls), 2)

    def test_incomplete_stream_is_rejected(self):
        with self.transport(lambda req: httpx.Response(200, text='{"message":{"content":"partial"}}\n')):
            with self.assertRaisesRegex(RuntimeError, "before it completed"):
                llm_client.chat([])

    def test_stream_error_retries_and_discards_partial_output(self):
        calls = []
        def handler(request):
            calls.append(request)
            chunks = ([{"message": {"content": "discard me", "tool_calls": [{"function": {"name": "stale"}}]}},
                       {"error": "upstream connection closed"}] if len(calls) == 1 else
                      [{"message": {"content": "complete"}, "done": True}])
            return httpx.Response(200, text="\n".join(map(json.dumps, chunks)))
        with self.transport(handler), patch.object(llm_client.asyncio, "sleep", new=AsyncMock()):
            result = llm_client.chat([])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["message"]["content"], "complete")
        self.assertEqual(result["message"]["tool_calls"], [])

    def test_repeated_stream_failure_preserves_reason_and_redacts_key(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"error": "upstream disconnected test-only"})
        with self.transport(handler), patch.object(llm_client.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(llm_client.CloudStreamError) as error:
                llm_client.chat([])
        self.assertEqual(len(calls), 2)
        self.assertIn("upstream disconnected", str(error.exception))
        self.assertNotIn("test-only", str(error.exception))

    def test_stream_account_error_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"error": "usage limit exceeded"})
        with self.transport(handler), self.assertRaisesRegex(llm_client.CloudStreamError, "usage limit"):
            llm_client.chat([])
        self.assertEqual(len(calls), 1)

    def test_stream_retry_does_not_reset_deadline(self):
        calls = []
        async def handler(request):
            calls.append(request)
            if len(calls) == 1:
                await asyncio.sleep(0.02)
                return httpx.Response(200, json={"error": "upstream disconnected"})
            await asyncio.sleep(10)
        # The budget expires during backoff, before a second request is opened.
        with self.transport(handler), self.assertRaisesRegex(TimeoutError, "request budget"):
            llm_client.chat([], timeout_seconds=0.05)
        self.assertEqual(len(calls), 1)

    def test_http_error_displays_only_redacted_error_field(self):
        with self.transport(lambda request: httpx.Response(400, json={
            "error": "invalid request test-only\nBearer secret-token", "prompt": "private prompt",
        })), self.assertRaises(ollama.ResponseError) as error:
            llm_client.chat([])
        detail = str(error.exception)
        self.assertIn("invalid request", detail)
        for private in ("test-only", "secret-token", "private prompt", "\n"):
            self.assertNotIn(private, detail)

    def test_deadline_cancels_active_stream_and_closes_it(self):
        class SlowStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                while True:
                    yield b'{"message":{"content":"."}}\n'
                    await asyncio.sleep(0.01)
            async def aclose(self):
                self.closed = True
        stream = SlowStream()
        with self.transport(lambda req: httpx.Response(200, stream=stream)):
            with self.assertRaisesRegex(TimeoutError, "request budget"):
                llm_client.chat([], timeout_seconds=0.04)
        self.assertTrue(stream.closed)

    def test_layout_survives_hint_service_failure(self):
        """A hint-call hiccup is no longer fatal: the solver still runs with
        neutral defaults, unlike the old design where any LLM failure during
        geometry generation aborted the whole request."""
        spec, _ = example()
        with patch.object(layout_agent, "chat", side_effect=TimeoutError("test")) as chat:
            layout, issues = layout_agent.generate_layout(spec)
        self.assertIsNotNone(layout)
        self.assertEqual(issues, [])
        self.assertEqual(chat.call_count, 1)

    def test_layout_retries_once_on_malformed_hints_then_uses_returned_hints(self):
        spec, _ = example()
        valid_hints = {"message": {"tool_calls": [{"function": {
            "name": "plan_design_hints",
            "arguments": {"room_weights": {"living room": 1.3},
                          "adjacency_preferences": [{"a": "kitchen", "b": "dining room"}],
                          "planning_notes": ["Kept kitchen next to dining.", "Living room enlarged.", "Entrance faces south."]},
        }}]}}
        with patch.object(layout_agent, "chat", side_effect=[{"message": {"content": "not a tool call"}}, valid_hints]) as chat:
            layout, issues = layout_agent.generate_layout(spec)
        self.assertIsNotNone(layout)
        self.assertEqual(issues, [])
        self.assertEqual(chat.call_count, 2)
        self.assertIn("Living room enlarged.", layout.planning_notes)

    def test_layout_falls_back_to_default_hints_after_repeated_bad_responses(self):
        spec, _ = example()
        with patch.object(layout_agent, "chat", return_value={"message": {"content": "sure, let me think about it"}}) as chat:
            layout, issues = layout_agent.generate_layout(spec)
        self.assertIsNotNone(layout)
        self.assertEqual(issues, [])
        self.assertEqual(chat.call_count, layout_agent.MAX_HINT_ROUNDS)

    def test_infeasible_request_reports_a_clear_reason_instead_of_a_bad_layout(self):
        spec = HouseSpec(plot_width_ft=15, plot_length_ft=15, num_bedrooms=6, num_bathrooms=6, num_floors=1)
        with patch.object(layout_agent, "chat", return_value={"message": {}}):
            layout, issues = layout_agent.generate_layout(spec)
        self.assertIsNone(layout)
        self.assertTrue(issues)

    def test_dining_hall_does_not_create_duplicate_dining_room(self):
        spec = HouseSpec(plot_width_ft=30, plot_length_ft=30, num_bedrooms=2,
                         num_bathrooms=3, num_floors=1, entrance_facing="south",
                         must_have_rooms=["kitchen", "dining hall"])
        layout = solve_layout(spec)
        self.assertIsNotNone(layout)
        self.assertEqual(validate_layout(layout, spec), [])
        dining = [room for room in layout.rooms if "dining" in normalized_room_name(room.name)]
        self.assertEqual(len(dining), 1)

    def test_solver_handles_a_range_of_bedroom_bathroom_and_must_have_combinations(self):
        cases = [
            HouseSpec(plot_width_ft=30, plot_length_ft=40, num_bedrooms=2, num_bathrooms=1, num_floors=1),
            HouseSpec(plot_width_ft=40, plot_length_ft=30, num_bedrooms=3, num_bathrooms=2, num_floors=1,
                      entrance_facing="north", must_have_rooms=["Home Office"]),
            # The exact brief that repeatedly failed the old LLM-geometry path.
            HouseSpec(plot_width_ft=50, plot_length_ft=50, num_bedrooms=3, num_bathrooms=3, num_floors=1,
                      entrance_facing="south",
                      must_have_rooms=["Pooja Room", "Car Parking"]),
            HouseSpec(plot_width_ft=60, plot_length_ft=45, num_bedrooms=4, num_bathrooms=4, num_floors=1,
                      must_have_rooms=["Store Room", "Utility", "Car Parking"]),
            # A "small bike parking" must size much smaller than a car - a tight
            # 40x40 plot has no room to spare for a full car-sized reservation.
            HouseSpec(plot_width_ft=40, plot_length_ft=40, num_bedrooms=2, num_bathrooms=3, num_floors=1,
                      entrance_facing="south",
                      must_have_rooms=["Living Room", "Kitchen", "Dining Hall", "Bike Parking"]),
            # A user describing HOW their 2 bathrooms should split (one
            # attached, one common) instead of naming a distinct extra room -
            # these must not be double-counted against num_bathrooms=2, and
            # a single attached bathroom must not span the full bed_depth.
            HouseSpec(plot_width_ft=40, plot_length_ft=40, num_bedrooms=2, num_bathrooms=2, num_floors=1,
                      entrance_facing="north",
                      must_have_rooms=["Living Room", "Kitchen", "Bike Parking", "Attached Bathroom",
                                       "Common Bathroom", "Dining Hall", "Spacious Hall"]),
        ]
        for spec in cases:
            with self.subTest(spec=spec.model_dump()):
                layout = solve_layout(spec)
                self.assertIsNotNone(layout, f"solver returned no layout for {spec.model_dump()}")
                self.assertEqual(validate_layout(layout, spec), [])

    def test_adjacency_hint_is_best_effort_and_never_breaks_validity(self):
        spec = HouseSpec(plot_width_ft=35, plot_length_ft=35, num_bedrooms=3, num_bathrooms=2, num_floors=1)
        hints = DesignHints(room_weights={"living room": 1.4},
                            adjacency_preferences=[{"a": "kitchen", "b": "dining room"}])
        layout = solve_layout(spec, hints)
        self.assertIsNotNone(layout)
        self.assertEqual(validate_layout(layout, spec), [])

    def test_attached_bathroom_hint_is_honored_when_the_plot_allows_it(self):
        spec = HouseSpec(plot_width_ft=40, plot_length_ft=40, num_bedrooms=2, num_bathrooms=2, num_floors=1)
        layout = solve_layout(spec, DesignHints(attached_bathrooms=1))
        self.assertIsNotNone(layout)
        self.assertEqual(validate_layout(layout, spec), [])
        self.assertTrue(any("attached" in room.name.lower() or "att." in room.name.lower() or room.ensuite_for for room in layout.rooms),
                        "expected an attached bathroom when explicitly requested and the plot allows it")

    def test_attached_bathroom_hint_falls_back_gracefully_when_infeasible(self):
        # attached_bathrooms=1 asks for fewer attached baths than this plot's
        # own default split (3 of 4) already uses - a hint that can't itself
        # be satisfied must not break generation, just fall back to the
        # default split rather than forcing the (infeasible) requested count.
        spec = HouseSpec(plot_width_ft=60, plot_length_ft=45, num_bedrooms=4, num_bathrooms=4, num_floors=1,
                         must_have_rooms=["Store Room", "Utility", "Car Parking"])
        layout = solve_layout(spec, DesignHints(attached_bathrooms=1))
        self.assertIsNotNone(layout)
        self.assertEqual(validate_layout(layout, spec), [])

    def test_multiple_parking_spaces_honored_with_attached_bathrooms(self):
        spec = HouseSpec(plot_width_ft=50, plot_length_ft=50, num_bedrooms=3, num_bathrooms=4, num_floors=1,
                         must_have_rooms=["bike parking", "car parking"],
                         attached_bathroom_bedrooms=[1, 2, 3])
        layout = solve_layout(spec)
        self.assertIsNotNone(layout, "expected solver to find a layout with multiple parking spaces and attached bathrooms")
        self.assertEqual(validate_layout(layout, spec), [])
        room_names = [r.name.lower() for r in layout.rooms]
        self.assertTrue(any("bike" in n for n in room_names))
        self.assertTrue(any("car" in n for n in room_names))


if __name__ == "__main__":
    unittest.main()
