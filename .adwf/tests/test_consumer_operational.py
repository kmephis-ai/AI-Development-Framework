from __future__ import annotations
from pathlib import Path
import copy, json, shutil, tempfile, unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.consumer_operational import (
    BINDING_REL, ConsumerOperationalError, build_binding, load_binding,
    resolve_operational_context, seal_binding, write_binding,
)
from lib.roadmap_view import build_roadmap_view
from tests.test_consumer_installation import ConsumerInstallationTests


class ConsumerOperationalTests(unittest.TestCase):
    def _installed(self, base: Path):
        helper = ConsumerInstallationTests()
        source, consumer, record = helper._installed(base)
        schema = ROOT / ".adwf/schemas/consumer-operational-binding.schema.json"
        for target_root in (source, consumer):
            target = target_root / ".adwf/schemas/consumer-operational-binding.schema.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(schema, target)
        roadmap = consumer / "docs/ROADMAP.md"
        roadmap.parent.mkdir(parents=True, exist_ok=True)
        roadmap.write_text("# Consumer Roadmap\n\nNative product truth.\n", encoding="utf-8")
        return source, consumer, record

    def test_fresh_session_resolves_native_sources_without_framework_roadmap(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, consumer, _ = self._installed(Path(tmp))
            binding = build_binding(
                consumer, consumer,
                consumer_repository="example/consumer",
                roadmap_path="docs/ROADMAP.md",
            )
            write_binding(binding, consumer, consumer)
            shutil.rmtree(consumer / ".adwf-runtime")
            context = resolve_operational_context(consumer, consumer)
            self.assertEqual(context["mode"], "CONSUMER_NATIVE")
            self.assertEqual(context["roadmap"], {"kind": "MARKDOWN_FILE", "path": "docs/ROADMAP.md"})
            self.assertEqual(context["work_items"]["repository"], "example/consumer")
            view = build_roadmap_view(consumer, {"work_items": [{"roadmap_id": "ADWF-SELF", "state": "DONE"}]})
            self.assertEqual(view["operating_mode"], "CONSUMER_NATIVE")
            self.assertEqual(view["goals"], [])
            self.assertEqual(view["summary"]["status"], "NATIVE_SOURCE_BOUND_NOT_MATERIALIZED")
            self.assertNotIn("ADWF-SELF", json.dumps(view))

    def test_installed_consumer_requires_binding_and_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, consumer, _ = self._installed(Path(tmp))
            with self.assertRaisesRegex(ConsumerOperationalError, "BINDING_REQUIRED"):
                resolve_operational_context(consumer, consumer)
            binding = build_binding(consumer, consumer, consumer_repository="example/consumer", roadmap_path="docs/ROADMAP.md")
            write_binding(binding, consumer, consumer)
            path = consumer / BINDING_REL
            value = json.loads(path.read_text(encoding="utf-8")); value["roadmap"]["path"] = "docs/OTHER.md"
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ConsumerOperationalError, "DIGEST_MISMATCH"):
                load_binding(consumer, consumer)

    def test_framework_and_internal_paths_cannot_be_native_roadmap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, consumer, _ = self._installed(Path(tmp))
            for path in (".adwf/roadmap.json", ".adwf/project-state.json", ".adwf-consumer/profile.json", "../ROADMAP.md"):
                with self.subTest(path=path):
                    with self.assertRaises(ConsumerOperationalError):
                        build_binding(consumer, consumer, consumer_repository="example/consumer", roadmap_path=path)

    def test_repository_profile_installation_and_authority_substitution_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, consumer, _ = self._installed(Path(tmp))
            binding = build_binding(consumer, consumer, consumer_repository="example/consumer", roadmap_path="docs/ROADMAP.md")
            forged = copy.deepcopy(binding); forged["consumer_repository"] = "other/repo"; forged["work_items"]["repository"] = "other/repo"; forged = seal_binding(forged)
            (consumer / BINDING_REL).write_text(json.dumps(forged, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ConsumerOperationalError, "REPOSITORY_MISMATCH"):
                load_binding(consumer, consumer)
            forged = copy.deepcopy(binding); forged["mutation_authority"] = "WRITE"; forged = seal_binding(forged)
            (consumer / BINDING_REL).write_text(json.dumps(forged, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(ConsumerOperationalError):
                load_binding(consumer, consumer)

    def test_missing_or_symlink_native_source_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, consumer, _ = self._installed(Path(tmp))
            (consumer / "docs/ROADMAP.md").unlink()
            with self.assertRaisesRegex(ConsumerOperationalError, "SOURCE_MISSING"):
                build_binding(consumer, consumer, consumer_repository="example/consumer", roadmap_path="docs/ROADMAP.md")
            target = consumer / "real-roadmap.md"; target.write_text("# R\n", encoding="utf-8")
            try:
                (consumer / "docs/ROADMAP.md").symlink_to(target)
            except OSError:
                self.skipTest("symlink unavailable")
            with self.assertRaisesRegex(ConsumerOperationalError, "SYMLINK_FORBIDDEN"):
                build_binding(consumer, consumer, consumer_repository="example/consumer", roadmap_path="docs/ROADMAP.md")

    def test_self_host_remains_canonical_without_consumer_records(self):
        context = resolve_operational_context(ROOT, ROOT)
        self.assertEqual(context["mode"], "SELF_HOST_CANONICAL")
        self.assertEqual(context["roadmap"]["path"], ".adwf/roadmap.json")


if __name__ == "__main__":
    unittest.main()
