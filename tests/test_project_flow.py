import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent import spec_store
from agent.schemas import HouseSpec, SessionState
from agent.spec_agent import run_turn, _explicit_bhk
from backend.api import app


def update(content="", **kwargs):
    return {"message": {"content": content, "tool_calls": [{"function": {"name": "update_house_spec", "arguments": kwargs}}]}}


class ProjectFlowTests(unittest.TestCase):
    def test_bhk_is_bedrooms_not_bathrooms_and_never_asked_again(self):
        session = SessionState(session_id="test")
        with patch("agent.spec_agent.chat", return_value={"message": {"content": "Got it, 2 bedrooms. What's your plot size?"}}):
            reply = run_turn(session, "I need a 2 BHK home")
        self.assertEqual(session.spec.num_bedrooms, 2)
        self.assertIsNone(session.spec.num_bathrooms)
        self.assertIn("Kitchen", session.spec.must_have_rooms)
        self.assertIn("plot", reply)

    def test_contextual_answer_and_generation_handoff(self):
        session = SessionState(session_id="test")
        with patch("agent.spec_agent.chat", side_effect=[
            update("Noted, 2 bedrooms. What's your plot's width and length?", num_bedrooms=2),
            update("Thanks. How many bathrooms would you like?", plot_width_ft=30, plot_length_ft=40),
            update("Your brief is ready. Click the Generate 3D home button to create your design.", num_bathrooms=2),
        ]) as chat:
            run_turn(session, "2 BHK")
            reply = run_turn(session, "30 by 40 feet")
            self.assertIn("How many bathrooms", reply)
            reply = run_turn(session, "two")
        self.assertIn("Generate 3D home", reply)
        self.assertEqual(session.spec.num_bathrooms, 2)
        self.assertTrue(any("How many bathrooms" in turn["content"] for turn in chat.call_args.kwargs["messages"]))
        self.assertEqual(chat.call_count, 3)

    def test_history_survives_reload_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, "DATA_DIR", Path(folder)):
            session = SessionState(session_id="test", project_name="Our Home")
            with patch("agent.spec_agent.chat", return_value={"message": {"content": "Hi there!"}}):
                for i in range(8):
                    run_turn(session, f"Hello {i}")
            spec_store.save(session)
            restored = spec_store.load("test")
            self.assertEqual(len(restored.history), 12)
            self.assertEqual(restored.project_name, "Our Home")

    def test_correction_updates_known_bedrooms(self):
        session = SessionState(session_id="test", spec=HouseSpec(plot_width_ft=30, plot_length_ft=40, num_bedrooms=2, num_bathrooms=1))
        with patch("agent.spec_agent.chat", return_value=update("Updated to 3 bedrooms. Your brief is ready — click Generate 3D home.", num_bedrooms=3)):
            reply = run_turn(session, "Change it to a 3 BHK home")
        self.assertEqual(session.spec.num_bedrooms, 3)
        self.assertEqual(session.spec.num_bathrooms, 1)
        self.assertIn("Generate 3D home", reply)

    def test_negated_or_ambiguous_bhk_not_inferred_locally(self):
        for text in ("not 2 BHK", "2 BHK or 3 BHK", "Is 2 BHK enough?"):
            self.assertIsNone(_explicit_bhk(text))

    def test_named_projects_use_safe_unique_ids_and_require_nonblank_name(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, "DATA_DIR", Path(folder)), TestClient(app) as client:
            self.assertEqual(client.post("/sessions", json={}).status_code, 422)
            self.assertEqual(client.post("/sessions", json={"project_name": "   "}).status_code, 422)
            first = client.post("/sessions", json={"project_name": "  Our Dream Home  "})
            self.assertEqual(first.status_code, 201)
            data = first.json()
            self.assertEqual(data["session"]["project_name"], "Our Dream Home")
            self.assertTrue(data["session"]["session_id"].startswith("our-dream-home-"))
            second = client.post("/sessions", json={"project_name": "Our Dream Home"}).json()
            self.assertNotEqual(data["session"]["session_id"], second["session"]["session_id"])
            self.assertFalse(data["ready_for_generation"])

    def test_api_handoff_does_not_automatically_generate(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(spec_store, "DATA_DIR", Path(folder)), TestClient(app) as client:
            session_id = client.post("/sessions", json={"project_name": "Test Home"}).json()["session"]["session_id"]
            with patch("agent.spec_agent.chat", return_value=update(
                    "Your brief is ready. Click the Generate 3D home button to create your design.",
                    num_bedrooms=2, num_bathrooms=1, plot_width_ft=30, plot_length_ft=40)), patch("backend.api.generate_and_save") as generate:
                result = client.post(f"/sessions/{session_id}/messages", json={"message": "2 BHK, 1 bathroom, 30 by 40 feet"})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()["ready_for_generation"])
            self.assertIn("Click the Generate 3D home", result.json()["reply"])
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
