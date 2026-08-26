from __future__ import annotations

from pathlib import Path
import copy
import json
import os
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.external_consumer_binding import (
    BINDING_REL,
    ExternalConsumerBindingError,
    build_binding,
    load_binding,
    seal_binding,
    validate_binding,
)


def run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class ExternalConsumerBindingTests(unittest.TestCase):
    def _consumer(self, base: Path) -> Path:
        consumer = base / "consumer"
        consumer.mkdir()
        run_git(consumer, "init")
        run_git(consumer, "remote", "add", "origin", "https://github.com/example/powershell-consumer.git")
        (consumer / ".adwf-powershell.json").write_text("{}\n", encoding="utf-8")
        return consumer

    def _gates(self) -> dict[str, list[dict[str, object]]]:
        return {
            "pr": [
                {"check_name": "repo-integrity", "app_slug": "github-actions", "app_id": 15368}
            ],
            "main": [
                {"check_name": "truth-contract", "app_slug": "github-actions", "app_id": 15368}
            ],
        }

    def _binding(self, consumer: Path) -> dict[str, object]:
        return build_binding(
            consumer,
            ROOT,
            default_branch="main",
            native_gates=self._gates(),
        )

    def test_exact_contract_is_proof_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            result = validate_binding(binding, consumer, ROOT)
            self.assertEqual(result["status"], "VERIFIED_CONTRACT")
            self.assertEqual(result["runtime_evidence"], "NOT_VERIFIED")
            self.assertEqual(result["native_gate_evidence"], "NOT_VERIFIED")
            self.assertEqual(result["mutation_authority"], "NONE_BINDING_IS_PROOF_ONLY")
            self.assertFalse(result["managed_surface_adoption"])

    def test_load_exact_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            target = consumer / BINDING_REL
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(binding, ensure_ascii=False) + "\n", encoding="utf-8")
            loaded, result = load_binding(consumer, ROOT)
            self.assertEqual(loaded, binding)
            self.assertEqual(result["status"], "VERIFIED_CONTRACT")

    def test_tamper_without_reseal_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            binding["consumer"]["default_branch"] = "other"
            with self.assertRaisesRegex(ExternalConsumerBindingError, "DIGEST_MISMATCH"):
                validate_binding(binding, consumer, ROOT)

    def test_framework_sha_substitution_blocks_even_when_resealed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            binding["framework"]["source_sha"] = "0" * 40
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "SOURCE_SHA_MISMATCH"):
                validate_binding(binding, consumer, ROOT)

    def test_consumer_repository_substitution_blocks_even_when_resealed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            binding["consumer"]["repository"] = "other/repository"
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "CONSUMER_REPOSITORY_MISMATCH"):
                validate_binding(binding, consumer, ROOT)

    def test_pack_digest_substitution_blocks_even_when_resealed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            binding["project_pack"]["digest"] = "0" * 64
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "PACK_DIGEST_MISMATCH"):
                validate_binding(binding, consumer, ROOT)

    def test_managed_surface_collision_blocks_thin_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            (consumer / ".adwf").mkdir()
            with self.assertRaisesRegex(ExternalConsumerBindingError, "MANAGED_SURFACE_FORBIDDEN"):
                build_binding(consumer, ROOT, default_branch="main", native_gates=self._gates())

    def test_reserved_and_duplicate_native_gates_block(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            binding["native_gates"]["pr"][0]["check_name"] = "adwf/trusted-gate"
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "RESERVED_GATE"):
                validate_binding(binding, consumer, ROOT)
            binding = self._binding(consumer)
            binding["native_gates"]["pr"].append(copy.deepcopy(binding["native_gates"]["pr"][0]))
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "DUPLICATE_GATE"):
                validate_binding(binding, consumer, ROOT)

    def test_default_branch_traversal_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            binding["consumer"]["default_branch"] = "feature/../main"
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "DEFAULT_BRANCH_INVALID"):
                validate_binding(binding, consumer, ROOT)

    def test_unknown_field_blocks_schema(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            binding = self._binding(consumer)
            binding["provider_token"] = "forbidden"
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "SCHEMA_MISMATCH"):
                validate_binding(binding, consumer, ROOT)

    def test_marker_symlink_blocks_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            consumer = self._consumer(Path(raw))
            marker = consumer / ".adwf-powershell.json"
            target = consumer / "marker-target.json"
            target.write_text("{}\n", encoding="utf-8")
            marker.unlink()
            try:
                marker.symlink_to(target.name)
            except OSError:
                self.skipTest("symlink creation is unavailable in this environment")
            binding = {
                "$schema": ".adwf/schemas/external-consumer-binding.schema.json",
                "schema_version": 1,
                "role": "EXTERNAL_CONSUMER_BINDING",
                "framework": {
                    "repository": subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip().removeprefix("https://github.com/").removesuffix(".git"),
                    "source_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
                },
                "consumer": {"repository": "example/powershell-consumer", "default_branch": "main"},
                "project_pack": {"id": "powershell", "digest": "0" * 64},
                "native_gates": self._gates(),
                "safety": {"monetary_budget_usd": 0, "secrets": "FORBIDDEN"},
                "mutation_authority": "NONE_BINDING_IS_PROOF_ONLY",
            }
            binding = seal_binding(binding)
            with self.assertRaisesRegex(ExternalConsumerBindingError, "MARKER_SYMLINK"):
                validate_binding(binding, consumer, ROOT)


if __name__ == "__main__":
    unittest.main()
