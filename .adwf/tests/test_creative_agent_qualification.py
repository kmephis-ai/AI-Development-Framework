import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.action_executors import _run_agent_command, creative_executor, ExecutorWait
from lib.ai_work_contracts import compile_work_package, build_work_result
from lib.contracts import validate
from lib.creative_agent_qualification import (
    adapter_by_id,
    command_argv,
    load_qualified_command_adapter,
    load_registry,
    reference_qualification_report,
    run_reference_qualification,
    sanitized_agent_environment,
    seal_registry,
    validate_qualification_report,
    validate_registry,
    verify_local_command_result,
)

BASE = "a" * 40


def state(**updates):
    value = {
        "run_id": "run-agentqual",
        "roadmap_id": "AGENTQUAL-001",
        "issue_id": "99",
        "revision": 1,
        "phase": "EXECUTE",
        "work_type": "verification",
        "risk": "R1",
        "subject_sha": BASE,
    }
    value.update(updates)
    return value


def envelope(package=None):
    value = {"capability": "edit"}
    if package is not None:
        value["work_package"] = package
        value["work_package_digest"] = package["package_digest"]
    return value


class CreativeAgentQualificationTests(unittest.TestCase):
    def test_canonical_registry_and_report_are_strict_and_bound(self):
        registry = load_registry(ROOT)
        schema = json.loads((ROOT / ".adwf/schemas/creative-agent-adapters.schema.json").read_text())
        self.assertEqual(validate(registry, schema), [])
        self.assertEqual(validate_registry(registry, ROOT), [])
        adapter = adapter_by_id(ROOT, "reference-local")
        report = json.loads((ROOT / adapter["qualification_report"]).read_text())
        report_schema = json.loads((ROOT / ".adwf/schemas/creative-agent-qualification-report.schema.json").read_text())
        self.assertEqual(validate(report, report_schema), [])
        self.assertEqual(validate_qualification_report(report, adapter, ROOT), [])
        self.assertEqual(report, reference_qualification_report(adapter))
        self.assertFalse(report["real_external_agent_verified"])
        self.assertTrue(report["low_trust_result"])
        self.assertEqual(report["executor_auth"], "NONE")
        self.assertEqual(report["qualification_profile_id"], "CREATIVE_AGENT_COMMAND_V2")
        self.assertEqual(report["qualification_profile_version"], 2)

    def test_tampered_registry_report_and_authority_fail_closed(self):
        registry = load_registry(ROOT)
        tampered = copy.deepcopy(registry)
        tampered["adapters"][0]["timeout_seconds"] += 1
        self.assertTrue(any("DIGEST_MISMATCH" in item for item in validate_registry(tampered, ROOT)))

        bad_authority = copy.deepcopy(registry)
        bad_authority["adapters"][0]["authority"]["network"] = "DECLARED_EXTERNAL"
        bad_authority = seal_registry({k: v for k, v in bad_authority.items() if k != "registry_sha256"})
        self.assertIn("COMMAND_ADAPTER_AUTHORITY_FORBIDDEN:reference-local", validate_registry(bad_authority, ROOT))

        adapter = adapter_by_id(ROOT, "reference-local")
        report = json.loads((ROOT / adapter["qualification_report"]).read_text())
        report["real_external_agent_verified"] = True
        self.assertTrue(validate_qualification_report(report, adapter, ROOT))

    def test_duplicate_adapter_and_nonzero_cost_fail(self):
        registry = load_registry(ROOT)
        raw = {k: copy.deepcopy(v) for k, v in registry.items() if k != "registry_sha256"}
        raw["adapters"].append(copy.deepcopy(raw["adapters"][0]))
        duplicate = seal_registry(raw)
        self.assertIn("ADAPTER_ID_DUPLICATE", validate_registry(duplicate, ROOT))

        raw = {k: copy.deepcopy(v) for k, v in registry.items() if k != "registry_sha256"}
        raw["adapters"][0]["monetary_budget_usd"] = 1
        costly = seal_registry(raw)
        errors = validate_registry(costly, ROOT)
        self.assertTrue(any("COST" in item or "SCHEMA" in item for item in errors))

    def test_environment_is_secret_filtered_and_authority_bound(self):
        adapter = load_qualified_command_adapter(ROOT, "reference-local", "EXECUTE")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = root / "request.json"
            result = root / "result.json"
            env = sanitized_agent_environment(
                {
                    "PATH": os.environ.get("PATH", ""),
                    "GITHUB_TOKEN": "secret",
                    "OPENAI_API_KEY": "secret",
                    "PASSWORD": "secret",
                    "LANG": "C.UTF-8",
                },
                request=request,
                result=result,
                state=state(),
                adapter=adapter,
            )
        self.assertIn("PATH", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("PASSWORD", env)
        self.assertEqual(env["ADWF_AGENT_ADAPTER_ID"], "reference-local")
        self.assertEqual(env["ADWF_AGENT_EXECUTOR_AUTH"], "NONE")


    def test_provider_managed_executor_auth_is_external_only_secret_free_and_fail_closed(self):
        registry = load_registry(ROOT)
        schema = json.loads((ROOT / ".adwf/schemas/creative-agent-adapters.schema.json").read_text())

        external = copy.deepcopy(registry)
        external_adapter = next(item for item in external["adapters"] if item["id"] == "openhands-local")
        external_adapter["executor_auth"] = "PROVIDER_MANAGED_SESSION"
        self.assertEqual(validate(external, schema), [])

        secret_delivery = copy.deepcopy(external)
        next(item for item in secret_delivery["adapters"] if item["id"] == "openhands-local")["authority"]["secrets"] = "DECLARED_REQUIRED"
        self.assertTrue(validate(secret_delivery, schema))

        reference = copy.deepcopy(registry)
        next(item for item in reference["adapters"] if item["id"] == "reference-local")["executor_auth"] = "PROVIDER_MANAGED_SESSION"
        self.assertTrue(validate(reference, schema))

        unknown = copy.deepcopy(registry)
        next(item for item in unknown["adapters"] if item["id"] == "openhands-local")["executor_auth"] = "UNKNOWN"
        self.assertTrue(validate(unknown, schema))

    def test_provider_managed_executor_auth_marker_is_non_secret_and_credentials_are_not_forwarded(self):
        registry = load_registry(ROOT)
        adapter = copy.deepcopy(next(item for item in registry["adapters"] if item["id"] == "openhands-local"))
        adapter["executor_auth"] = "PROVIDER_MANAGED_SESSION"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = sanitized_agent_environment(
                {
                    "PATH": os.environ.get("PATH", ""),
                    "GITHUB_TOKEN": "github-secret",
                    "GH_TOKEN": "gh-secret",
                    "OPENAI_API_KEY": "api-secret",
                    "PASSWORD": "password-secret",
                    "CREDENTIAL_FILE": "credential-secret",
                    "PRIVATE_KEY": "private-secret",
                },
                request=root / "request.json",
                result=root / "result.json",
                state=state(),
                adapter=adapter,
            )
        self.assertEqual(env["ADWF_AGENT_EXECUTOR_AUTH"], "PROVIDER_MANAGED_SESSION")
        serialized = "\n".join(f"{key}={value}" for key, value in sorted(env.items()))
        for secret in ("github-secret", "gh-secret", "api-secret", "password-secret", "credential-secret", "private-secret"):
            self.assertNotIn(secret, serialized)
        for name in ("GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY", "PASSWORD", "CREDENTIAL_FILE", "PRIVATE_KEY"):
            self.assertNotIn(name, env)

    def test_reference_qualification_runs_offline_and_remains_synthetic(self):
        result = run_reference_qualification(ROOT)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["network"], "NONE")
        self.assertEqual(result["secrets"], "FORBIDDEN")
        self.assertEqual(result["executor_auth"], "NONE")
        self.assertEqual(result["monetary_budget_usd"], 0)
        self.assertTrue(result["low_trust_result"])
        self.assertFalse(result["real_external_agent_verified"])

    def test_raw_agent_command_without_adapter_is_rejected(self):
        with patch.dict(os.environ, {"ADWF_AGENT_COMMAND": "python arbitrary.py"}, clear=False):
            os.environ.pop("ADWF_AGENT_ADAPTER_ID", None)
            result = _run_agent_command(ROOT, state(), "k" * 64, envelope())
        self.assertEqual(result["outcome"], "FAIL")
        self.assertEqual(result["reason_codes"], ["AGENT_COMMAND_UNQUALIFIED"])

    def test_no_adapter_still_waits_for_agent(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADWF_AGENT_COMMAND", None)
            os.environ.pop("ADWF_AGENT_ADAPTER_ID", None)
            result = creative_executor(ROOT, state(), "k" * 64, envelope())
        self.assertIsInstance(result, ExecutorWait)
        self.assertEqual(result.status, "WAITING_AGENT")
        self.assertEqual(result.reason, "CREATIVE_AGENT_RESULT_REQUIRED")

    def test_reference_adapter_requires_explicit_test_only_opt_in(self):
        with patch.dict(os.environ, {"ADWF_AGENT_ADAPTER_ID": "reference-local"}, clear=False):
            os.environ.pop("ADWF_AGENT_COMMAND", None)
            os.environ.pop("ADWF_ALLOW_REFERENCE_AGENT", None)
            result = _run_agent_command(ROOT, state(), "k" * 64, envelope())
        self.assertEqual(result["outcome"], "FAIL")
        self.assertEqual(result["reason_codes"], ["REFERENCE_AGENT_RUNTIME_FORBIDDEN"])

    def test_command_override_is_forbidden_even_for_declared_adapter(self):
        with patch.dict(os.environ, {"ADWF_AGENT_ADAPTER_ID": "reference-local", "ADWF_AGENT_COMMAND": "echo bypass"}, clear=False):
            result = _run_agent_command(ROOT, state(), "k" * 64, envelope())
        self.assertEqual(result["outcome"], "FAIL")
        self.assertEqual(result["reason_codes"], ["AGENT_COMMAND_OVERRIDE_FORBIDDEN"])

    def _minimal_qualified_root(self, tmp):
        root = Path(tmp)
        (root / ".adwf/schemas").mkdir(parents=True)
        for rel in (
            ".adwf/creative-agent-adapters.json",
            ".adwf/creative-agent-qualification.json",
            ".adwf/schemas/creative-agent-adapters.schema.json",
            ".adwf/schemas/creative-agent-qualification-report.schema.json",
            ".adwf/scripts/reference_agent_adapter.py",
        ):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / rel).read_bytes())
        # Keep this fixture intentionally minimal. Production now has multiple
        # qualified adapters; copying the whole production registry would make
        # an unrelated adapter's command/report files mandatory in this isolated
        # reference-adapter timeout/nonzero/missing-result test.
        registry_path = root / ".adwf/creative-agent-adapters.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["adapters"] = [
            item for item in registry.get("adapters") or []
            if item.get("id") == "reference-local"
        ]
        if len(registry["adapters"]) != 1:
            raise AssertionError("minimal reference adapter fixture identity")
        registry_path.write_text(
            json.dumps(seal_registry(registry), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return root

    def test_timeout_nonzero_and_missing_result_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._minimal_qualified_root(tmp)
            env = {"ADWF_AGENT_ADAPTER_ID": "reference-local", "ADWF_ALLOW_REFERENCE_AGENT": "1"}
            pkg = compile_work_package(state(), {"task_ru": "Проверить fail-closed command execution cases"}, created_at="2026-08-16T00:00:00Z")
            with patch.dict(os.environ, env, clear=False), patch("lib.action_executors.subprocess.run", side_effect=subprocess.TimeoutExpired(["agent"], 60)):
                result = _run_agent_command(root, state(), "a" * 64, envelope(pkg))
                self.assertEqual(result["reason_codes"], ["AGENT_COMMAND_TIMEOUT"])
            fake_nonzero = subprocess.CompletedProcess(["agent"], 3, stdout="", stderr="boom")
            with patch.dict(os.environ, env, clear=False), patch("lib.action_executors.subprocess.run", return_value=fake_nonzero):
                result = _run_agent_command(root, state(), "b" * 64, envelope(pkg))
                self.assertEqual(result["reason_codes"], ["AGENT_COMMAND_FAILED"])
            fake_zero = subprocess.CompletedProcess(["agent"], 0, stdout="", stderr="")
            with patch.dict(os.environ, env, clear=False), patch("lib.action_executors.subprocess.run", return_value=fake_zero):
                result = _run_agent_command(root, state(), "c" * 64, envelope(pkg))
                self.assertEqual(result["reason_codes"], ["AGENT_RESULT_MISSING"])

    def test_local_result_verifier_rejects_forged_head_and_changed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "src").mkdir()
            (root / "src/a.txt").write_text("a\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "base"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            pkg = compile_work_package(state(subject_sha=base, allowed_write_surfaces=["src/**"]), {"task_ru": "Проверить local result exact binding"}, created_at="2026-08-16T00:00:00Z")
            (root / "src/a.txt").write_text("b\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "head"], cwd=root, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            good = build_work_result(pkg, outcome="PASS", head_sha=head, changed_paths=["src/a.txt"], verification_claims=["tests"], evidence_claims=["changed_paths", "verification_claims"])
            self.assertEqual(verify_local_command_result(root, pkg, good), [])
            forged = dict(good)
            forged["head_sha"] = "f" * 40
            self.assertIn("LOCAL_AGENT_HEAD_MISMATCH", verify_local_command_result(root, pkg, forged))
            forged = dict(good)
            forged["changed_paths"] = ["src/other.txt"]
            self.assertIn("LOCAL_AGENT_CHANGED_PATHS_MISMATCH", verify_local_command_result(root, pkg, forged))

    def test_tampered_command_bytes_invalidate_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._minimal_qualified_root(tmp)
            script = root / ".adwf/scripts/reference_agent_adapter.py"
            script.write_text(script.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
            self.assertIn("ADAPTER_COMMAND_DIGEST_MISMATCH:reference-local", validate_registry(load_registry(root), root))

    def test_command_argv_is_framework_bound(self):
        adapter = load_qualified_command_adapter(ROOT, "reference-local", "RECOVERY")
        argv = command_argv(ROOT, adapter)
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(Path(argv[1]).resolve(), (ROOT / ".adwf/scripts/reference_agent_adapter.py").resolve())


if __name__ == "__main__":
    unittest.main()
