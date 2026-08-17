"""Fail-closed routing between ADWF self-host CI and installed-consumer native gates."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import re
import subprocess
import time

from .consumer_profile import PROFILE_REL, ConsumerProfileError, load_consumer_profile
from .consumer_installation import RECORD_REL, ConsumerInstallationError, load_record
from .consumer_operational import BINDING_REL, ConsumerOperationalError, resolve_operational_context
from .consumer_gates import GATES_REL, ConsumerGateError, load_binding as load_gate_binding, resolve_provider_phase

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SELF_HOST_FILES = ("MANIFEST.json", "SHA256SUMS.txt", ".adwf/config.json")
CONSUMER_FILES = (PROFILE_REL, RECORD_REL, BINDING_REL, GATES_REL)


class ConsumerCIRouteError(ValueError):
    """Deterministic routing failure; callers must stop CI."""


def _git_exists(root: Path, sha: str, rel: str) -> bool:
    if SHA40.fullmatch(sha or "") is None:
        raise ConsumerCIRouteError("CONSUMER_CI_ANCHOR_SHA_INVALID")
    p = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{sha}:{rel}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return p.returncode == 0


def classify_anchor(project_root: str | Path, anchor_sha: str) -> str:
    """Classify trusted predecessor bytes, not candidate marker claims."""
    root = Path(project_root).resolve()
    self_flags = [_git_exists(root, anchor_sha, rel) for rel in SELF_HOST_FILES]
    consumer_flags = [_git_exists(root, anchor_sha, rel) for rel in CONSUMER_FILES]
    if all(self_flags) and not any(consumer_flags):
        return "SELF_HOST_CANONICAL"
    if not any(self_flags[:2]) and all(consumer_flags):
        return "CONSUMER_NATIVE"
    if not any(self_flags) and not any(consumer_flags):
        return "UNMANAGED_PREINSTALL"
    raise ConsumerCIRouteError("CONSUMER_CI_ANCHOR_AMBIGUOUS")


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_managed_bytes(project: Path, record: dict[str, Any]) -> int:
    verified = 0
    for entry in record.get("managed_surface", {}).get("entries") or []:
        if entry.get("managed_by_adwf") is not True:
            continue
        rel = str(entry.get("path") or "")
        path = project / rel
        expected = str(entry.get("installed_sha256") or "")
        if path.is_symlink() or not path.is_file() or _file_sha(path) != expected:
            raise ConsumerCIRouteError("CONSUMER_CI_MANAGED_BYTES_MISMATCH:" + rel)
        verified += 1
    if verified == 0:
        raise ConsumerCIRouteError("CONSUMER_CI_MANAGED_SURFACE_EMPTY")
    return verified


def classify_current(project_root: str | Path, framework_root: str | Path, *, expected_repository: str | None = None) -> dict[str, Any]:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    self_flags = [(project / rel).is_file() and not (project / rel).is_symlink() for rel in SELF_HOST_FILES]
    consumer_flags = [(project / rel).is_file() and not (project / rel).is_symlink() for rel in CONSUMER_FILES]
    if all(self_flags) and not any(consumer_flags):
        return {"mode": "SELF_HOST_CANONICAL", "verified_managed_files": 0}
    if any(self_flags[:2]):
        raise ConsumerCIRouteError("CONSUMER_CI_CURRENT_AMBIGUOUS")
    if not all(consumer_flags):
        raise ConsumerCIRouteError("CONSUMER_CI_CONSUMER_BINDINGS_INCOMPLETE")
    try:
        profile = load_consumer_profile(project, framework, required=True)
        record = load_record(project, framework)
        operational = resolve_operational_context(project, framework)
        gates = load_gate_binding(project, framework)
    except (ConsumerProfileError, ConsumerInstallationError, ConsumerOperationalError, ConsumerGateError, OSError) as exc:
        raise ConsumerCIRouteError("CONSUMER_CI_BINDING_INVALID:" + str(exc)) from exc
    assert profile is not None
    repository = str(record.get("consumer", {}).get("repository") or "")
    if expected_repository is not None and repository != expected_repository:
        raise ConsumerCIRouteError("CONSUMER_CI_REPOSITORY_MISMATCH")
    if operational.get("mode") != "CONSUMER_NATIVE" or operational.get("consumer_repository") != repository:
        raise ConsumerCIRouteError("CONSUMER_CI_OPERATIONAL_MODE_INVALID")
    if gates.get("consumer_repository") != repository:
        raise ConsumerCIRouteError("CONSUMER_CI_GATE_REPOSITORY_MISMATCH")
    verified = _validate_managed_bytes(project, record)
    return {
        "mode": "CONSUMER_NATIVE",
        "consumer_repository": repository,
        "verified_managed_files": verified,
        "mutation_authority": "NONE_ROUTING_IS_EVIDENCE_ONLY",
    }


def resolve_route(
    project_root: str | Path,
    framework_root: str | Path,
    *,
    phase: str,
    subject_sha: str,
    expected_repository: str,
    anchor_sha: str | None = None,
) -> dict[str, Any]:
    if phase not in {"pr", "main"}:
        raise ConsumerCIRouteError("CONSUMER_CI_PHASE_INVALID")
    if SHA40.fullmatch(subject_sha or "") is None:
        raise ConsumerCIRouteError("CONSUMER_CI_SUBJECT_SHA_INVALID")
    current = classify_current(project_root, framework_root, expected_repository=expected_repository)
    anchor_mode = None
    if anchor_sha:
        anchor_mode = classify_anchor(project_root, anchor_sha)
        allowed = {
            "SELF_HOST_CANONICAL": {"SELF_HOST_CANONICAL"},
            "CONSUMER_NATIVE": {"CONSUMER_NATIVE"},
            "UNMANAGED_PREINSTALL": {"CONSUMER_NATIVE"},
        }[anchor_mode]
        if current["mode"] not in allowed:
            raise ConsumerCIRouteError(f"CONSUMER_CI_TRANSITION_FORBIDDEN:{anchor_mode}->{current['mode']}")
    return {
        **current,
        "phase": phase,
        "subject_sha": subject_sha,
        "anchor_sha": anchor_sha,
        "anchor_mode": anchor_mode,
    }


def delegate_native_phase(
    project_root: str | Path,
    framework_root: str | Path,
    client: Any,
    *,
    phase: str,
    subject_sha: str,
) -> dict[str, Any]:
    result = resolve_provider_phase(project_root, framework_root, client, subject_sha=subject_sha, phase=phase)
    if result.get("status") != "VERIFIED":
        raise ConsumerCIRouteError("CONSUMER_CI_NATIVE_GATE_NOT_VERIFIED:" + ",".join(result.get("failures") or ["UNKNOWN"]))
    return result


def wait_for_native_phase(
    project_root: str | Path,
    framework_root: str | Path,
    client: Any,
    *,
    phase: str,
    subject_sha: str,
    attempts: int = 30,
    interval_seconds: int = 10,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Wait a bounded amount of time for exact native provider evidence.

    Evidence semantics never weaken: every attempt still requires the same exact
    subject SHA, unique check/app identity, completed status and success
    conclusion.  Exhausting the bounded window remains a hard block.
    """
    bounded_attempts = max(1, min(int(attempts), 30))
    bounded_interval = max(0, min(int(interval_seconds), 30))
    last: ConsumerCIRouteError | None = None
    for index in range(bounded_attempts):
        try:
            return delegate_native_phase(
                project_root, framework_root, client, phase=phase, subject_sha=subject_sha
            )
        except ConsumerCIRouteError as exc:
            last = exc
            if index + 1 < bounded_attempts and bounded_interval:
                sleep(bounded_interval)
    assert last is not None
    raise last
