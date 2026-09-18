import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.okf_knowledge import MAX_DOCUMENTS, read_concept, select_okf_context
from agent.schemas import HouseSpec


def write(path: Path, frontmatter: str, body: str = "Some guidance.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


def spec(**changes):
    base = HouseSpec(plot_width_ft=30, plot_length_ft=40, num_bedrooms=2, num_bathrooms=1)
    return base.model_copy(update=changes)


class ReadConceptTests(unittest.TestCase):
    def test_valid_document_parses_metadata_and_body(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "doc.md"
            write(path, "type: Design Guideline\ntitle: Example", "The body text.")
            metadata, body = read_concept(path)
            self.assertEqual(metadata["type"], "Design Guideline")
            self.assertEqual(body, "The body text.")

    def test_missing_frontmatter_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "doc.md"
            path.write_text("Just prose, no frontmatter.")
            with self.assertRaises(ValueError):
                read_concept(path)

    def test_missing_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "doc.md"
            write(path, "title: No type here")
            with self.assertRaises(ValueError):
                read_concept(path)

    def test_oversized_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "doc.md"
            write(path, "type: Design Guideline", "x" * 40_000)
            with self.assertRaises(ValueError):
                read_concept(path)


class SelectOkfContextTests(unittest.TestCase):
    def test_filters_by_applies_to_features(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / "always.md", "type: Design Guideline\napplies_to: [always]", "Always shown.")
            write(root / "parking.md", "type: Design Guideline\napplies_to: [parking]", "Parking only.")
            context = select_okf_context(spec(), root)
            self.assertIn("Always shown", context)
            self.assertNotIn("Parking only", context)
            context = select_okf_context(spec(must_have_rooms=["Parking"]), root)
            self.assertIn("Parking only", context)

    def test_deprecated_documents_are_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / "old.md", "type: Design Guideline\napplies_to: [always]\nstatus: deprecated",
                 "Outdated guidance.")
            self.assertNotIn("Outdated guidance", select_okf_context(spec(), root))

    def test_stale_documents_are_skipped_but_future_expiry_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
            write(root / "expired.md", f"type: Design Guideline\napplies_to: [always]\nstale_after: '{past}'",
                 "Expired guidance.")
            write(root / "fresh.md", f"type: Design Guideline\napplies_to: [always]\nstale_after: '{future}'",
                 "Fresh guidance.")
            context = select_okf_context(spec(), root)
            self.assertNotIn("Expired guidance", context)
            self.assertIn("Fresh guidance", context)

    def test_higher_priority_documents_come_first_and_budget_can_exclude_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / "low.md", "type: Design Guideline\napplies_to: [always]\npriority: 1", "Low priority.")
            write(root / "high.md", "type: Design Guideline\napplies_to: [always]\npriority: 99", "High priority.")
            context = select_okf_context(spec(), root)
            self.assertLess(context.index("High priority"), context.index("Low priority"))
            tight = select_okf_context(spec(), root, max_chars=1)
            self.assertEqual(tight, "")

    def test_index_and_log_are_never_included(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / "index.md", "type: Knowledge Bundle\napplies_to: [always]", "Bundle index text.")
            write(root / "log.md", "type: Change Log\napplies_to: [always]", "Bundle log text.")
            context = select_okf_context(spec(), root)
            self.assertEqual(context, "")

    def test_malformed_or_invalid_documents_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "broken.md").write_text("---\nnot: [valid: yaml:\n---\nBroken.")
            write(root / "bad_priority.md", "type: Design Guideline\napplies_to: [always]\npriority: 999",
                 "Bad priority.")
            write(root / "bad_applies.md", "type: Design Guideline\napplies_to: not-a-list", "Bad applies.")
            write(root / "good.md", "type: Design Guideline\napplies_to: [always]", "Good guidance.")
            context = select_okf_context(spec(), root)
            self.assertIn("Good guidance", context)
            self.assertNotIn("Bad priority", context)
            self.assertNotIn("Bad applies", context)

    def test_document_count_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for i in range(MAX_DOCUMENTS + 5):
                write(root / f"doc{i:02d}.md", "type: Design Guideline\napplies_to: [always]", f"Guidance {i}.")
            context = select_okf_context(spec(), root, max_chars=1_000_000)
            self.assertEqual(context.count("OKF concept:"), MAX_DOCUMENTS)

    def test_missing_root_is_empty_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(select_okf_context(spec(), Path(folder) / "missing"), "")

    def test_bundled_okf_documents_all_parse(self):
        from agent.design_knowledge import KNOWLEDGE_DIR
        root = KNOWLEDGE_DIR / "okf"
        self.assertTrue(root.is_dir())
        for path in sorted(root.rglob("*.md")):
            if path.name in ("index.md", "log.md"):
                continue  # select_okf_context excludes these by filename before parsing.
            with self.subTest(path=path.name):
                metadata, body = read_concept(path)
                self.assertTrue(metadata.get("type"))
                self.assertTrue(body)


if __name__ == "__main__":
    unittest.main()
