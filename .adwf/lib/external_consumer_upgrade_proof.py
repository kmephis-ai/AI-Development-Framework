"""Real external-consumer upgrade proof in a disposable tracked-file copy.

UPGRADE-003 never mutates the supplied external consumer checkout. The source
checkout is Git identity truth; only its tracked regular-file bytes are copied
into a private disposable root where existing lifecycle primitives execute.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from .consumer_profile import apply_consumer_profile, load_consumer_profile
from .consumer_upgrade import build_upgrade_compatibility, plan_consumer_upgrade
from .consumer_upgrade_transaction import apply_upgrade, rollback_upgrade
from .contracts import validate
from .managed_surface import plan_adoption
from .managed_surface_transaction import apply_adoption
from .strict_json import loads as strict_loads

REPORT_SCHEMA = ".adwf/schemas/external-consumer-upgrade-proof.schema.json"


class ExternalConsumerUpgradeProofError(ValueError):
    """Deterministic fail-closed external-consumer proof blocker."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _object_sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False, timeout=120)
    if proc.returncode:
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_GIT_FAILED:" + str(args[0]))
    return proc.stdout.strip()


def _require_sha(value: str, code: str) -> str:
    text = str(value or "")
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise ExternalConsumerUpgradeProofError(code)
    return text


def _git_identity(root: Path, expected_sha: str, expected_tree: str, label: str) -> tuple[str, str]:
    if not root.is_dir():
        raise ExternalConsumerUpgradeProofError(label + "_ROOT_MISSING")
    expected_sha = _require_sha(expected_sha, label + "_SHA_INVALID")
    expected_tree = _require_sha(expected_tree, label + "_TREE_INVALID")
    try:
        top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    except Exception as exc:
        raise ExternalConsumerUpgradeProofError(label + "_NOT_GIT") from exc
    if top != root:
        raise ExternalConsumerUpgradeProofError(label + "_NOT_GIT_TOPLEVEL")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ExternalConsumerUpgradeProofError(label + "_DIRTY")
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    if head != expected_sha:
        raise ExternalConsumerUpgradeProofError(label + "_SHA_MISMATCH")
    if tree != expected_tree:
        raise ExternalConsumerUpgradeProofError(label + "_TREE_MISMATCH")
    return head, tree


def _tracked_regular_files(root: Path) -> dict[str, str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=False, timeout=120,
    )
    if proc.returncode:
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_TRACKED_LIST_FAILED")
    result: dict[str, str] = {}
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            rel = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_TRACKED_PATH_UTF8_REQUIRED") from exc
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_TRACKED_PATH_UNSAFE")
        path = root.joinpath(*pure.parts)
        if path.is_symlink():
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_TRACKED_SYMLINK_FORBIDDEN:" + rel)
        if not path.is_file():
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_TRACKED_NON_FILE:" + rel)
        result[rel] = _file_sha(path)
    if not result:
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_TRACKED_FILES_REQUIRED")
    return dict(sorted(result.items()))


def _preservation_sha(files: dict[str, str]) -> str:
    return _object_sha({"files": files})


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _assert_disposable_isolated(target: Path, *protected_roots: Path) -> None:
    resolved = target.resolve(strict=False)
    for root in protected_roots:
        protected = root.resolve()
        if resolved == protected or _is_within(resolved, protected) or _is_within(protected, resolved):
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_DISPOSABLE_NOT_ISOLATED")


def _copy_tracked_files(source: Path, target: Path, baseline: dict[str, str]) -> None:
    if target.exists():
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_DISPOSABLE_ROOT_EXISTS")
    target.mkdir(parents=True, mode=0o700)
    for rel, digest in baseline.items():
        pure = PurePosixPath(rel)
        src = source.joinpath(*pure.parts)
        dst = target.joinpath(*pure.parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink() or not src.is_file() or _file_sha(src) != digest:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_SOURCE_CHANGED_DURING_COPY:" + rel)
        shutil.copyfile(src, dst)
        if _file_sha(dst) != digest:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_COPY_DIGEST_MISMATCH:" + rel)


def _checkpoint(consumer: Path, baseline: dict[str, str], label: str) -> dict[str, Any]:
    current: dict[str, str] = {}
    for rel, expected in baseline.items():
        path = consumer.joinpath(*PurePosixPath(rel).parts)
        if path.is_symlink() or not path.is_file():
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_PRESERVATION_MISSING:" + label + ":" + rel)
        digest = _file_sha(path)
        if digest != expected:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_PRESERVATION_MISMATCH:" + label + ":" + rel)
        current[rel] = digest
    return {"label": label, "file_count": len(current), "preservation_sha256": _preservation_sha(current)}


def seal_external_consumer_upgrade_proof(report: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(report)
    value["report_sha256"] = _object_sha({k: v for k, v in value.items() if k != "report_sha256"})
    return value


def validate_external_consumer_upgrade_proof(value: dict[str, Any], framework_root: str | Path) -> list[str]:
    root = Path(framework_root).resolve()
    errors: list[str] = []
    try:
        schema = strict_loads((root / REPORT_SCHEMA).read_text(encoding="utf-8"))
    except Exception as exc:
        return ["EXTERNAL_PROOF_SCHEMA_INVALID:" + type(exc).__name__]
    for item in validate(value, schema):
        errors.append(f"SCHEMA:{item.path}:{item.code}")
    expected = _object_sha({k: v for k, v in value.items() if k != "report_sha256"})
    if value.get("report_sha256") != expected:
        errors.append("EXTERNAL_PROOF_REPORT_DIGEST_MISMATCH")
    if value.get("status") != "PASS":
        errors.append("EXTERNAL_PROOF_STATUS_NOT_PASS")
    transitions = value.get("transitions") or {}
    if transitions != {"adoption": "COMMITTED", "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"}:
        errors.append("EXTERNAL_PROOF_TRANSITION_MISMATCH")
    checkpoints = value.get("preservation_checkpoints") or []
    baseline_sha = value.get("preservation_set_sha256")
    if len(checkpoints) != 4 or any(item.get("preservation_sha256") != baseline_sha for item in checkpoints):
        errors.append("EXTERNAL_PROOF_PRESERVATION_BINDING_MISMATCH")
    source = value.get("framework") or {}
    if source.get("source_sha") == source.get("target_sha"):
        errors.append("EXTERNAL_PROOF_FRAMEWORK_REVISIONS_MUST_DIFFER")
    return errors


def run_external_consumer_upgrade_proof(
    consumer_source_root: str | Path,
    source_framework_root: str | Path,
    target_framework_root: str | Path,
    *,
    consumer_repository: str,
    consumer_sha: str,
    consumer_tree: str,
    source_sha: str,
    source_tree: str,
    target_sha: str,
    target_tree: str,
    product_name: str,
    default_branch: str,
    repository_visibility: str,
    provider_run_id: str,
    provider_check: str = "adwf/external-consumer-upgrade-proof",
) -> dict[str, Any]:
    """Execute A→B→A→B against a disposable copy of exact external tracked bytes."""
    external = Path(consumer_source_root).resolve()
    source = Path(source_framework_root).resolve()
    target = Path(target_framework_root).resolve()
    repo = str(consumer_repository or "").strip()
    if "/" not in repo or any(ch in repo for ch in "\r\n\x00"):
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_REPOSITORY_INVALID")
    _git_identity(external, consumer_sha, consumer_tree, "EXTERNAL_PROOF_CONSUMER")
    _git_identity(source, source_sha, source_tree, "EXTERNAL_PROOF_SOURCE_FRAMEWORK")
    _git_identity(target, target_sha, target_tree, "EXTERNAL_PROOF_TARGET_FRAMEWORK")
    if source_sha == target_sha:
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_FRAMEWORK_REVISIONS_MUST_DIFFER")

    baseline = _tracked_regular_files(external)
    baseline_sha = _preservation_sha(baseline)
    with tempfile.TemporaryDirectory(prefix="adwf-external-upgrade-proof-") as tmp:
        consumer = Path(tmp) / "consumer"
        _assert_disposable_isolated(consumer, external, source, target)
        _copy_tracked_files(external, consumer, baseline)

        adoption_plan = plan_adoption(source, consumer, source_revision=source_sha)
        if adoption_plan.get("status") != "READY":
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ADOPTION_NOT_READY:" + ";".join(adoption_plan.get("blockers") or []))
        adoption = apply_adoption(source, consumer, adoption_plan)
        if adoption.get("status") != "COMMITTED" or not isinstance(adoption.get("snapshot"), dict):
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ADOPTION_FAILED")
        checkpoint_adoption = _checkpoint(consumer, baseline, "ADOPTION_A")

        profile_result = apply_consumer_profile(
            consumer, source, product_name=product_name, default_branch=default_branch,
            repository_visibility=repository_visibility,
        )
        if profile_result.get("status") not in {"APPLIED", "ALREADY_MATERIALIZED"}:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_PROFILE_FAILED:" + str(profile_result.get("reason") or "UNKNOWN"))
        profile_a = load_consumer_profile(consumer, source, required=True)
        if profile_a is None or profile_a.get("project_packs", {}).get("selected") != "apps-script":
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_APPS_SCRIPT_PACK_REQUIRED")

        snapshot_a = adoption["snapshot"]
        compatibility = build_upgrade_compatibility(
            source, target, consumer, source_revision=source_sha, target_revision=target_sha, snapshot=snapshot_a,
        )
        plan = plan_consumer_upgrade(
            source, target, consumer, source_revision=source_sha, target_revision=target_sha, snapshot=snapshot_a,
        )
        if compatibility.get("status") != "PASS" or plan.get("status") != "READY":
            codes = [str(item.get("code")) for item in compatibility.get("findings") or []]
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_UPGRADE_NOT_READY:" + ",".join(codes))

        upgraded = apply_upgrade(source, target, consumer, compatibility, plan, snapshot_a)
        if upgraded.get("status") != "COMMITTED":
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_UPGRADE_B_FAILED:" + str(upgraded.get("status")))
        checkpoint_b = _checkpoint(consumer, baseline, "UPGRADE_B")
        profile_b = load_consumer_profile(consumer, target, required=True)
        if profile_b is None:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_TARGET_PROFILE_MISSING")

        transaction_id = str(upgraded.get("transaction_id") or "")
        rolled_back = rollback_upgrade(source, target, consumer, transaction_id)
        if rolled_back.get("status") != "ROLLED_BACK":
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ROLLBACK_A_FAILED:" + str(rolled_back.get("status")))
        checkpoint_rollback = _checkpoint(consumer, baseline, "ROLLBACK_A")
        restored_profile = load_consumer_profile(consumer, source, required=True)
        if restored_profile is None or restored_profile.get("profile_sha256") != profile_a.get("profile_sha256"):
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ROLLBACK_PROFILE_MISMATCH")

        retried = apply_upgrade(source, target, consumer, compatibility, plan, snapshot_a)
        if retried.get("status") != "COMMITTED":
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_RETRY_B_FAILED:" + str(retried.get("status")))
        checkpoint_final = _checkpoint(consumer, baseline, "RETRY_B")
        final_profile = load_consumer_profile(consumer, target, required=True)
        if final_profile is None or final_profile.get("profile_sha256") != profile_b.get("profile_sha256"):
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_FINAL_PROFILE_MISMATCH")

        # Source checkouts must remain exact/clean after every mutable lifecycle operation.
        _git_identity(external, consumer_sha, consumer_tree, "EXTERNAL_PROOF_CONSUMER")
        _git_identity(source, source_sha, source_tree, "EXTERNAL_PROOF_SOURCE_FRAMEWORK")
        _git_identity(target, target_sha, target_tree, "EXTERNAL_PROOF_TARGET_FRAMEWORK")

        report = seal_external_consumer_upgrade_proof({
            "$schema": REPORT_SCHEMA,
            "schema_version": 1,
            "role": "EXTERNAL_CONSUMER_UPGRADE_PROOF",
            "status": "PASS",
            "consumer": {"repository": repo, "sha": consumer_sha, "tree": consumer_tree, "tracked_regular_file_count": len(baseline)},
            "framework": {"source_sha": source_sha, "source_tree": source_tree, "target_sha": target_sha, "target_tree": target_tree},
            "pack": {"id": "apps-script", "source_digest": profile_a["project_pack_digest"], "target_digest": profile_b["project_pack_digest"]},
            "profile": {"source_sha256": profile_a["profile_sha256"], "target_sha256": profile_b["profile_sha256"]},
            "upgrade": {
                "compatibility_sha256": compatibility["compatibility_sha256"],
                "plan_sha256": plan["plan_sha256"],
                "transaction_id": transaction_id,
                "retry_transaction_id": str(retried.get("transaction_id") or ""),
            },
            "transitions": {"adoption": "COMMITTED", "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"},
            "preservation_set_sha256": baseline_sha,
            "preservation_checkpoints": [checkpoint_adoption, checkpoint_b, checkpoint_rollback, checkpoint_final],
            "provider": {"run_id": str(provider_run_id), "check": str(provider_check)},
            "external_source_unchanged": True,
            "write_back_performed": False,
        })
        errors = validate_external_consumer_upgrade_proof(report, target)
        if errors:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_REPORT_INVALID:" + errors[0])
        return report
