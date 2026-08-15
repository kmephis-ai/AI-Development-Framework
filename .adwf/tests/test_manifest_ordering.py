import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / ".adwf/scripts/generate_manifest.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("adwf_generate_manifest", GEN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ManifestIntegrityOrderingTests(unittest.TestCase):
    def test_source_files_are_sorted_by_posix_repository_path(self):
        module = _load_generator()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".adwf").mkdir()
            (root / ".adwf/Zeta.txt").write_text("Z\n", encoding="utf-8")
            (root / ".adwf/alpha.txt").write_text("a\n", encoding="utf-8")
            observed = [p.relative_to(root).as_posix() for p in module.source_files(root)]
            self.assertEqual(observed, [".adwf/Zeta.txt", ".adwf/alpha.txt"])

    def test_expected_sums_uses_same_posix_order(self):
        module = _load_generator()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".adwf").mkdir()
            (root / "VERSION").write_text("1.6.0\n", encoding="utf-8")
            (root / ".adwf/Zeta.txt").write_text("Z\n", encoding="utf-8")
            (root / ".adwf/alpha.txt").write_text("a\n", encoding="utf-8")
            manifest_text = __import__("json").dumps(module.expected_manifest(root), ensure_ascii=False, indent=2) + "\n"
            (root / "MANIFEST.json").write_text(manifest_text, encoding="utf-8", newline="\n")
            paths = [line.split("  ", 1)[1] for line in module.expected_sums(root).splitlines()]
            self.assertEqual(paths, [".adwf/Zeta.txt", ".adwf/alpha.txt", "MANIFEST.json", "VERSION"])


if __name__ == "__main__":
    unittest.main()