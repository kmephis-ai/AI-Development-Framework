"""Fail-closed execution envelope for consumer Project Pack commands.

The envelope enforces properties that are portable and observable in ADWF v1:
- exact validated Project Pack/config binding;
- a minimal child environment that does not inherit secret-like variables;
- execution in an independent local Git clone of the exact source revision;
- post-command proof that the canonical checkout was not mutated;
- machine-readable self-sealed runtime evidence.

Network declarations remain requirements. This module deliberately does not claim
packet/domain confinement where the host provides no verified sandbox primitive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid

from .contracts import validate
from .consumer_profile import ConsumerProfileError, load_effective_config
from .project_packs import commands_for_pack
from .strict_json import loads as strict_loads

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SECRET_NAME = re.compile(
    r"(?i)(?:^|_)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|client[_-]?secret|credential|cookie|password|passwd|private[_-]?key|secret|session|token)(?:_|$)"
)
SAFE_INHERITED_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "TERM",
)
NETWORK_ENFORCEMENT = "DECLARATION_ONLY_NOT_ENFORCED"


class ProjectExecutionError(ValueError):
    """Deterministic runtime-safety blocker."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _run_git(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=root, env=env, text=True, capture_output=True, check=False, timeout=60,
    )
    if proc.returncode:
        raise ProjectExecutionError(f"PROJECT_EXECUTION_GIT_FAILED:{args[0]}")
    return proc


def _git_identity(root: Path) -> tuple[str, str]:
    head = _run_git(root, "rev-parse", "HEAD").stdout.strip()
    tree = _run_git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    if not SHA40.fullmatch(head) or not SHA40.fullmatch(tree):
        raise ProjectExecutionError("PROJECT_EXECUTION_GIT_IDENTITY_INVALID")
    return head, tree


def _git_clean(root: Path) -> bool:
    return not _run_git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip()


def _tracked_dirty(root: Path) -> bool:
    unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=root, check=False).returncode != 0
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root, check=False).returncode != 0
    return unstaged or staged


def _status_counts(root: Path) -> tuple[int, int]:
    text = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout
    tracked = 0
    untracked = 0
    for line in text.splitlines():
        if line.startswith("??"):
            untracked += 1
        elif line.strip():
            tracked += 1
    return tracked, untracked


def _strict_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = strict_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProjectExecutionError(code + ":" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ProjectExecutionError(code + ":OBJECT_REQUIRED")
    return value


def _validate_config(config: dict[str, Any], framework_root: Path) -> None:
    schema = _strict_object(framework_root / ".adwf/schemas/config.schema.json", "PROJECT_CONFIG_SCHEMA_INVALID")
    findings = validate(config, schema)
    if findings:
        raise ProjectExecutionError("PROJECT_CONFIG_SCHEMA_MISMATCH")


def load_bound_project_pack(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    """Return a pack binding only when canonical config matches current validated pack truth."""
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    try:
        config = load_effective_config(project, framework)
    except ConsumerProfileError as exc:
        raise ProjectExecutionError(str(exc)) from exc
    _validate_config(config, framework)
    pp = config.get("project_packs") or {}
    if pp.get("materialized") is not True:
        raise ProjectExecutionError("PROJECT_PACK_RUNTIME_BINDING_REQUIRED")
    selected = pp.get("selected")
    selected_digest = pp.get("selected_digest")
    materialized_safety = pp.get("safety")
    if not isinstance(selected, str) or not selected or not isinstance(selected_digest, str) or not SHA256.fullmatch(selected_digest):
        raise ProjectExecutionError("PROJECT_PACK_RUNTIME_BINDING_INVALID")
    current = commands_for_pack(project, framework)
    if current.get("pack") != selected:
        raise ProjectExecutionError("PROJECT_PACK_SELECTED_DETECTION_MISMATCH")
    if current.get("pack_digest") != selected_digest:
        raise ProjectExecutionError("PROJECT_PACK_DIGEST_MISMATCH")
    if current.get("safety") != materialized_safety:
        raise ProjectExecutionError("PROJECT_PACK_SAFETY_MISMATCH")
    safety = current.get("safety") or {}
    if safety.get("monetary_budget_usd") != 0 or safety.get("secrets") != "FORBIDDEN" or safety.get("environment") != "PROCESS_MINIMAL":
        raise ProjectExecutionError("PROJECT_PACK_RUNTIME_SAFETY_INVALID")
    data_access = safety.get("data_access") or {}
    if data_access != {"read_scope": "PROJECT_TREE", "write_scope": "TOOL_OUTPUTS_ONLY"}:
        raise ProjectExecutionError("PROJECT_PACK_DATA_ACCESS_INVALID")
    return {
        "config": config,
        "pack": selected,
        "pack_digest": selected_digest,
        "commands": current.get("commands") or {},
        "preview": current.get("preview") or {},
        "safety": copy.deepcopy(safety),
    }


def _minimal_environment(home: Path, scratch: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in SAFE_INHERITED_ENV:
        value = os.environ.get(name)
        if value and not SECRET_NAME.search(name):
            env[name] = value
    home.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMP": str(scratch),
        "TEMP": str(scratch),
        "TMPDIR": str(scratch),
        "XDG_CACHE_HOME": str(scratch / "xdg-cache"),
        "PIP_CACHE_DIR": str(scratch / "pip-cache"),
        "NPM_CONFIG_CACHE": str(scratch / "npm-cache"),
        "GOCACHE": str(scratch / "go-build-cache"),
        "GOMODCACHE": str(scratch / "go-mod-cache"),
        "CI": "true",
        "ADWF_PROJECT_EXECUTION": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    if any(SECRET_NAME.search(name) for name in env):
        raise ProjectExecutionError("PROJECT_EXECUTION_SECRET_ENV_NAME_PRESENT")
    return env


def _seal_evidence(value: dict[str, Any]) -> dict[str, Any]:
    sealed = copy.deepcopy(value)
    sealed["evidence_sha256"] = _sha256({k: v for k, v in sealed.items() if k != "evidence_sha256"})
    return sealed


def validate_execution_evidence(
    value: dict[str, Any], framework_root: str | Path, *, expected_head: str | None = None, expected_pack_digest: str | None = None,
) -> list[str]:
    root = Path(framework_root).resolve()
    errors: list[str] = []
    try:
        schema = _strict_object(root / ".adwf/schemas/project-execution-evidence.schema.json", "PROJECT_EXECUTION_EVIDENCE_SCHEMA_INVALID")
    except ProjectExecutionError as exc:
        return [str(exc)]
    for item in validate(value, schema):
        errors.append(f"SCHEMA:{item.path}:{item.code}")
    actual = value.get("evidence_sha256")
    expected = _sha256({k: v for k, v in value.items() if k != "evidence_sha256"})
    if actual != expected:
        errors.append("PROJECT_EXECUTION_EVIDENCE_DIGEST_MISMATCH")
    if expected_head is not None and value.get("head_sha") != expected_head:
        errors.append("PROJECT_EXECUTION_EVIDENCE_HEAD_MISMATCH")
    if expected_pack_digest is not None and value.get("pack_digest") != expected_pack_digest:
        errors.append("PROJECT_EXECUTION_EVIDENCE_PACK_DIGEST_MISMATCH")
    return list(dict.fromkeys(errors))


@dataclass
class CommandObservation:
    process: subprocess.CompletedProcess[str]
    safety_status: str
    reason_codes: list[str]
    tracked_mutation_count: int
    untracked_output_count: int


class ProjectExecutionSession:
    """Independent exact-revision local clone for one bounded consumer execution cycle."""

    def __init__(self, project_root: str | Path, framework_root: str | Path, binding: dict[str, Any], *, purpose: str):
        self.project_root = Path(project_root).resolve()
        self.framework_root = Path(framework_root).resolve()
        self.binding = binding
        self.purpose = str(purpose)
        self.execution_id = "PEX-" + uuid.uuid4().hex
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.workspace: Path | None = None
        self._env: dict[str, str] | None = None
        self._head: str | None = None
        self._tree: str | None = None
        self._records: list[dict[str, Any]] = []
        self._reason_codes: list[str] = []
        self._canonical_integrity = "NOT_VERIFIED"
        self.evidence: dict[str, Any] | None = None

    @property
    def head_sha(self) -> str:
        if not self._head:
            raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
        return self._head

    @property
    def tree_sha(self) -> str:
        if not self._tree:
            raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
        return self._tree

    @property
    def environment_names(self) -> list[str]:
        if self._env is None:
            raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
        return sorted(self._env)

    def __enter__(self) -> "ProjectExecutionSession":
        top = Path(_run_git(self.project_root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        if top != self.project_root:
            raise ProjectExecutionError("PROJECT_EXECUTION_ROOT_NOT_GIT_TOPLEVEL")
        if not _git_clean(self.project_root):
            raise ProjectExecutionError("PROJECT_SOURCE_NOT_CLEAN")
        self._head, self._tree = _git_identity(self.project_root)
        self._tmp = tempfile.TemporaryDirectory(prefix="adwf-project-exec-")
        temp_root = Path(self._tmp.name)
        workspace = temp_root / "workspace"
        home = temp_root / "home"
        scratch = temp_root / "scratch"
        self._env = _minimal_environment(home, scratch)
        clone = subprocess.run(
            ["git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", "--no-tags", "--quiet", str(self.project_root), str(workspace)],
            cwd=self.project_root, env=self._env, text=True, capture_output=True, check=False, timeout=120,
        )
        if clone.returncode:
            self._tmp.cleanup(); self._tmp = None
            raise ProjectExecutionError("PROJECT_EXECUTION_CLONE_FAILED")
        self.workspace = workspace.resolve()
        _run_git(self.workspace, "checkout", "--detach", "--quiet", self._head, env=self._env)
        _run_git(self.workspace, "remote", "remove", "origin", env=self._env)
        alternates = self.workspace / ".git/objects/info/alternates"
        if alternates.exists():
            self._tmp.cleanup(); self._tmp = None
            raise ProjectExecutionError("PROJECT_EXECUTION_SHARED_OBJECT_STORE_FORBIDDEN")
        clone_head, clone_tree = _git_identity(self.workspace)
        if clone_head != self._head or clone_tree != self._tree or not _git_clean(self.workspace):
            self._tmp.cleanup(); self._tmp = None
            raise ProjectExecutionError("PROJECT_EXECUTION_EXACT_CLONE_NOT_VERIFIED")
        return self

    def _canonical_ok(self) -> bool:
        if not self._head or not self._tree:
            return False
        try:
            head, tree = _git_identity(self.project_root)
            return head == self._head and tree == self._tree and _git_clean(self.project_root)
        except ProjectExecutionError:
            return False

    def _check_command_binding(self, name: str, command: list[str], *, pack_bound: bool) -> None:
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise ProjectExecutionError("PROJECT_COMMAND_INVALID:" + name)
        if not pack_bound:
            return
        current = self.binding.get("commands", {}).get(name)
        if not isinstance(current, dict) or current.get("available") is not True or current.get("command") != command:
            raise ProjectExecutionError("PROJECT_COMMAND_PACK_BINDING_MISMATCH:" + name)

    def run(
        self, name: str, command: list[str], *, pack_bound: bool = True, cwd: Path | None = None,
        timeout: int = 900, capture_output: bool = True,
    ) -> CommandObservation:
        if self.workspace is None or self._env is None:
            raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
        self._check_command_binding(name, command, pack_bound=pack_bound)
        target = (cwd or self.workspace).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ProjectExecutionError("PROJECT_EXECUTION_CWD_ESCAPE")
        reason_codes: list[str] = []
        safety_reasons: list[str] = []
        try:
            proc = subprocess.run(
                command, cwd=target, env=self._env, text=True, capture_output=capture_output,
                check=False, timeout=timeout,
            )
        except FileNotFoundError:
            proc = subprocess.CompletedProcess(command, 127, "", "")
            reason_codes.append("PROJECT_COMMAND_EXECUTABLE_NOT_FOUND")
        except subprocess.TimeoutExpired:
            proc = subprocess.CompletedProcess(command, 124, "", "")
            reason_codes.append("PROJECT_COMMAND_TIMEOUT")
        tracked_count, untracked_count = _status_counts(self.workspace)
        tracked_dirty = _tracked_dirty(self.workspace)
        head, tree = _git_identity(self.workspace)
        if head != self._head or tree != self._tree:
            safety_reasons.append("PROJECT_EXECUTION_REVISION_DRIFT")
        if tracked_dirty or tracked_count:
            safety_reasons.append("PROJECT_COMMAND_TRACKED_MUTATION")
        if not self._canonical_ok():
            safety_reasons.append("PROJECT_CANONICAL_SOURCE_MUTATED")
        reason_codes.extend(safety_reasons)
        if proc.returncode:
            reason_codes.append("PROJECT_COMMAND_NONZERO_EXIT")
        safety_status = "PASS" if not safety_reasons else "BLOCK"
        self._records.append({
            "name": name,
            "kind": "PACK_COMMAND" if pack_bound else "FRAMEWORK_TOOL",
            "argv_sha256": hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest(),
            "returncode": int(proc.returncode),
            "safety_status": safety_status,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "tracked_mutation_count": tracked_count,
            "untracked_output_count": untracked_count,
        })
        self._reason_codes.extend(reason_codes)
        return CommandObservation(
            process=proc, safety_status=safety_status, reason_codes=list(dict.fromkeys(reason_codes)),
            tracked_mutation_count=tracked_count, untracked_output_count=untracked_count,
        )

    def popen(self, name: str, command: list[str], *, pack_bound: bool = True, cwd: Path | None = None) -> subprocess.Popen[str]:
        if self.workspace is None or self._env is None:
            raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
        self._check_command_binding(name, command, pack_bound=pack_bound)
        target = (cwd or self.workspace).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ProjectExecutionError("PROJECT_EXECUTION_CWD_ESCAPE")
        try:
            return subprocess.Popen(
                command, cwd=target, env=self._env, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise ProjectExecutionError("PROJECT_COMMAND_EXECUTABLE_NOT_FOUND:" + name) from exc

    def record_process(self, name: str, command: list[str], process: subprocess.Popen[str], *, pack_bound: bool = True, expected_termination: bool = False) -> CommandObservation:
        returncode = process.poll()
        if returncode is None:
            raise ProjectExecutionError("PROJECT_PROCESS_STILL_RUNNING:" + name)
        fake = subprocess.CompletedProcess(command, int(returncode), "", "")
        # Do not execute again; reproduce postcondition checks and evidence shape.
        if self.workspace is None:
            raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
        self._check_command_binding(name, command, pack_bound=pack_bound)
        tracked_count, untracked_count = _status_counts(self.workspace)
        reason_codes: list[str] = []
        safety_reasons: list[str] = []
        head, tree = _git_identity(self.workspace)
        if head != self._head or tree != self._tree:
            safety_reasons.append("PROJECT_EXECUTION_REVISION_DRIFT")
        if _tracked_dirty(self.workspace) or tracked_count:
            safety_reasons.append("PROJECT_COMMAND_TRACKED_MUTATION")
        if not self._canonical_ok():
            safety_reasons.append("PROJECT_CANONICAL_SOURCE_MUTATED")
        reason_codes.extend(safety_reasons)
        if returncode and not expected_termination:
            reason_codes.append("PROJECT_COMMAND_NONZERO_EXIT")
        status = "PASS" if not safety_reasons else "BLOCK"
        self._records.append({
            "name": name, "kind": "PACK_COMMAND" if pack_bound else "FRAMEWORK_TOOL",
            "argv_sha256": hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest(),
            "returncode": int(returncode), "safety_status": status,
            "reason_codes": list(dict.fromkeys(reason_codes)), "tracked_mutation_count": tracked_count,
            "untracked_output_count": untracked_count,
        })
        self._reason_codes.extend(reason_codes)
        return CommandObservation(fake, status, list(dict.fromkeys(reason_codes)), tracked_count, untracked_count)

    def _build_evidence(self) -> dict[str, Any]:
        if not self._head or not self._tree:
            raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
        canonical_ok = self._canonical_ok()
        self._canonical_integrity = "PASS" if canonical_ok else "BLOCK"
        if not canonical_ok:
            self._reason_codes.append("PROJECT_CANONICAL_SOURCE_MUTATED")
        evidence = {
            "$schema": ".adwf/schemas/project-execution-evidence.schema.json",
            "schema_version": 1,
            "role": "PROJECT_EXECUTION_EVIDENCE",
            "execution_id": self.execution_id,
            "purpose": self.purpose,
            "head_sha": self._head,
            "tree_sha": self._tree,
            "pack_id": self.binding["pack"],
            "pack_digest": self.binding["pack_digest"],
            "environment_policy": "PROCESS_MINIMAL",
            "environment_names": sorted(self._env or {}),
            "secret_like_inherited": False,
            "declared_network": self.binding["safety"]["network"],
            "network_enforcement": NETWORK_ENFORCEMENT,
            "data_write_policy": "DISPOSABLE_INDEPENDENT_GIT_CLONE",
            "canonical_source_integrity": self._canonical_integrity,
            "commands": copy.deepcopy(self._records),
            "outcome": "PASS" if self._canonical_integrity == "PASS" and not self._reason_codes else "BLOCK",
            "reason_codes": list(dict.fromkeys(self._reason_codes)),
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return _seal_evidence(evidence)

    def _persist_evidence(self) -> dict[str, Any]:
        evidence = self._build_evidence()
        errors = validate_execution_evidence(
            evidence, self.framework_root, expected_head=self._head, expected_pack_digest=self.binding["pack_digest"],
        )
        if errors:
            raise ProjectExecutionError("PROJECT_EXECUTION_EVIDENCE_INVALID:" + errors[0])
        target = self.project_root / ".adwf-runtime/project-execution" / f"{self.execution_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(evidence, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        self.evidence = evidence
        return evidence

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and isinstance(exc, ProjectExecutionError):
            self._reason_codes.append(str(exc).split(":", 1)[0])
        evidence_error: Exception | None = None
        try:
            self._persist_evidence()
        except Exception as persist_exc:  # fail closed after cleanup
            evidence_error = persist_exc
        finally:
            if self._tmp is not None:
                self._tmp.cleanup(); self._tmp = None
                self.workspace = None
        if exc is not None:
            return False
        if evidence_error is not None:
            raise evidence_error
        return False
