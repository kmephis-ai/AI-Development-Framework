import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.ai_work_contracts import compile_work_package
from lib.creative_agent_qualification import adapter_by_id, validate_qualification_report
from lib.strict_json import loads as strict_loads


def load_wrapper():
    path = ROOT / ".adwf/scripts/openhands_agent_adapter.py"
    spec = importlib.util.spec_from_file_location("adwf_openhands_agent_adapter", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def init_git(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "src").mkdir(parents=True)
    (root / "src/input.txt").write_text("before\n", encoding="utf-8")
    (root / ".gitignore").write_text(".adwf-runtime/\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "base"],
        cwd=root,
        check=True,
    )
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def package(base: str):
    state = {
        "run_id": "run-openhands-test",
        "roadmap_id": "OPENHANDS-001",
        "issue_id": "268",
        "revision": 1,
        "phase": "EXECUTE",
        "work_type": "feature",
        "risk": "R1",
        "subject_sha": base,
        "allowed_write_surfaces": ["src/**"],
        "forbidden_write_surfaces": [".git/**", ".adwf-runtime/**"],
        "required_evidence": ["changed_paths", "verification_claims"],
    }
    return compile_work_package(
        state,
        {"task_ru": "Изменить src/input.txt через bounded OpenHands adapter"},
        created_at="2026-08-23T00:00:00Z",
    )


def request_value(pkg):
    return {
        "schema_version": 3,
        "idempotency_key": "q" * 64,
        "run_id": pkg["run_id"],
        "revision": pkg["revision"],
        "brief_id": pkg["roadmap_id"],
        "phase": pkg["phase"],
        "capability": "edit",
        "subject_sha": pkg["base_sha"],
        "risk": pkg["risk"],
        "work_type": pkg["work_type"],
        "work_package": pkg,
        "work_package_digest": pkg["package_digest"],
    }


def fake_openhands(cost: float):
    sdk = types.ModuleType("openhands.sdk")
    tools = types.ModuleType("openhands.tools")
    file_editor = types.ModuleType("openhands.tools.file_editor")
    task_tracker = types.ModuleType("openhands.tools.task_tracker")
    package_mod = types.ModuleType("openhands")

    class Metrics:
        accumulated_cost = cost

    class LLM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.metrics = Metrics()

    class Tool:
        def __init__(self, name):
            self.name = name

    class Agent:
        def __init__(self, llm, tools):
            self.llm = llm
            self.tools = tools

    class Conversation:
        def __init__(self, agent, workspace):
            self.agent = agent
            self.workspace = workspace
            self.message = None

        def send_message(self, message):
            self.message = message

        def run(self):
            target = Path(self.workspace) / "src/input.txt"
            target.write_text("after\n", encoding="utf-8")

    class FileEditorTool:
        name = "file_editor"

    class TaskTrackerTool:
        name = "task_tracker"

    sdk.LLM = LLM
    sdk.Agent = Agent
    sdk.Conversation = Conversation
    sdk.Tool = Tool
    file_editor.FileEditorTool = FileEditorTool
    task_tracker.TaskTrackerTool = TaskTrackerTool
    return {
        "openhands": package_mod,
        "openhands.sdk": sdk,
        "openhands.tools": tools,
        "openhands.tools.file_editor": file_editor,
        "openhands.tools.task_tracker": task_tracker,
    }


class OpenHandsAdapterTests(unittest.TestCase):
    def test_canonical_adapter_is_external_but_no_secret_and_not_live_verified(self):
        adapter = adapter_by_id(ROOT, "openhands-local")
        self.assertEqual(adapter["kind"], "EXTERNAL_COMMAND")
        self.assertEqual(
            adapter["authority"],
            {"network": "DECLARED_EXTERNAL", "secrets": "FORBIDDEN", "filesystem": "PACKAGE_SCOPED"},
        )
        self.assertEqual(adapter["monetary_budget_usd"], 0)
        report = strict_loads((ROOT / adapter["qualification_report"]).read_text(encoding="utf-8"))
        self.assertEqual(validate_qualification_report(report, adapter, ROOT), [])
        self.assertFalse(report["real_external_agent_verified"])

    def test_external_report_cannot_self_promote_live_verification(self):
        adapter = adapter_by_id(ROOT, "openhands-local")
        report = strict_loads((ROOT / adapter["qualification_report"]).read_text(encoding="utf-8"))
        forged = copy.deepcopy(report)
        forged["real_external_agent_verified"] = True
        payload = {key: value for key, value in forged.items() if key != "report_sha256"}
        forged["report_sha256"] = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        errors = validate_qualification_report(forged, adapter, ROOT)
        self.assertIn("QUALIFICATION_REPORT_BINDING_MISMATCH", errors)
        self.assertIn("REFERENCE_AGENT_CANNOT_VERIFY_EXTERNAL_AGENT", errors)

    def test_runtime_config_is_loopback_only_and_contains_no_secret_field(self):
        module = load_wrapper()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / module.CONFIG_REL
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"schema_version": 1, "model": "openai/local-coder", "base_url": "http://127.0.0.1:11434/v1"}),
                encoding="utf-8",
            )
            self.assertEqual(module.load_runtime_config(root)["model"], "openai/local-coder")
            path.write_text(
                json.dumps({"schema_version": 1, "model": "x", "base_url": "https://api.example.com/v1"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "NON_LOOPBACK"):
                module.load_runtime_config(root)
            path.write_text(
                json.dumps({"schema_version": 1, "model": "x", "base_url": "http://localhost:8000/v1", "api_key": "secret"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "FIELDS_INVALID"):
                module.load_runtime_config(root)

    def test_hard_forbidden_control_surfaces_do_not_depend_on_package_allowlist(self):
        module = load_wrapper()
        for path in (
            ".github/workflows/x.yml",
            ".adwf/policies/effective-policy.json",
            ".adwf/roadmap.json",
            ".adwf/capabilities.json",
            ".adwf/config.json",
            "AGENTS.md",
        ):
            self.assertTrue(module.hard_forbidden(path), path)
        self.assertFalse(module.hard_forbidden("src/app.py"))

    def _execute_case(self, cost: float, *, secret=False):
        module = load_wrapper()
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        base = init_git(root)
        cfg = root / module.CONFIG_REL
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps({"schema_version": 1, "model": "openai/local-coder", "base_url": "http://localhost:11434/v1"}),
            encoding="utf-8",
        )
        pkg = package(base)
        request = root / ".adwf-runtime/supervisor/request.json"
        result = root / ".adwf-runtime/supervisor/result.json"
        request.parent.mkdir(parents=True, exist_ok=True)
        request.write_text(json.dumps(request_value(pkg), ensure_ascii=False), encoding="utf-8")
        env = {
            "ADWF_RUN_ID": pkg["run_id"],
            "ADWF_PHASE": pkg["phase"],
            "ADWF_AGENT_NETWORK_AUTHORITY": "DECLARED_EXTERNAL",
            "ADWF_AGENT_SECRETS_AUTHORITY": "FORBIDDEN",
        }
        if secret:
            env["GITHUB_TOKEN"] = "must-not-leak"
        modules = fake_openhands(cost)
        with patch.dict(sys.modules, modules, clear=False):
            code = module.execute(root, request, result, env)
        return holder, root, base, result, code

    def test_fake_sdk_zero_cost_edits_snapshot_then_wrapper_commits_bound_result(self):
        holder, root, base, result_path, code = self._execute_case(0.0)
        try:
            self.assertEqual(code, 0)
            result = strict_loads(result_path.read_text(encoding="utf-8"))
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            self.assertNotEqual(head, base)
            self.assertEqual(result["head_sha"], head)
            self.assertEqual(result["changed_paths"], ["src/input.txt"])
            self.assertEqual(result["cost_usd"], 0)
            self.assertEqual((root / "src/input.txt").read_text(encoding="utf-8"), "after\n")
            status = subprocess.check_output(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root, text=True
            ).strip()
            self.assertEqual(status, "")
        finally:
            holder.cleanup()

    def test_positive_cost_is_rejected_before_real_workspace_mutation(self):
        holder, root, base, result_path, code = self._execute_case(0.25)
        try:
            self.assertNotEqual(code, 0)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            self.assertEqual(head, base)
            self.assertEqual((root / "src/input.txt").read_text(encoding="utf-8"), "before\n")
            self.assertFalse(result_path.exists())
        finally:
            holder.cleanup()

    def test_secret_environment_is_rejected_before_sdk_run(self):
        holder, root, base, result_path, code = self._execute_case(0.0, secret=True)
        try:
            self.assertNotEqual(code, 0)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            self.assertEqual(head, base)
            self.assertFalse(result_path.exists())
        finally:
            holder.cleanup()


if __name__ == "__main__":
    unittest.main()
