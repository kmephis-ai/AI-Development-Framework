"""Durable provider-resolved certification for Capability Truth LIVE_VERIFIED.

Operational Evidence Graph entries keep their normal freshness semantics.  A
capability live certification is a separate *durable certification projection*
of an immutable provider proof: it is strict-schema validated, self-sealed and
must be re-read from the provider by the trusted default-branch controller when
it is introduced or changed.  Local/offline validation never treats a formatted
URL/string as evidence and never requires network access.
"""
from __future__ import annotations

from typing import Any
import copy
import hashlib
import json
import re

from .contracts import validate

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CERT_REF = re.compile(r"^certification:([A-Z0-9_-]+)$")
UPGRADE_CAPABILITIES = {
    "CONSUMER_FRAMEWORK_UPGRADE_PLANNING",
    "CONSUMER_FRAMEWORK_UPGRADE_TRANSACTION",
}
UPGRADE_CLASS = "REAL_EXTERNAL_CONSUMER_UPGRADE"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _without(value: dict[str, Any], *names: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in names}


def seal_certification(certification: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(certification)
    value["certification_sha256"] = _digest(_without(value, "certification_sha256"))
    return value


def seal_registry(registry: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(registry)
    value["certifications"] = [seal_certification(item) for item in value.get("certifications", [])]
    value["registry_sha256"] = _digest(_without(value, "registry_sha256"))
    return value


def validate_certification_registry(
    registry: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
    known_capability_ids: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if schema is not None:
        errors.extend(f"LIVE_CERT_SCHEMA:{item.path}:{item.code}" for item in validate(registry, schema))
    if registry.get("schema_version") != 1 or registry.get("role") != "CANONICAL_CAPABILITY_LIVE_EVIDENCE_CERTIFICATIONS":
        errors.append("LIVE_CERT_REGISTRY_IDENTITY_INVALID")
    seen: set[str] = set()
    for cert in registry.get("certifications") or []:
        cid = str(cert.get("id") or "")
        if not cid or cid in seen:
            errors.append("LIVE_CERT_DUPLICATE_OR_MISSING_ID:" + (cid or "?"))
        seen.add(cid)
        if cert.get("certification_sha256") != _digest(_without(cert, "certification_sha256")):
            errors.append("LIVE_CERT_DIGEST_MISMATCH:" + (cid or "?"))
        caps = [str(item) for item in (cert.get("capability_ids") or [])]
        if len(caps) != len(set(caps)) or not caps:
            errors.append("LIVE_CERT_CAPABILITY_BINDING_INVALID:" + (cid or "?"))
        if known_capability_ids is not None:
            unknown = sorted(set(caps) - known_capability_ids)
            if unknown:
                errors.append("LIVE_CERT_CAPABILITY_UNKNOWN:" + (cid or "?") + ":" + ",".join(unknown))
        if cert.get("evidence_class") != UPGRADE_CLASS:
            errors.append("LIVE_CERT_EVIDENCE_CLASS_UNSUPPORTED:" + (cid or "?"))
        if set(caps) != UPGRADE_CAPABILITIES:
            errors.append("LIVE_CERT_UPGRADE_SCOPE_INVALID:" + (cid or "?"))
        subject = cert.get("subject") if isinstance(cert.get("subject"), dict) else {}
        framework = cert.get("framework") if isinstance(cert.get("framework"), dict) else {}
        consumer = cert.get("consumer") if isinstance(cert.get("consumer"), dict) else {}
        provider = cert.get("provider") if isinstance(cert.get("provider"), dict) else {}
        for label, value in (
            ("SUBJECT_SHA", subject.get("sha")), ("SUBJECT_TREE", subject.get("tree")),
            ("SOURCE_SHA", framework.get("source_sha")), ("SOURCE_TREE", framework.get("source_tree")),
            ("TARGET_SHA", framework.get("target_sha")), ("TARGET_TREE", framework.get("target_tree")),
            ("CONSUMER_SHA", consumer.get("sha")), ("CONSUMER_TREE", consumer.get("tree")),
            ("WORKFLOW_HEAD_SHA", provider.get("workflow_run_head_sha")),
        ):
            if SHA40.fullmatch(str(value or "")) is None:
                errors.append(f"LIVE_CERT_{label}_INVALID:{cid or '?'}")
        if subject.get("sha") != framework.get("target_sha") or subject.get("tree") != framework.get("target_tree"):
            errors.append("LIVE_CERT_TARGET_SUBJECT_MISMATCH:" + (cid or "?"))
        if framework.get("source_sha") == framework.get("target_sha"):
            errors.append("LIVE_CERT_SOURCE_TARGET_NOT_DISTINCT:" + (cid or "?"))
        if SHA256.fullmatch(str(cert.get("report_sha256") or "")) is None:
            errors.append("LIVE_CERT_REPORT_DIGEST_INVALID:" + (cid or "?"))
        if provider.get("provider") != "github" or provider.get("repository") != "kmephis-ai/AI-Development-Framework":
            errors.append("LIVE_CERT_PROVIDER_SCOPE_INVALID:" + (cid or "?"))
        if provider.get("workflow_name") != "ADWF UPGRADE-003 Post-Merge External Consumer Proof":
            errors.append("LIVE_CERT_WORKFLOW_IDENTITY_INVALID:" + (cid or "?"))
        if provider.get("check_name") != "adwf/external-consumer-upgrade-proof" or provider.get("check_app_slug") != "github-actions" or provider.get("check_app_id") != 15368:
            errors.append("LIVE_CERT_CHECK_IDENTITY_INVALID:" + (cid or "?"))
        if not isinstance(provider.get("workflow_run_id"), int) or isinstance(provider.get("workflow_run_id"), bool) or provider.get("workflow_run_id", 0) < 1:
            errors.append("LIVE_CERT_WORKFLOW_RUN_ID_INVALID:" + (cid or "?"))
        if not isinstance(provider.get("check_run_id"), int) or isinstance(provider.get("check_run_id"), bool) or provider.get("check_run_id", 0) < 1:
            errors.append("LIVE_CERT_CHECK_RUN_ID_INVALID:" + (cid or "?"))
        expected_transitions = {"adoption": "COMMITTED", "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"}
        if cert.get("transitions") != expected_transitions:
            errors.append("LIVE_CERT_TRANSITIONS_INVALID:" + (cid or "?"))
        if cert.get("external_source_unchanged") is not True:
            errors.append("LIVE_CERT_EXTERNAL_SOURCE_UNCHANGED_REQUIRED:" + (cid or "?"))
        if cert.get("write_back_performed") is not False:
            errors.append("LIVE_CERT_WRITE_BACK_FORBIDDEN:" + (cid or "?"))
    if registry.get("registry_sha256") != _digest(_without(registry, "registry_sha256")):
        errors.append("LIVE_CERT_REGISTRY_DIGEST_MISMATCH")
    return list(dict.fromkeys(errors))


def resolve_capability_live_evidence(trace: dict[str, Any], registry: dict[str, Any], *, schema: dict[str, Any] | None = None) -> list[str]:
    capabilities = trace.get("capabilities") or []
    known = {str(item.get("id") or "") for item in capabilities if item.get("id")}
    errors = validate_certification_registry(registry, schema=schema, known_capability_ids=known)
    certs: dict[str, dict[str, Any]] = {}
    for item in registry.get("certifications") or []:
        cid = str(item.get("id") or "")
        if cid and cid not in certs:
            certs[cid] = item
    referenced: set[str] = set()
    for cap in capabilities:
        capability_id = str(cap.get("id") or "")
        refs = cap.get("live_evidence") or []
        if cap.get("status") != "LIVE_VERIFIED":
            continue
        if not refs:
            errors.append("CAPABILITY_LIVE_CERTIFICATION_MISSING:" + capability_id)
        for ref in refs:
            match = CERT_REF.fullmatch(str(ref))
            if match is None:
                errors.append("CAPABILITY_LIVE_CERTIFICATION_REF_INVALID:" + capability_id + ":" + str(ref))
                continue
            cert_id = match.group(1); referenced.add(cert_id)
            cert = certs.get(cert_id)
            if cert is None:
                errors.append("CAPABILITY_LIVE_CERTIFICATION_REF_MISSING:" + capability_id + ":" + cert_id)
                continue
            if capability_id not in set(cert.get("capability_ids") or []):
                errors.append("CAPABILITY_LIVE_CERTIFICATION_SCOPE_MISMATCH:" + capability_id + ":" + cert_id)
            if cert.get("evidence_class") != UPGRADE_CLASS:
                errors.append("CAPABILITY_LIVE_CERTIFICATION_CLASS_MISMATCH:" + capability_id + ":" + cert_id)
    for cert_id, cert in certs.items():
        if cert_id not in referenced:
            errors.append("LIVE_CERT_UNREFERENCED:" + cert_id)
        for capability_id in cert.get("capability_ids") or []:
            cap = next((item for item in capabilities if item.get("id") == capability_id), None)
            ref = "certification:" + cert_id
            if cap is None or cap.get("status") != "LIVE_VERIFIED" or ref not in (cap.get("live_evidence") or []):
                errors.append("LIVE_CERT_DECLARED_SCOPE_NOT_ACTIVE:" + cert_id + ":" + str(capability_id))
    return list(dict.fromkeys(errors))


def verify_provider_certification(client: Any, certification: dict[str, Any]) -> dict[str, Any]:
    """Fresh provider readback for a durable certification.

    Called only from authenticated provider context.  The local validator never
    calls this function, so offline and Windows self-tests remain deterministic.
    """
    reasons: list[str] = []
    provider = certification.get("provider") if isinstance(certification.get("provider"), dict) else {}
    subject = certification.get("subject") if isinstance(certification.get("subject"), dict) else {}
    framework = certification.get("framework") if isinstance(certification.get("framework"), dict) else {}
    consumer = certification.get("consumer") if isinstance(certification.get("consumer"), dict) else {}
    repo = str(provider.get("repository") or "")
    if repo != getattr(client, "repo", None):
        reasons.append("LIVE_CERT_PROVIDER_REPOSITORY_MISMATCH")
        return {"verified": False, "reason_codes": reasons}
    try:
        run = client.get(f"/repos/{repo}/actions/runs/{int(provider.get('workflow_run_id'))}")
        check = client.get(f"/repos/{repo}/check-runs/{int(provider.get('check_run_id'))}")
        target_commit = client.get(f"/repos/{repo}/git/commits/{framework.get('target_sha')}")
        source_commit = client.get(f"/repos/{repo}/git/commits/{framework.get('source_sha')}")
        from .github_provider import GitHubClient
        consumer_client = GitHubClient(str(consumer.get("repository") or ""), client.token, transport=client.transport, api_base=client.api_base)
        consumer_commit = consumer_client.get(f"/repos/{consumer_client.repo}/git/commits/{consumer.get('sha')}")
    except Exception as exc:
        return {"verified": False, "reason_codes": ["LIVE_CERT_PROVIDER_READBACK_FAILED:" + type(exc).__name__]}
    if run.get("id") != provider.get("workflow_run_id") or run.get("name") != provider.get("workflow_name"):
        reasons.append("LIVE_CERT_PROVIDER_WORKFLOW_MISMATCH")
    if run.get("head_sha") != provider.get("workflow_run_head_sha") or run.get("event") != "push" or run.get("status") != "completed" or run.get("conclusion") != "success":
        reasons.append("LIVE_CERT_PROVIDER_WORKFLOW_NOT_SUCCESS")
    if str((run.get("repository") or {}).get("full_name") or "") != repo:
        reasons.append("LIVE_CERT_PROVIDER_WORKFLOW_REPOSITORY_MISMATCH")
    app = check.get("app") if isinstance(check.get("app"), dict) else {}
    if check.get("id") != provider.get("check_run_id") or check.get("name") != provider.get("check_name") or check.get("head_sha") != subject.get("sha"):
        reasons.append("LIVE_CERT_PROVIDER_CHECK_MISMATCH")
    if check.get("status") != "completed" or check.get("conclusion") != "success" or app.get("slug") != provider.get("check_app_slug") or app.get("id") != provider.get("check_app_id"):
        reasons.append("LIVE_CERT_PROVIDER_CHECK_NOT_TRUSTED_SUCCESS")
    if str((target_commit.get("tree") or {}).get("sha") or "") != framework.get("target_tree") or framework.get("target_sha") != subject.get("sha") or framework.get("target_tree") != subject.get("tree"):
        reasons.append("LIVE_CERT_PROVIDER_TARGET_TREE_MISMATCH")
    if str((source_commit.get("tree") or {}).get("sha") or "") != framework.get("source_tree"):
        reasons.append("LIVE_CERT_PROVIDER_SOURCE_TREE_MISMATCH")
    if str((consumer_commit.get("tree") or {}).get("sha") or "") != consumer.get("tree"):
        reasons.append("LIVE_CERT_PROVIDER_CONSUMER_TREE_MISMATCH")
    output = check.get("output") if isinstance(check.get("output"), dict) else {}
    expected_text = (
        f"consumer={consumer.get('sha')} tree={consumer.get('tree')}\n"
        f"source={framework.get('source_sha')} target={framework.get('target_sha')}\n"
        f"report_sha256={certification.get('report_sha256')}"
    )
    if str(output.get("text") or "").strip() != expected_text:
        reasons.append("LIVE_CERT_PROVIDER_CHECK_OUTPUT_MISMATCH")
    return {
        "verified": not reasons,
        "reason_codes": list(dict.fromkeys(reasons)),
        "workflow_run_id": run.get("id"),
        "check_run_id": check.get("id"),
        "subject_sha": subject.get("sha"),
    }
