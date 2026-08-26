from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.execution_plane import (  # noqa: E402
    EXPECTED_REPRODUCIBLE,
    EXPECTED_RUNTIME_ONLY,
    evidence_plane,
    load_policy,
    validate_execution_plane,
)


class ExecutionPlaneTests(unittest.TestCase):
    def _fixture(self, base: Path) -> Path:
        root = base / "repo"
        (root / ".adwf/schemas").mkdir(parents=True)
        (root / ".github/workflows").mkdir(parents=True)
        shutil.copy2(ROOT / ".adwf/execution-plane.json", root / ".adwf/execution-plane.json")
        shutil.copy2(ROOT / ".adwf/schemas/execution-plane.schema.json", root / ".adwf/schemas/execution-plane.schema.json")
        (root / ".github/workflows/adwf-pr.yml").write_text(
            "name: ADWF PR\njobs:\n  test:\n    runs-on: ubuntu-24.04\n", encoding="utf-8"
        )
        (root / ".github/workflows/adwf-platform-smoke.yml").write_text(
            "name: ADWF Platform Smoke\njobs:\n  smoke:\n    strategy:\n      matrix:\n        os: [ubuntu-24.04, windows-2022]\n    runs-on: ${{ matrix.os }}\n",
            encoding="utf-8",
        )
        return root

    def test_canonical_repository_passes(self):
        self.assertEqual(validate_execution_plane(ROOT), [])

    def test_evidence_classes_are_disjoint_and_routed(self):
        self.assertFalse(EXPECTED_REPRODUCIBLE & EXPECTED_RUNTIME_ONLY)
        self.assertEqual(evidence_plane(ROOT, "UNIT_TEST")["execution_plane"], "GITHUB_HOSTED")
        private = evidence_plane(ROOT, "PRIVATE_NETWORK")
        self.assertEqual(private["execution_plane"], "PRIVATE_RUNTIME_NODE")
        self.assertEqual(private["ci_authority"], "NONE")
        self.assertEqual(evidence_plane(ROOT, "UNKNOWN_CLASS")["execution_plane"], "UNKNOWN")

    def test_self_hosted_runner_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(Path(tmp))
            (root / ".github/workflows/adwf-pr.yml").write_text(
                "name: ADWF PR\njobs:\n  test:\n    runs-on: [self-hosted, windows]\n", encoding="utf-8"
            )
            errors = validate_execution_plane(root)
            self.assertTrue(any("SELF_HOSTED_CI_FORBIDDEN" in item for item in errors))

    def test_unknown_runner_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(Path(tmp))
            (root / ".github/workflows/adwf-pr.yml").write_text(
                "name: ADWF PR\njobs:\n  test:\n    runs-on: owner-pc\n", encoding="utf-8"
            )
            errors = validate_execution_plane(root)
            self.assertTrue(any("NON_GITHUB_HOSTED_RUNNER:owner-pc" in item for item in errors))

    def test_matrix_must_be_exact_hosted_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(Path(tmp))
            (root / ".github/workflows/adwf-platform-smoke.yml").write_text(
                "name: smoke\njobs:\n  smoke:\n    strategy:\n      matrix:\n        os: [ubuntu-24.04, owner-pc]\n    runs-on: ${{ matrix.os }}\n",
                encoding="utf-8",
            )
            errors = validate_execution_plane(root)
            self.assertTrue(any("HOSTED_MATRIX_NOT_EXACT" in item for item in errors))

    def test_private_node_cannot_gain_ci_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(Path(tmp))
            policy_path = root / ".adwf/execution-plane.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["private_execution_node"]["ci_authority"] = "GITHUB_ACTIONS"
            policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EXECUTION_PLANE_SCHEMA_MISMATCH"):
                load_policy(root)

    def test_missing_canonical_workflows_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(Path(tmp))
            shutil.rmtree(root / ".github/workflows")
            (root / ".github/workflows").mkdir()
            self.assertIn("CANONICAL_ADWF_WORKFLOWS_REQUIRED", validate_execution_plane(root))


if __name__ == "__main__":
    unittest.main()
