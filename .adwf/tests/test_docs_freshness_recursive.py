import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.docs_freshness import check_docs, files_for_pattern, source_digest, updated_registry


class RecursiveDocsFreshnessTests(unittest.TestCase):
    def _repository(self, temporary: str) -> Path:
        root = Path(temporary)
        (root / ".adwf/schemas").mkdir(parents=True)
        (root / "src/deep").mkdir(parents=True)
        (root / "guide.md").write_text("owner guide\n", encoding="utf-8")
        (root / "src/deep/module.py").write_text("VALUE = 1\n", encoding="utf-8")
        shutil.copyfile(
            ROOT / ".adwf/schemas/docs-registry.schema.json",
            root / ".adwf/schemas/docs-registry.schema.json",
        )
        registry = {
            "version": "test",
            "documents": [{
                "path": "guide.md",
                "watched": ["src/**"],
                "mode": "manual",
                "source_digest": source_digest(root, ["src/**"]),
                "reviewed_at": "2026-08-13T00:00:00Z",
                "valid_until": "2026-08-14T00:00:00Z",
            }],
        }
        (root / ".adwf/docs-registry.json").write_text(json.dumps(registry), encoding="utf-8")
        return root

    def test_terminal_double_star_reaches_nested_file_and_detects_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._repository(temporary)
            now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
            self.assertEqual([path.relative_to(root).as_posix() for path in files_for_pattern(root, "src/**")], ["src/deep/module.py"])
            self.assertEqual(check_docs(root, now=now), [])
            (root / "src/deep/module.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.assertIn("DOCUMENT_STALE:guide.md", check_docs(root, now=now))

    def test_runtime_files_are_not_document_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._repository(temporary)
            before = source_digest(root, ["**"])
            (root / ".adwf-runtime/workspaces").mkdir(parents=True)
            (root / ".adwf-runtime/workspaces/transient.json").write_text("{}", encoding="utf-8")
            self.assertEqual(source_digest(root, ["**"]), before)

    def test_traversal_pattern_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                files_for_pattern(temporary, "../**")

    def test_empty_recursive_watch_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._repository(temporary)
            registry_path = root / ".adwf/docs-registry.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["documents"][0]["watched"] = ["missing/**"]
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            errors = check_docs(root, now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc))
            self.assertIn("DOCUMENT_WATCH_EMPTY:guide.md:missing/**", errors)

    def test_registry_update_rejects_inverted_review_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._repository(temporary)
            with self.assertRaises(ValueError):
                updated_registry(root, "2026-08-14T00:00:00Z", "2026-08-13T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
