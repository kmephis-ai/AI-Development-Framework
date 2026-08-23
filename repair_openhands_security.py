#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json
import sys

EXPECTED_HEAD = "81c5d51ee6f77c41cc9c5d2b4e65a341d1df7a6b"


def replace_once(text: str, old: str, new: str, code: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{code}:count={count}")
    return text.replace(old, new, 1)


def patch_adapter(root: Path) -> None:
    path = root / ".adwf/scripts/openhands_agent_adapter.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from pathlib import Path\n",
        "from pathlib import Path, PureWindowsPath\n",
        "PATCH_ADAPTER_PATH_IMPORT",
    )
    text = replace_once(
        text,
        'LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}\n',
        'LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}\nMAX_AGENT_FILE_BYTES = 1_000_000\nMAX_AGENT_LIST_ENTRIES = 200\n',
        "PATCH_ADAPTER_LIMITS",
    )
    old_guard = '''def hard_forbidden(path: str) -> bool:\n    return any(fnmatchcase(path, pattern) for pattern in HARD_FORBIDDEN_SURFACES)\n'''
    new_guard = '''def hard_forbidden(path: str) -> bool:\n    return any(fnmatchcase(path, pattern) for pattern in HARD_FORBIDDEN_SURFACES)\n\n\ndef _bounded_target(workspace: Path, raw_path: str, *, allow_root: bool = False) -> tuple[str, Path]:\n    if not isinstance(raw_path, str) or not raw_path or raw_path != raw_path.strip() or len(raw_path) > 1000:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_INVALID")\n    if "\\x00" in raw_path or "\\\\" in raw_path:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_INVALID")\n    base = workspace.resolve(strict=True)\n    if not base.is_dir():\n        raise ValueError("OPENHANDS_LOCAL_TOOL_WORKSPACE_INVALID")\n    if raw_path == ".":\n        if allow_root:\n            return ".", base\n        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_INVALID")\n    windows = PureWindowsPath(raw_path)\n    if Path(raw_path).is_absolute() or windows.is_absolute() or windows.drive:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_ABSOLUTE_PATH_FORBIDDEN")\n    parts = raw_path.split("/")\n    if any(part in {"", ".", ".."} for part in parts):\n        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_TRAVERSAL_FORBIDDEN")\n    current = base\n    for part in parts:\n        current = current / part\n        if current.is_symlink():\n            raise ValueError("OPENHANDS_LOCAL_TOOL_SYMLINK_FORBIDDEN")\n    target = (base / raw_path).resolve(strict=False)\n    try:\n        target.relative_to(base)\n    except ValueError as exc:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_ESCAPE_FORBIDDEN") from exc\n    return "/".join(parts), target\n\n\ndef _bounded_write_target(workspace: Path, raw_path: str, package: dict) -> tuple[str, Path]:\n    rel, target = _bounded_target(workspace, raw_path)\n    if hard_forbidden(rel) or not path_is_allowed(rel, package):\n        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_SURFACE_FORBIDDEN:" + rel)\n    return rel, target\n\n\ndef _bounded_read(workspace: Path, raw_path: str) -> str:\n    rel, target = _bounded_target(workspace, raw_path)\n    if not target.is_file():\n        raise ValueError("OPENHANDS_LOCAL_TOOL_READ_NOT_FILE:" + rel)\n    if target.stat().st_size > MAX_AGENT_FILE_BYTES:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_READ_TOO_LARGE:" + rel)\n    return target.read_text(encoding="utf-8")\n\n\ndef _bounded_list(workspace: Path, raw_path: str) -> str:\n    rel, target = _bounded_target(workspace, raw_path, allow_root=True)\n    if not target.is_dir():\n        raise ValueError("OPENHANDS_LOCAL_TOOL_LIST_NOT_DIRECTORY:" + rel)\n    entries = sorted(target.iterdir(), key=lambda item: item.name)\n    if len(entries) > MAX_AGENT_LIST_ENTRIES:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_LIST_TOO_LARGE:" + rel)\n    rows = []\n    for entry in entries:\n        suffix = "@" if entry.is_symlink() else "/" if entry.is_dir() else ""\n        rows.append(entry.name + suffix)\n    return "\\n".join(rows)\n\n\ndef _bounded_write(workspace: Path, raw_path: str, content: str, package: dict) -> str:\n    if not isinstance(content, str):\n        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_CONTENT_INVALID")\n    encoded = content.encode("utf-8")\n    if len(encoded) > MAX_AGENT_FILE_BYTES:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_TOO_LARGE")\n    rel, target = _bounded_write_target(workspace, raw_path, package)\n    if target.exists() and not target.is_file():\n        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_NOT_FILE:" + rel)\n    target.parent.mkdir(parents=True, exist_ok=True)\n    parent = target.parent.resolve(strict=True)\n    base = workspace.resolve(strict=True)\n    try:\n        parent.relative_to(base)\n    except ValueError as exc:\n        raise ValueError("OPENHANDS_LOCAL_TOOL_PARENT_ESCAPE_FORBIDDEN") from exc\n    if target.is_symlink():\n        raise ValueError("OPENHANDS_LOCAL_TOOL_SYMLINK_FORBIDDEN")\n    target.write_text(content, encoding="utf-8")\n    return "wrote " + rel\n\n\ndef _bounded_delete(workspace: Path, raw_path: str, package: dict) -> str:\n    rel, target = _bounded_write_target(workspace, raw_path, package)\n    if not target.is_file():\n        raise ValueError("OPENHANDS_LOCAL_TOOL_DELETE_NOT_FILE:" + rel)\n    target.unlink()\n    return "deleted " + rel\n\n\ndef _bounded_operation(workspace: Path, package: dict, operation: str, raw_path: str, content: str | None) -> str:\n    if operation == "read":\n        return _bounded_read(workspace, raw_path)\n    if operation == "list":\n        return _bounded_list(workspace, raw_path)\n    if operation == "write":\n        return _bounded_write(workspace, raw_path, content, package)\n    if operation == "delete":\n        return _bounded_delete(workspace, raw_path, package)\n    raise ValueError("OPENHANDS_LOCAL_TOOL_OPERATION_INVALID")\n'''
    text = replace_once(text, old_guard, new_guard, "PATCH_ADAPTER_GUARD")
    text = replace_once(
        text,
        '        "Work only inside the provided snapshot. Finish after making the smallest changes that satisfy the goal."\n',
        '        "Use only the bounded file tool with repository-relative / paths. "\n        "Work only inside the provided snapshot. Finish after making the smallest changes that satisfy the goal."\n',
        "PATCH_ADAPTER_PROMPT",
    )
    start = text.index("def run_openhands(workspace: Path, package: dict, config: dict[str, str]) -> float:\n")
    end = text.index("\n\ndef execute(root: Path", start)
    new_run = '''def run_openhands(workspace: Path, package: dict, config: dict[str, str]) -> float:\n    try:\n        from collections.abc import Sequence\n        from typing import Literal\n        from pydantic import Field\n        from openhands.sdk import LLM, Action, Agent, Conversation, Observation, ToolDefinition\n        from openhands.sdk.tool import Tool, ToolExecutor, register_tool\n    except Exception as exc:\n        raise RuntimeError("OPENHANDS_SDK_UNAVAILABLE") from exc\n\n    class ADWFBoundedFileAction(Action):\n        operation: Literal["read", "list", "write", "delete"] = Field(description="Bounded file operation")\n        path: str = Field(description="Repository-relative / path inside the disposable snapshot")\n        content: str | None = Field(default=None, description="UTF-8 content required only for write")\n\n    class ADWFBoundedFileObservation(Observation):\n        pass\n\n    class ADWFBoundedFileExecutor(ToolExecutor[ADWFBoundedFileAction, ADWFBoundedFileObservation]):\n        def __init__(self, root: Path, work_package: dict):\n            self.root = root.resolve(strict=True)\n            self.package = work_package\n\n        def __call__(self, action: ADWFBoundedFileAction, conversation=None) -> ADWFBoundedFileObservation:  # noqa: ARG002\n            try:\n                value = _bounded_operation(self.root, self.package, action.operation, action.path, action.content)\n                return ADWFBoundedFileObservation.from_text(text=value)\n            except (OSError, UnicodeError, ValueError) as exc:\n                return ADWFBoundedFileObservation.from_text(text=str(exc).splitlines()[0][:500], is_error=True)\n\n    description = (\n        "ADWF-owned bounded file tool. Operations: read, list, write, delete. "\n        "Paths must be repository-relative using / and remain inside the disposable snapshot. "\n        "Writes and deletes are checked against the exact AIWorkPackage allowed/forbidden surfaces before filesystem effect. "\n        "Absolute paths, traversal, backslashes and symlinks are rejected."\n    )\n\n    class ADWFBoundedFileTool(ToolDefinition[ADWFBoundedFileAction, ADWFBoundedFileObservation]):\n        @classmethod\n        def create(cls, conv_state, package: dict) -> Sequence[ToolDefinition]:\n            root = Path(conv_state.workspace.working_dir).resolve(strict=True)\n            executor = ADWFBoundedFileExecutor(root, package)\n            return [\n                cls(\n                    description=description,\n                    action_type=ADWFBoundedFileAction,\n                    observation_type=ADWFBoundedFileObservation,\n                    executor=executor,\n                )\n            ]\n\n    register_tool(ADWFBoundedFileTool.name, ADWFBoundedFileTool)\n    llm = LLM(\n        model=config["model"],\n        api_key="adwf-local-no-secret",\n        base_url=config["base_url"],\n        usage_id="adwf-openhands-local",\n        drop_params=True,\n    )\n    agent = Agent(\n        llm=llm,\n        tools=[Tool(name=ADWFBoundedFileTool.name, params={"package": package})],\n    )\n    conversation = Conversation(agent=agent, workspace=str(workspace))\n    conversation.send_message(_prompt(package))\n    conversation.run()\n    metrics = getattr(llm, "metrics", None)\n    cost = getattr(metrics, "accumulated_cost", None)\n    if isinstance(cost, bool) or not isinstance(cost, (int, float)):\n        raise RuntimeError("OPENHANDS_LOCAL_COST_NOT_VERIFIED")\n    return float(cost)\n'''
    text = text[:start] + new_run + text[end:]
    path.write_text(text, encoding="utf-8")


def patch_tests(root: Path) -> None:
    path = root / ".adwf/tests/test_openhands_agent_adapter.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("def fake_openhands(cost: float):\n")
    end = text.index("\n\nclass OpenHandsAdapterTests", start)
    fake = '''def fake_openhands(cost: float, *, target_path: str = "src/input.txt"):\n    sdk = types.ModuleType("openhands.sdk")\n    sdk_tool = types.ModuleType("openhands.sdk.tool")\n    package_mod = types.ModuleType("openhands")\n    pydantic_mod = types.ModuleType("pydantic")\n    registry = {}\n\n    def Field(default=None, **kwargs):  # noqa: N802, ARG001\n        return default\n\n    class Metrics:\n        accumulated_cost = cost\n\n    class LLM:\n        def __init__(self, **kwargs):\n            self.kwargs = kwargs\n            self.metrics = Metrics()\n\n    class Action:\n        def __init__(self, **kwargs):\n            for key, value in kwargs.items():\n                setattr(self, key, value)\n\n    class Observation:\n        def __init__(self, *, text="", is_error=False, **kwargs):\n            self.text = text\n            self.is_error = is_error\n            for key, value in kwargs.items():\n                setattr(self, key, value)\n\n        @classmethod\n        def from_text(cls, *, text, is_error=False, **kwargs):\n            return cls(text=text, is_error=is_error, **kwargs)\n\n    class ToolExecutor:\n        def __class_getitem__(cls, item):  # noqa: ARG003\n            return cls\n\n    class ToolDefinition:\n        name = ""\n\n        def __class_getitem__(cls, item):  # noqa: ARG003\n            return cls\n\n        def __init_subclass__(cls, **kwargs):\n            super().__init_subclass__(**kwargs)\n            cls.name = "adwf_bounded_file"\n\n        def __init__(self, **kwargs):\n            for key, value in kwargs.items():\n                setattr(self, key, value)\n\n    class Tool:\n        def __init__(self, name, params=None):\n            self.name = name\n            self.params = dict(params or {})\n\n    def register_tool(name, definition):\n        registry[name] = definition\n\n    class Agent:\n        def __init__(self, llm, tools):\n            self.llm = llm\n            self.tools = tools\n\n    class Conversation:\n        def __init__(self, agent, workspace):\n            self.agent = agent\n            self.workspace = workspace\n            self.message = None\n\n        def send_message(self, message):\n            self.message = message\n\n        def run(self):\n            self_agent_tool = self.agent.tools[0]\n            definition_cls = registry[self_agent_tool.name]\n            conv_state = types.SimpleNamespace(\n                workspace=types.SimpleNamespace(working_dir=self.workspace)\n            )\n            definition = definition_cls.create(conv_state, **self_agent_tool.params)[0]\n            action = definition.action_type(\n                operation="write", path=target_path, content="after\\n"\n            )\n            observation = definition.executor(action)\n            if observation.is_error:\n                raise RuntimeError("FAKE_BOUNDED_TOOL_REJECTED:" + observation.text)\n\n    pydantic_mod.Field = Field\n    sdk.LLM = LLM\n    sdk.Action = Action\n    sdk.Agent = Agent\n    sdk.Conversation = Conversation\n    sdk.Observation = Observation\n    sdk.ToolDefinition = ToolDefinition\n    sdk_tool.Tool = Tool\n    sdk_tool.ToolExecutor = ToolExecutor\n    sdk_tool.register_tool = register_tool\n    return {\n        "pydantic": pydantic_mod,\n        "openhands": package_mod,\n        "openhands.sdk": sdk,\n        "openhands.sdk.tool": sdk_tool,\n    }\n'''
    text = text[:start] + fake + text[end:]
    text = replace_once(
        text,
        "    def _execute_case(self, cost: float, *, secret=False):\n",
        "    def _execute_case(self, cost: float, *, secret=False, target_path=\"src/input.txt\"):\n",
        "PATCH_TEST_EXECUTE_SIGNATURE",
    )
    text = replace_once(
        text,
        "        modules = fake_openhands(cost)\n",
        "        modules = fake_openhands(cost, target_path=target_path)\n",
        "PATCH_TEST_FAKE_CALL",
    )
    marker = '\n\nif __name__ == "__main__":\n'
    extra = '''\n    def test_bounded_path_guard_rejects_absolute_traversal_windows_and_symlink_escape(self):\n        module = load_wrapper()\n        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:\n            workspace = Path(tmp)\n            (workspace / "src").mkdir()\n            outside = Path(outside_tmp)\n            for value in ("/tmp/x", "../x", "src/../x", "C:/temp/x", "src\\\\input.txt"):\n                with self.assertRaises(ValueError, msg=value):\n                    module._bounded_target(workspace, value)\n            link = workspace / "link"\n            try:\n                link.symlink_to(outside, target_is_directory=True)\n            except (OSError, NotImplementedError):\n                self.skipTest("symlink creation unavailable")\n            with self.assertRaisesRegex(ValueError, "SYMLINK"):\n                module._bounded_target(workspace, "link/escape.txt")\n\n    def test_bounded_write_rejects_package_escape_before_effect(self):\n        module = load_wrapper()\n        with tempfile.TemporaryDirectory() as tmp:\n            workspace = Path(tmp)\n            (workspace / "src").mkdir()\n            pkg = package("a" * 40)\n            with self.assertRaisesRegex(ValueError, "WRITE_SURFACE_FORBIDDEN"):\n                module._bounded_write(workspace, "README.md", "forbidden\\n", pkg)\n            self.assertFalse((workspace / "README.md").exists())\n            with self.assertRaisesRegex(ValueError, "WRITE_SURFACE_FORBIDDEN"):\n                module._bounded_write(workspace, ".github/workflows/x.yml", "bad\\n", pkg)\n            self.assertFalse((workspace / ".github/workflows/x.yml").exists())\n\n    def test_fake_sdk_absolute_host_path_is_rejected_before_host_effect(self):\n        with tempfile.TemporaryDirectory() as outside_tmp:\n            outside = Path(outside_tmp) / "escape.txt"\n            holder, root, base, result_path, code = self._execute_case(\n                0.0, target_path=str(outside)\n            )\n            try:\n                self.assertNotEqual(code, 0)\n                self.assertFalse(outside.exists())\n                head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()\n                self.assertEqual(head, base)\n                self.assertEqual((root / "src/input.txt").read_text(encoding="utf-8"), "before\\n")\n                self.assertFalse(result_path.exists())\n            finally:\n                holder.cleanup()\n'''
    text = replace_once(text, marker, extra + marker, "PATCH_TEST_EXTRA")
    path.write_text(text, encoding="utf-8")


def refresh_qualification(root: Path, trusted: Path) -> None:
    sys.path.insert(0, str(trusted / ".adwf"))
    from lib.creative_agent_qualification import reference_qualification_report, seal_registry
    from lib.strict_json import loads as strict_loads

    script = root / ".adwf/scripts/openhands_agent_adapter.py"
    registry_path = root / ".adwf/creative-agent-adapters.json"
    report_path = root / ".adwf/creative-agent-qualification-openhands-local.json"
    registry = strict_loads(registry_path.read_text(encoding="utf-8"))
    matches = [item for item in registry.get("adapters") or [] if item.get("id") == "openhands-local"]
    if len(matches) != 1:
        raise SystemExit("PATCH_ADAPTER_REGISTRY_IDENTITY")
    matches[0]["command"]["sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
    sealed = seal_registry(registry)
    registry_path.write_text(json.dumps(sealed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    adapter = next(item for item in sealed["adapters"] if item["id"] == "openhands-local")
    report = reference_qualification_report(adapter)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--trusted", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    trusted = Path(args.trusted).resolve()
    patch_adapter(source)
    patch_tests(source)
    refresh_qualification(source, trusted)
    print("OPENHANDS_SECURITY_PATCH=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
