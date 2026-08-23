#!/usr/bin/env python3
"""Bounded no-secret OpenHands Software Agent SDK adapter for local execution."""
from __future__ import annotations

from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from fnmatch import fnmatchcase

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FRAMEWORK_ROOT / ".adwf"))
from lib.ai_work_contracts import build_work_result, path_is_allowed, validate_work_package  # noqa: E402
from lib.strict_json import loads as strict_loads  # noqa: E402

SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")
CONFIG_REL = ".adwf-runtime/creative-agents/openhands-local.json"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_AGENT_FILE_BYTES = 1_000_000
MAX_AGENT_LIST_ENTRIES = 200
HARD_FORBIDDEN_SURFACES = (
    ".git/**",
    ".github/**",
    ".adwf/policies/**",
    ".adwf/roadmap.json",
    ".adwf/capabilities.json",
    ".adwf/capability-live-evidence.json",
    ".adwf/config.json",
    "AGENTS.md",
    "ADWS.md",
    "SPECIFICATION.md",
    "SECURITY.md",
)


def fail(code: str) -> int:
    print(code, file=sys.stderr)
    return 2


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError("GIT_FAILED:" + " ".join(args))
    return proc.stdout.strip()


def load_runtime_config(root: Path) -> dict[str, str]:
    path = root / CONFIG_REL
    if not path.is_file():
        raise ValueError("OPENHANDS_LOCAL_CONFIG_MISSING")
    try:
        value = strict_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("OPENHANDS_LOCAL_CONFIG_INVALID") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "model", "base_url"}:
        raise ValueError("OPENHANDS_LOCAL_CONFIG_FIELDS_INVALID")
    if value.get("schema_version") != 1:
        raise ValueError("OPENHANDS_LOCAL_CONFIG_VERSION_INVALID")
    model = value.get("model")
    base_url = value.get("base_url")
    if not isinstance(model, str) or not model.strip() or model != model.strip() or len(model) > 200 or any(ch in model for ch in "\r\n"):
        raise ValueError("OPENHANDS_LOCAL_MODEL_INVALID")
    if not isinstance(base_url, str) or not base_url.strip() or base_url != base_url.strip() or len(base_url) > 500:
        raise ValueError("OPENHANDS_LOCAL_BASE_URL_INVALID")
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in LOOPBACK_HOSTS:
        raise ValueError("OPENHANDS_LOCAL_NON_LOOPBACK_FORBIDDEN")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OPENHANDS_LOCAL_BASE_URL_AUTH_OR_QUERY_FORBIDDEN")
    return {"model": model, "base_url": base_url}


def _secret_env_leaked(environ: dict[str, str]) -> bool:
    for name in environ:
        if name == "ADWF_AGENT_SECRETS_AUTHORITY":
            continue
        upper = name.upper()
        if any(marker in upper for marker in SECRET_MARKERS):
            return True
    return False


def _extract_snapshot(root: Path, base_sha: str, target: Path) -> None:
    archive = target.parent / "snapshot.tar"
    proc = subprocess.run(
        ["git", "archive", "--format=tar", "-o", str(archive), base_sha],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError("OPENHANDS_LOCAL_ARCHIVE_FAILED")
    with tarfile.open(archive, "r") as handle:
        handle.extractall(target, filter="data")
    archive.unlink(missing_ok=True)


def _files(root: Path) -> dict[str, tuple[str, bytes | str]]:
    rows: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows[rel] = ("symlink", os.readlink(path))
        elif path.is_file():
            rows[rel] = ("file", path.read_bytes())
    return rows


def changed_paths(snapshot: Path, work: Path) -> list[str]:
    before = _files(snapshot)
    after = _files(work)
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def hard_forbidden(path: str) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in HARD_FORBIDDEN_SURFACES)


def _bounded_target(workspace: Path, raw_path: str, *, allow_root: bool = False) -> tuple[str, Path]:
    if not isinstance(raw_path, str) or not raw_path or raw_path != raw_path.strip() or len(raw_path) > 1000:
        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_INVALID")
    if "\x00" in raw_path or "\\" in raw_path:
        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_INVALID")
    base = workspace.resolve(strict=True)
    if not base.is_dir():
        raise ValueError("OPENHANDS_LOCAL_TOOL_WORKSPACE_INVALID")
    if raw_path == ".":
        if allow_root:
            return ".", base
        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_INVALID")
    windows = PureWindowsPath(raw_path)
    if Path(raw_path).is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError("OPENHANDS_LOCAL_TOOL_ABSOLUTE_PATH_FORBIDDEN")
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_TRAVERSAL_FORBIDDEN")
    current = base
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("OPENHANDS_LOCAL_TOOL_SYMLINK_FORBIDDEN")
    target = (base / raw_path).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("OPENHANDS_LOCAL_TOOL_PATH_ESCAPE_FORBIDDEN") from exc
    return "/".join(parts), target


def _bounded_write_target(workspace: Path, raw_path: str, package: dict) -> tuple[str, Path]:
    rel, target = _bounded_target(workspace, raw_path)
    if hard_forbidden(rel) or not path_is_allowed(rel, package):
        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_SURFACE_FORBIDDEN:" + rel)
    return rel, target


def _bounded_read(workspace: Path, raw_path: str) -> str:
    rel, target = _bounded_target(workspace, raw_path)
    if not target.is_file():
        raise ValueError("OPENHANDS_LOCAL_TOOL_READ_NOT_FILE:" + rel)
    if target.stat().st_size > MAX_AGENT_FILE_BYTES:
        raise ValueError("OPENHANDS_LOCAL_TOOL_READ_TOO_LARGE:" + rel)
    return target.read_text(encoding="utf-8")


def _bounded_list(workspace: Path, raw_path: str) -> str:
    rel, target = _bounded_target(workspace, raw_path, allow_root=True)
    if not target.is_dir():
        raise ValueError("OPENHANDS_LOCAL_TOOL_LIST_NOT_DIRECTORY:" + rel)
    entries = sorted(target.iterdir(), key=lambda item: item.name)
    if len(entries) > MAX_AGENT_LIST_ENTRIES:
        raise ValueError("OPENHANDS_LOCAL_TOOL_LIST_TOO_LARGE:" + rel)
    rows = []
    for entry in entries:
        suffix = "@" if entry.is_symlink() else "/" if entry.is_dir() else ""
        rows.append(entry.name + suffix)
    return "\n".join(rows)


def _bounded_write(workspace: Path, raw_path: str, content: str, package: dict) -> str:
    if not isinstance(content, str):
        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_CONTENT_INVALID")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_AGENT_FILE_BYTES:
        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_TOO_LARGE")
    rel, target = _bounded_write_target(workspace, raw_path, package)
    if target.exists() and not target.is_file():
        raise ValueError("OPENHANDS_LOCAL_TOOL_WRITE_NOT_FILE:" + rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    parent = target.parent.resolve(strict=True)
    base = workspace.resolve(strict=True)
    try:
        parent.relative_to(base)
    except ValueError as exc:
        raise ValueError("OPENHANDS_LOCAL_TOOL_PARENT_ESCAPE_FORBIDDEN") from exc
    if target.is_symlink():
        raise ValueError("OPENHANDS_LOCAL_TOOL_SYMLINK_FORBIDDEN")
    target.write_text(content, encoding="utf-8")
    return "wrote " + rel


def _bounded_delete(workspace: Path, raw_path: str, package: dict) -> str:
    rel, target = _bounded_write_target(workspace, raw_path, package)
    if not target.is_file():
        raise ValueError("OPENHANDS_LOCAL_TOOL_DELETE_NOT_FILE:" + rel)
    target.unlink()
    return "deleted " + rel


def _bounded_operation(workspace: Path, package: dict, operation: str, raw_path: str, content: str | None) -> str:
    if operation == "read":
        return _bounded_read(workspace, raw_path)
    if operation == "list":
        return _bounded_list(workspace, raw_path)
    if operation == "write":
        return _bounded_write(workspace, raw_path, content, package)
    if operation == "delete":
        return _bounded_delete(workspace, raw_path, package)
    raise ValueError("OPENHANDS_LOCAL_TOOL_OPERATION_INVALID")


def _apply_changes(real_root: Path, snapshot: Path, work: Path, changed: list[str]) -> None:
    before = _files(snapshot)
    after = _files(work)
    for rel in changed:
        if (before.get(rel) or ("",))[0] == "symlink" or (after.get(rel) or ("",))[0] == "symlink":
            raise ValueError("OPENHANDS_LOCAL_SYMLINK_CHANGE_FORBIDDEN:" + rel)
        target = real_root / rel
        entry = after.get(rel)
        if entry is None:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.exists():
                raise ValueError("OPENHANDS_LOCAL_DIRECTORY_CHANGE_FORBIDDEN:" + rel)
            continue
        kind, content = entry
        if kind != "file" or not isinstance(content, bytes):
            raise ValueError("OPENHANDS_LOCAL_FILE_TYPE_FORBIDDEN:" + rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _prompt(package: dict) -> str:
    acceptance = "\n".join("- " + str(item) for item in package.get("acceptance_criteria") or [])
    allowed = "\n".join("- " + str(item) for item in package.get("allowed_write_surfaces") or [])
    forbidden = "\n".join("- " + str(item) for item in package.get("forbidden_write_surfaces") or [])
    return (
        "You are a bounded Creative Executor inside ADWF. Edit files only; do not claim trusted verification. "
        "No shell, Git, GitHub, merge, push, policy, lease, or provider authority is available.\n\n"
        "Goal:\n" + str(package.get("goal") or "") + "\n\n"
        "Acceptance criteria:\n" + acceptance + "\n\n"
        "Allowed write surfaces:\n" + allowed + "\n\n"
        "Forbidden write surfaces:\n" + forbidden + "\n\n"
        "Use only the bounded file tool with repository-relative / paths. "
        "Work only inside the provided snapshot. Finish after making the smallest changes that satisfy the goal."
    )


def run_openhands(workspace: Path, package: dict, config: dict[str, str]) -> float:
    try:
        from collections.abc import Sequence
        from typing import Literal
        from pydantic import Field
        from openhands.sdk import LLM, Action, Agent, Conversation, Observation, ToolDefinition
        from openhands.sdk.tool import Tool, ToolExecutor, register_tool
    except Exception as exc:
        raise RuntimeError("OPENHANDS_SDK_UNAVAILABLE") from exc

    class ADWFBoundedFileAction(Action):
        operation: Literal["read", "list", "write", "delete"] = Field(description="Bounded file operation")
        path: str = Field(description="Repository-relative / path inside the disposable snapshot")
        content: str | None = Field(default=None, description="UTF-8 content required only for write")

    class ADWFBoundedFileObservation(Observation):
        pass

    class ADWFBoundedFileExecutor(ToolExecutor[ADWFBoundedFileAction, ADWFBoundedFileObservation]):
        def __init__(self, root: Path, work_package: dict):
            self.root = root.resolve(strict=True)
            self.package = work_package

        def __call__(self, action: ADWFBoundedFileAction, conversation=None) -> ADWFBoundedFileObservation:  # noqa: ARG002
            try:
                value = _bounded_operation(self.root, self.package, action.operation, action.path, action.content)
                return ADWFBoundedFileObservation.from_text(text=value)
            except (OSError, UnicodeError, ValueError) as exc:
                return ADWFBoundedFileObservation.from_text(text=str(exc).splitlines()[0][:500], is_error=True)

    description = (
        "ADWF-owned bounded file tool. Operations: read, list, write, delete. "
        "Paths must be repository-relative using / and remain inside the disposable snapshot. "
        "Writes and deletes are checked against the exact AIWorkPackage allowed/forbidden surfaces before filesystem effect. "
        "Absolute paths, traversal, backslashes and symlinks are rejected."
    )

    class ADWFBoundedFileTool(ToolDefinition[ADWFBoundedFileAction, ADWFBoundedFileObservation]):
        @classmethod
        def create(cls, conv_state, package: dict) -> Sequence[ToolDefinition]:
            root = Path(conv_state.workspace.working_dir).resolve(strict=True)
            executor = ADWFBoundedFileExecutor(root, package)
            return [
                cls(
                    description=description,
                    action_type=ADWFBoundedFileAction,
                    observation_type=ADWFBoundedFileObservation,
                    executor=executor,
                )
            ]

    register_tool(ADWFBoundedFileTool.name, ADWFBoundedFileTool)
    llm = LLM(
        model=config["model"],
        api_key="adwf-local-no-secret",
        base_url=config["base_url"],
        usage_id="adwf-openhands-local",
        drop_params=True,
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name=ADWFBoundedFileTool.name, params={"package": package})],
    )
    conversation = Conversation(agent=agent, workspace=str(workspace))
    conversation.send_message(_prompt(package))
    conversation.run()
    metrics = getattr(llm, "metrics", None)
    cost = getattr(metrics, "accumulated_cost", None)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise RuntimeError("OPENHANDS_LOCAL_COST_NOT_VERIFIED")
    return float(cost)


def execute(root: Path, request_path: Path, result_path: Path, environ: dict[str, str]) -> int:
    if _secret_env_leaked(environ):
        return fail("OPENHANDS_LOCAL_SECRET_ENVIRONMENT_LEAK")
    if environ.get("ADWF_AGENT_NETWORK_AUTHORITY") != "DECLARED_EXTERNAL":
        return fail("OPENHANDS_LOCAL_NETWORK_AUTHORITY_INVALID")
    if environ.get("ADWF_AGENT_SECRETS_AUTHORITY") != "FORBIDDEN":
        return fail("OPENHANDS_LOCAL_SECRETS_AUTHORITY_INVALID")
    if not request_path.is_file():
        return fail("OPENHANDS_LOCAL_REQUEST_MISSING")
    try:
        request = strict_loads(request_path.read_text(encoding="utf-8"))
    except Exception:
        return fail("OPENHANDS_LOCAL_REQUEST_INVALID")
    if not isinstance(request, dict):
        return fail("OPENHANDS_LOCAL_REQUEST_NOT_OBJECT")
    package = request.get("work_package")
    if not isinstance(package, dict) or request.get("work_package_digest") != package.get("package_digest"):
        return fail("OPENHANDS_LOCAL_PACKAGE_BINDING_INVALID")
    errors = validate_work_package(package)
    if errors:
        return fail("OPENHANDS_LOCAL_PACKAGE_INVALID")
    if environ.get("ADWF_RUN_ID") != package.get("run_id") or environ.get("ADWF_PHASE") != package.get("phase"):
        return fail("OPENHANDS_LOCAL_ENV_BINDING_INVALID")
    if package.get("monetary_budget_usd") != 0:
        return fail("OPENHANDS_LOCAL_BUDGET_INVALID")
    if sorted(package.get("required_evidence") or []) != ["changed_paths", "verification_claims"]:
        return fail("OPENHANDS_LOCAL_EVIDENCE_PROFILE_UNSUPPORTED")
    current = ""
    try:
        current = git(root, "rev-parse", "HEAD")
        if current != package.get("base_sha"):
            return fail("OPENHANDS_LOCAL_STALE_BASE")
        if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            return fail("OPENHANDS_LOCAL_REAL_WORKTREE_DIRTY")
        config = load_runtime_config(root)
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", CONFIG_REL],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if ignored.returncode != 0:
            return fail("OPENHANDS_LOCAL_CONFIG_MUST_BE_GITIGNORED")
        with tempfile.TemporaryDirectory(prefix="adwf-openhands-") as tmp:
            temp_root = Path(tmp)
            baseline = temp_root / "baseline"
            work = temp_root / "work"
            baseline.mkdir()
            work.mkdir()
            _extract_snapshot(root, current, baseline)
            shutil.copytree(baseline, work, dirs_exist_ok=True, symlinks=True)
            cost = run_openhands(work, package, config)
            if cost != 0.0:
                return fail("OPENHANDS_LOCAL_NONZERO_COST")
            changed = changed_paths(baseline, work)
            if not changed:
                return fail("OPENHANDS_LOCAL_NO_CHANGES")
            forbidden = [path for path in changed if hard_forbidden(path) or not path_is_allowed(path, package)]
            if forbidden:
                return fail("OPENHANDS_LOCAL_WRITE_SURFACE_FORBIDDEN:" + forbidden[0])
            _apply_changes(root, baseline, work, changed)
        actual_changed = sorted(
            set(
                line.strip()
                for line in git(root, "diff", "--name-only", "HEAD", "--").splitlines()
                if line.strip()
            )
            | set(
                line.strip()
                for line in git(root, "ls-files", "--others", "--exclude-standard").splitlines()
                if line.strip()
            )
        )
        if actual_changed != changed:
            raise RuntimeError("OPENHANDS_LOCAL_CHANGED_PATHS_MISMATCH")
        subprocess.run(["git", "add", "-A", "--", *changed], cwd=root, check=True)
        commit = subprocess.run(
            [
                "git",
                "-c", "user.name=ADWF OpenHands Adapter",
                "-c", "user.email=adwf-openhands@invalid",
                "commit", "-q", "-m",
                "[ADWF] bounded OpenHands creative result",
            ],
            cwd=root,
            check=False,
        )
        if commit.returncode:
            raise RuntimeError("OPENHANDS_LOCAL_COMMIT_FAILED")
        head = git(root, "rev-parse", "HEAD")
        if head == current:
            raise RuntimeError("OPENHANDS_LOCAL_HEAD_NOT_ADVANCED")
        if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise RuntimeError("OPENHANDS_LOCAL_REAL_WORKTREE_NOT_CLEAN")
        result = build_work_result(
            package,
            outcome="PASS",
            head_sha=head,
            changed_paths=changed,
            verification_claims=[
                "openhands_sdk_conversation_completed",
                "adwf_wrapper_scope_validated",
                "adwf_wrapper_zero_cost_observed",
            ],
            evidence_claims=["changed_paths", "verification_claims"],
            summary_ru="OpenHands SDK выполнил bounded file-editing slice; результат остаётся LOW_TRUST до ADWF verification.",
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return 0
    except Exception as exc:
        # The agent only works in a disposable snapshot until all checks pass.
        # After application, any exception must leave no uncommitted residue.
        try:
            if root.joinpath(".git").exists():
                reset_target = current if current else "HEAD"
                subprocess.run(["git", "reset", "--hard", reset_target], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                subprocess.run(["git", "clean", "-fd"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        finally:
            return fail(str(exc).splitlines()[0][:240])


def main() -> int:
    request_raw = os.environ.get("ADWF_ACTION_REQUEST", "")
    result_raw = os.environ.get("ADWF_ACTION_RESULT", "")
    if not request_raw or not result_raw:
        return fail("OPENHANDS_LOCAL_CHANNEL_MISSING")
    return execute(Path.cwd().resolve(), Path(request_raw).resolve(), Path(result_raw).resolve(), dict(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
