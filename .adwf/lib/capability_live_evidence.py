"""Durable provider-resolved certification for Capability Truth LIVE_VERIFIED.

Operational Evidence Graph entries keep their normal freshness semantics. A
capability live certification is a separate durable projection of immutable
provider proof. Candidate code cannot self-authorize a new evidence class:
trusted PR verification loads this module/schema from the exact protected BASE.
"""
from __future__ import annotations

from typing import Any
import base64
import copy
import hashlib
import json
import re
from urllib.parse import quote

from .contracts import validate
from .strict_json import loads as strict_loads

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CERT_REF = re.compile(r"^certification:([A-Z0-9_-]+)$")
UPGRADE_CAPABILITIES = {
    "CONSUMER_FRAMEWORK_UPGRADE_PLANNING",
    "CONSUMER_FRAMEWORK_UPGRADE_TRANSACTION",
}
UPGRADE_CLASS = "REAL_EXTERNAL_CONSUMER_UPGRADE"
SESSION_CAPABILITIES = {"SESSION_CONTINUITY"}
SESSION_CLASS = "SESSION_CONTINUITY_HANDOVER"
SUPPORTED_CLASS_CAPABILITIES = {
    UPGRADE_CLASS: UPGRADE_CAPABILITIES,
    SESSION_CLASS: SESSION_CAPABILITIES,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text_digest(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


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


def _sha40(value: Any) -> bool:
    return SHA40.fullmatch(str(value or "")) is not None


def _sha256(value: Any) -> bool:
    return SHA256.fullmatch(str(value or "")) is not None


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_upgrade_certification(cert: dict[str, Any], cid: str) -> list[str]:
    errors: list[str] = []
    caps = {str(item) for item in (cert.get("capability_ids") or [])}
    if caps != UPGRADE_CAPABILITIES:
        errors.append("LIVE_CERT_UPGRADE_SCOPE_INVALID:" + cid)
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
        if not _sha40(value):
            errors.append(f"LIVE_CERT_{label}_INVALID:{cid}")
    if subject.get("sha") != framework.get("target_sha") or subject.get("tree") != framework.get("target_tree"):
        errors.append("LIVE_CERT_TARGET_SUBJECT_MISMATCH:" + cid)
    if framework.get("source_sha") == framework.get("target_sha"):
        errors.append("LIVE_CERT_SOURCE_TARGET_NOT_DISTINCT:" + cid)
    if not _sha256(cert.get("report_sha256")):
        errors.append("LIVE_CERT_REPORT_DIGEST_INVALID:" + cid)
    if provider.get("provider") != "github" or provider.get("repository") != "kmephis-ai/AI-Development-Framework":
        errors.append("LIVE_CERT_PROVIDER_SCOPE_INVALID:" + cid)
    if provider.get("workflow_name") != "ADWF UPGRADE-003 Post-Merge External Consumer Proof":
        errors.append("LIVE_CERT_WORKFLOW_IDENTITY_INVALID:" + cid)
    if provider.get("check_name") != "adwf/external-consumer-upgrade-proof" or provider.get("check_app_slug") != "github-actions" or provider.get("check_app_id") != 15368:
        errors.append("LIVE_CERT_CHECK_IDENTITY_INVALID:" + cid)
    if not _positive_int(provider.get("workflow_run_id")):
        errors.append("LIVE_CERT_WORKFLOW_RUN_ID_INVALID:" + cid)
    if not _positive_int(provider.get("check_run_id")):
        errors.append("LIVE_CERT_CHECK_RUN_ID_INVALID:" + cid)
    expected_transitions = {"adoption": "COMMITTED", "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"}
    if cert.get("transitions") != expected_transitions:
        errors.append("LIVE_CERT_TRANSITIONS_INVALID:" + cid)
    if cert.get("external_source_unchanged") is not True:
        errors.append("LIVE_CERT_EXTERNAL_SOURCE_UNCHANGED_REQUIRED:" + cid)
    if cert.get("write_back_performed") is not False:
        errors.append("LIVE_CERT_WRITE_BACK_FORBIDDEN:" + cid)
    return errors


def _validate_run(run: dict[str, Any], cid: str, label: str, *, runner_required: bool) -> list[str]:
    errors: list[str] = []
    if not _positive_int(run.get("run_id")) or not _positive_int(run.get("job_id")):
        errors.append(f"LIVE_CERT_SESSION_{label}_RUN_ID_INVALID:{cid}")
    if not _sha40(run.get("head_sha")) or not _sha40(run.get("evidence_blob_sha")):
        errors.append(f"LIVE_CERT_SESSION_{label}_SHA_INVALID:{cid}")
    if not str(run.get("workflow_name") or "").strip() or not str(run.get("job_name") or "").strip():
        errors.append(f"LIVE_CERT_SESSION_{label}_IDENTITY_INVALID:{cid}")
    if runner_required and not str(run.get("runner_name") or "").strip():
        errors.append(f"LIVE_CERT_SESSION_{label}_RUNNER_INVALID:{cid}")
    return errors


def _validate_anchor(anchor: dict[str, Any], cid: str, label: str) -> list[str]:
    errors: list[str] = []
    if not str(anchor.get("tag") or "").strip():
        errors.append(f"LIVE_CERT_SESSION_{label}_TAG_INVALID:{cid}")
    for field in ("tag_object_sha", "target_sha"):
        if not _sha40(anchor.get(field)):
            errors.append(f"LIVE_CERT_SESSION_{label}_{field.upper()}_INVALID:{cid}")
    if not _sha256(anchor.get("message_sha256")):
        errors.append(f"LIVE_CERT_SESSION_{label}_MESSAGE_DIGEST_INVALID:{cid}")
    return errors


def _validate_session_certification(cert: dict[str, Any], cid: str) -> list[str]:
    errors: list[str] = []
    if {str(item) for item in (cert.get("capability_ids") or [])} != SESSION_CAPABILITIES:
        errors.append("LIVE_CERT_SESSION_SCOPE_INVALID:" + cid)
    self_host = cert.get("self_host") if isinstance(cert.get("self_host"), dict) else {}
    connected = cert.get("connected_consumer") if isinstance(cert.get("connected_consumer"), dict) else {}
    if self_host.get("repository") != "kmephis-ai/AI-Development-Framework":
        errors.append("LIVE_CERT_SESSION_SELF_HOST_REPOSITORY_INVALID:" + cid)
    if not str(connected.get("repository") or "") or connected.get("repository") == self_host.get("repository"):
        errors.append("LIVE_CERT_SESSION_CONSUMER_REPOSITORY_INVALID:" + cid)
    for lane_name, lane in (("SELF_HOST", self_host), ("CONSUMER", connected)):
        subject = lane.get("subject") if isinstance(lane.get("subject"), dict) else {}
        if not _sha40(subject.get("sha")) or not _sha40(subject.get("tree")):
            errors.append(f"LIVE_CERT_SESSION_{lane_name}_SUBJECT_INVALID:{cid}")
        issue = lane.get("issue") if isinstance(lane.get("issue"), dict) else {}
        if not _positive_int(issue.get("number")) or issue.get("state") != "closed" or not _positive_int(issue.get("terminal_comment_id")) or not _sha256(issue.get("terminal_comment_sha256")):
            errors.append(f"LIVE_CERT_SESSION_{lane_name}_ISSUE_INVALID:{cid}")
        ledger = lane.get("ledger") if isinstance(lane.get("ledger"), dict) else {}
        if not _positive_int(ledger.get("issue_number")) or not _positive_int(ledger.get("comment_id")):
            errors.append(f"LIVE_CERT_SESSION_{lane_name}_LEDGER_ID_INVALID:{cid}")
        for field in ("comment_sha256", "event_hash", "checkpoint_digest"):
            if not _sha256(ledger.get(field)):
                errors.append(f"LIVE_CERT_SESSION_{lane_name}_LEDGER_{field.upper()}_INVALID:{cid}")
        errors.extend(_validate_anchor(ledger.get("event_anchor") if isinstance(ledger.get("event_anchor"), dict) else {}, cid, lane_name + "_EVENT_ANCHOR"))
    self_a = self_host.get("session_a") if isinstance(self_host.get("session_a"), dict) else {}
    self_b = self_host.get("session_b") if isinstance(self_host.get("session_b"), dict) else {}
    con_a = connected.get("session_a") if isinstance(connected.get("session_a"), dict) else {}
    con_b = connected.get("session_b") if isinstance(connected.get("session_b"), dict) else {}
    errors.extend(_validate_run(self_a, cid, "SELF_A", runner_required=False))
    errors.extend(_validate_run(self_b, cid, "SELF_B", runner_required=False))
    errors.extend(_validate_run(con_a, cid, "CONSUMER_A", runner_required=True))
    errors.extend(_validate_run(con_b, cid, "CONSUMER_B", runner_required=True))
    if self_a.get("run_id") == self_b.get("run_id"):
        errors.append("LIVE_CERT_SESSION_SELF_RUNS_NOT_DISTINCT:" + cid)
    if con_a.get("run_id") == con_b.get("run_id"):
        errors.append("LIVE_CERT_SESSION_CONSUMER_RUNS_NOT_DISTINCT:" + cid)
    if con_a.get("runner_name") == con_b.get("runner_name"):
        errors.append("LIVE_CERT_SESSION_CONSUMER_RUNNERS_NOT_DISTINCT:" + cid)
    installed = connected.get("installed_framework") if isinstance(connected.get("installed_framework"), dict) else {}
    if not _sha40(installed.get("sha")) or not _sha40(installed.get("tree")):
        errors.append("LIVE_CERT_SESSION_INSTALLED_FRAMEWORK_INVALID:" + cid)
    root_anchor = (connected.get("ledger") or {}).get("root_anchor") if isinstance(connected.get("ledger"), dict) else {}
    errors.extend(_validate_anchor(root_anchor if isinstance(root_anchor, dict) else {}, cid, "CONSUMER_ROOT_ANCHOR"))
    duplicate = connected.get("legacy_duplicate") if isinstance(connected.get("legacy_duplicate"), dict) else {}
    if not _positive_int(duplicate.get("issue_number")) or duplicate.get("state") != "closed" or duplicate.get("state_reason") != "duplicate":
        errors.append("LIVE_CERT_SESSION_DUPLICATE_LEDGER_INVALID:" + cid)
    defect = connected.get("accepted_defect") if isinstance(connected.get("accepted_defect"), dict) else {}
    if not str(defect.get("repository") or "") or not _positive_int(defect.get("issue_number")) or defect.get("state") != "closed":
        errors.append("LIVE_CERT_SESSION_DEFECT_ACCEPTANCE_INVALID:" + cid)
    expected_self_safety = {"provider_authority": False, "independent_checkout": True, "session_a_local_runtime_present": False, "stale_authority_allowed": False}
    if self_host.get("safety") != expected_self_safety:
        errors.append("LIVE_CERT_SESSION_SELF_SAFETY_INVALID:" + cid)
    expected_con_safety = {
        "provider_authority": False, "no_duplicate_writer": True, "no_stale_mutation": True,
        "no_runtime_ledger_write_session_b": True, "singleton_ledger": True, "stale_checkpoint_rejected": True,
    }
    if connected.get("safety") != expected_con_safety:
        errors.append("LIVE_CERT_SESSION_CONSUMER_SAFETY_INVALID:" + cid)
    if ((self_host.get("ledger") or {}).get("event_anchor") or {}).get("target_sha") != (self_host.get("subject") or {}).get("sha"):
        errors.append("LIVE_CERT_SESSION_SELF_ANCHOR_TARGET_MISMATCH:" + cid)
    if ((connected.get("ledger") or {}).get("root_anchor") or {}).get("target_sha") != (connected.get("subject") or {}).get("sha"):
        errors.append("LIVE_CERT_SESSION_ROOT_ANCHOR_TARGET_MISMATCH:" + cid)
    return errors


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
        evidence_class = str(cert.get("evidence_class") or "")
        if evidence_class == UPGRADE_CLASS:
            errors.extend(_validate_upgrade_certification(cert, cid or "?"))
        elif evidence_class == SESSION_CLASS:
            errors.extend(_validate_session_certification(cert, cid or "?"))
        else:
            errors.append("LIVE_CERT_EVIDENCE_CLASS_UNSUPPORTED:" + (cid or "?"))
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
            cert_id = match.group(1)
            referenced.add(cert_id)
            cert = certs.get(cert_id)
            if cert is None:
                errors.append("CAPABILITY_LIVE_CERTIFICATION_REF_MISSING:" + capability_id + ":" + cert_id)
                continue
            if capability_id not in set(cert.get("capability_ids") or []):
                errors.append("CAPABILITY_LIVE_CERTIFICATION_SCOPE_MISMATCH:" + capability_id + ":" + cert_id)
                continue
            allowed = SUPPORTED_CLASS_CAPABILITIES.get(str(cert.get("evidence_class") or ""))
            if allowed is None or capability_id not in allowed:
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


def _provider_client(client: Any, repo: str) -> Any:
    if repo == getattr(client, "repo", None):
        return client
    from .github_provider import GitHubClient
    return GitHubClient(repo, client.token, transport=client.transport, api_base=client.api_base)


def _verify_commit_tree(client: Any, sha: str, tree: str, reasons: list[str], code: str) -> None:
    try:
        payload = client.get(f"/repos/{client.repo}/git/commits/{sha}")
    except Exception as exc:
        reasons.append(code + "_READBACK_FAILED:" + type(exc).__name__)
        return
    if payload.get("sha") != sha or str((payload.get("tree") or {}).get("sha") or "") != tree:
        reasons.append(code + "_MISMATCH")


def _verify_issue_and_comment(client: Any, expected: dict[str, Any], reasons: list[str], code: str) -> None:
    try:
        issue = client.get(f"/repos/{client.repo}/issues/{int(expected.get('number'))}")
        comment = client.get(f"/repos/{client.repo}/issues/comments/{int(expected.get('terminal_comment_id'))}")
    except Exception as exc:
        reasons.append(code + "_READBACK_FAILED:" + type(exc).__name__)
        return
    if issue.get("number") != expected.get("number") or issue.get("state") != expected.get("state"):
        reasons.append(code + "_ISSUE_MISMATCH")
    if comment.get("id") != expected.get("terminal_comment_id") or not str(comment.get("issue_url") or "").endswith(f"/issues/{expected.get('number')}"):
        reasons.append(code + "_COMMENT_IDENTITY_MISMATCH")
    if _text_digest(comment.get("body")) != expected.get("terminal_comment_sha256"):
        reasons.append(code + "_COMMENT_DIGEST_MISMATCH")


def _verify_ledger(client: Any, ledger: dict[str, Any], reasons: list[str], code: str, *, expect_singleton: bool) -> None:
    try:
        issue = client.get(f"/repos/{client.repo}/issues/{int(ledger.get('issue_number'))}")
        comment = client.get(f"/repos/{client.repo}/issues/comments/{int(ledger.get('comment_id'))}")
    except Exception as exc:
        reasons.append(code + "_READBACK_FAILED:" + type(exc).__name__)
        return
    if issue.get("number") != ledger.get("issue_number") or issue.get("state") != "open" or issue.get("title") != "[ADWF] Runtime Ledger":
        reasons.append(code + "_ISSUE_MISMATCH")
    if expect_singleton and issue.get("comments") != 1:
        reasons.append(code + "_COMMENT_COUNT_MISMATCH")
    if comment.get("id") != ledger.get("comment_id") or not str(comment.get("issue_url") or "").endswith(f"/issues/{ledger.get('issue_number')}"):
        reasons.append(code + "_COMMENT_IDENTITY_MISMATCH")
    if _text_digest(comment.get("body")) != ledger.get("comment_sha256"):
        reasons.append(code + "_COMMENT_DIGEST_MISMATCH")


def _verify_tag(client: Any, anchor: dict[str, Any], reasons: list[str], code: str) -> None:
    tag = str(anchor.get("tag") or "")
    try:
        ref = client.get(f"/repos/{client.repo}/git/ref/tags/{quote(tag, safe='')}")
        ref_obj = ref.get("object") if isinstance(ref.get("object"), dict) else {}
        if ref_obj.get("type") != "tag" or ref_obj.get("sha") != anchor.get("tag_object_sha"):
            reasons.append(code + "_REF_MISMATCH")
            return
        payload = client.get(f"/repos/{client.repo}/git/tags/{anchor.get('tag_object_sha')}")
    except Exception as exc:
        reasons.append(code + "_READBACK_FAILED:" + type(exc).__name__)
        return
    target = payload.get("object") if isinstance(payload.get("object"), dict) else {}
    if payload.get("sha") != anchor.get("tag_object_sha") or target.get("type") != "commit" or target.get("sha") != anchor.get("target_sha"):
        reasons.append(code + "_OBJECT_MISMATCH")
    if _text_digest(payload.get("message")) != anchor.get("message_sha256"):
        reasons.append(code + "_MESSAGE_DIGEST_MISMATCH")


def _verify_run(client: Any, expected: dict[str, Any], reasons: list[str], code: str) -> None:
    try:
        run = client.get(f"/repos/{client.repo}/actions/runs/{int(expected.get('run_id'))}")
        jobs_payload = client.get(f"/repos/{client.repo}/actions/runs/{int(expected.get('run_id'))}/jobs?per_page=100")
    except Exception as exc:
        reasons.append(code + "_READBACK_FAILED:" + type(exc).__name__)
        return
    if (
        run.get("id") != expected.get("run_id") or run.get("name") != expected.get("workflow_name")
        or run.get("head_sha") != expected.get("head_sha") or run.get("event") != "push"
        or run.get("status") != "completed" or run.get("conclusion") != "success"
        or str((run.get("repository") or {}).get("full_name") or "") != client.repo
    ):
        reasons.append(code + "_RUN_MISMATCH")
    jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else []
    matches = [item for item in (jobs or []) if item.get("id") == expected.get("job_id")]
    if len(matches) != 1:
        reasons.append(code + "_JOB_MISSING")
        return
    job = matches[0]
    if job.get("name") != expected.get("job_name") or job.get("status") != "completed" or job.get("conclusion") != "success":
        reasons.append(code + "_JOB_MISMATCH")
    if "runner_name" in expected and job.get("runner_name") != expected.get("runner_name"):
        reasons.append(code + "_RUNNER_MISMATCH")


def _provider_blob_json(client: Any, blob_sha: str, reasons: list[str], code: str) -> dict[str, Any]:
    try:
        payload = client.get(f"/repos/{client.repo}/git/blobs/{blob_sha}")
        if payload.get("sha") != blob_sha or payload.get("encoding") != "base64":
            reasons.append(code + "_BLOB_IDENTITY_MISMATCH")
            return {}
        encoded = "".join(str(payload.get("content") or "").split())
        raw = base64.b64decode(encoded, validate=True)
        value = strict_loads(raw.decode("utf-8"))
    except Exception as exc:
        reasons.append(code + "_BLOB_READBACK_FAILED:" + type(exc).__name__)
        return {}
    if not isinstance(value, dict):
        reasons.append(code + "_BLOB_JSON_INVALID")
        return {}
    return value


def _verify_self_host_blob_evidence(cert: dict[str, Any], a: dict[str, Any], b: dict[str, Any], reasons: list[str]) -> None:
    subject = cert["self_host"]["subject"]
    ledger = cert["self_host"]["ledger"]
    run_b = cert["self_host"]["session_b"]
    a_ledger = a.get("runtime_ledger") if isinstance(a.get("runtime_ledger"), dict) else {}
    a_read = a.get("same_session_readback") if isinstance(a.get("same_session_readback"), dict) else {}
    a_rec = a_read.get("reconciliation") if isinstance(a_read.get("reconciliation"), dict) else {}
    if (
        a.get("proof_phase") != "SESSION_A_PERSIST_AND_READBACK"
        or a.get("subject_sha") != subject.get("sha")
        or a_ledger.get("issue_number") != ledger.get("issue_number")
        or a_ledger.get("comment_id") != ledger.get("comment_id")
        or a_ledger.get("event_hash") != ledger.get("event_hash")
        or a_ledger.get("checkpoint_digest") != ledger.get("checkpoint_digest")
        or (a_ledger.get("external_anchor") or {}).get("tag") != ledger["event_anchor"].get("tag")
        or (a_ledger.get("external_anchor") or {}).get("tag_object_sha") != ledger["event_anchor"].get("tag_object_sha")
        or (a_ledger.get("external_anchor") or {}).get("ruleset_verified") is not True
        or a_ledger.get("public_projection_only") is not True
        or a_read.get("event_hash") != ledger.get("event_hash")
        or a_rec.get("provider_authority") is not False
        or a_rec.get("stale") is not False
        or a_rec.get("actual_main_sha") != subject.get("sha")
        or a_rec.get("next_step") != "RESUME_CONTEXT_ONLY"
    ):
        reasons.append("LIVE_CERT_SESSION_SELF_A_EVIDENCE_MISMATCH")
    restored = b.get("restored") if isinstance(b.get("restored"), dict) else {}
    b_rec = restored.get("reconciliation") if isinstance(restored.get("reconciliation"), dict) else {}
    if (
        b.get("proof_phase") != "SESSION_B_FRESH_PROVIDER_RESTORE"
        or str(b.get("github_run_id") or "") != str(run_b.get("run_id"))
        or b.get("subject_sha") != subject.get("sha")
        or b.get("independent_checkout") is not True
        or b.get("session_a_local_runtime_present") is not False
        or restored.get("checkpoint_digest") != ledger.get("checkpoint_digest")
        or restored.get("event_hash") != ledger.get("event_hash")
        or str(restored.get("provider_object_id") or "") != str(ledger.get("comment_id"))
        or b_rec.get("provider_authority") is not False
        or b_rec.get("stale") is not False
        or b_rec.get("actual_main_sha") != subject.get("sha")
        or b_rec.get("next_step") != "RESUME_CONTEXT_ONLY"
    ):
        reasons.append("LIVE_CERT_SESSION_SELF_B_EVIDENCE_MISMATCH")


def _verify_consumer_blob_evidence(cert: dict[str, Any], a: dict[str, Any], b: dict[str, Any], reasons: list[str]) -> None:
    lane = cert["connected_consumer"]
    subject = lane["subject"]
    installed = lane["installed_framework"]
    ledger = lane["ledger"]
    run_a = lane["session_a"]
    run_b = lane["session_b"]
    a_restore = a.get("immediate_root_only_restore") if isinstance(a.get("immediate_root_only_restore"), dict) else {}
    a_rec = a_restore.get("reconciliation") if isinstance(a_restore.get("reconciliation"), dict) else {}
    if (
        a.get("proof_phase") != "SESSION_A_LEGACY_ROOT_ADOPTION_AND_IMMEDIATE_RESTORE"
        or a.get("adoption_status") != "UNCHANGED"
        or str(a.get("github_run_id") or "") != str(run_a.get("run_id"))
        or a.get("subject_sha") != subject.get("sha")
        or a.get("installed_adwf") != installed.get("sha")
        or a.get("ledger_issue") != ledger.get("issue_number")
        or a.get("legacy_comment_sha256") != ledger.get("comment_sha256")
        or a.get("legacy_event_hash") != ledger.get("event_hash")
        or a.get("legacy_event_tag_object_sha") != ledger["event_anchor"].get("tag_object_sha")
        or a.get("root_tag_object_sha") != ledger["root_anchor"].get("tag_object_sha")
        or a.get("root_target_sha") != subject.get("sha")
        or a.get("current_writer_unique") is not True
        or a.get("no_legacy_rewrite") is not True
        or a.get("no_new_ledger_event") is not True
        or a.get("open_ledger_count_before") != 1
        or a.get("open_ledger_count_after") != 1
        or a.get("provider_authority") is not False
        or a.get("monetary_budget_usd") != 0
        or a.get("secrets") != "FORBIDDEN"
        or a.get("runner_name") != run_a.get("runner_name")
        or a_restore.get("checkpoint_digest") != ledger.get("checkpoint_digest")
        or a_restore.get("event_hash") != ledger.get("event_hash")
        or a_rec.get("provider_authority") is not False
        or a_rec.get("stale") is not True
        or a_rec.get("stale_main") is not True
        or a_rec.get("actual_main_sha") != subject.get("sha")
        or a_rec.get("next_step") != "FRESH_AUTHORITY_RESOLUTION_REQUIRED"
    ):
        reasons.append("LIVE_CERT_SESSION_CONSUMER_A_EVIDENCE_MISMATCH")
    authority = b.get("authority_evaluation") if isinstance(b.get("authority_evaluation"), dict) else {}
    restored = b.get("restored") if isinstance(b.get("restored"), dict) else {}
    b_rec = restored.get("reconciliation") if isinstance(restored.get("reconciliation"), dict) else {}
    if (
        b.get("proof_phase") != "SESSION_B_INDEPENDENT_ROOT_ONLY_RESTORE"
        or str(b.get("github_run_id") or "") != str(run_b.get("run_id"))
        or b.get("subject_sha") != subject.get("sha")
        or b.get("installed_adwf") != installed.get("sha")
        or str(b.get("source_session_a_run_id") or "") != str(run_a.get("run_id"))
        or b.get("source_session_a_runner") != run_a.get("runner_name")
        or b.get("runner_name") != run_b.get("runner_name")
        or b.get("ledger_issue") != ledger.get("issue_number")
        or b.get("legacy_comment_sha256") != ledger.get("comment_sha256")
        or b.get("legacy_event_hash") != ledger.get("event_hash")
        or b.get("legacy_event_tag_object_sha") != ledger["event_anchor"].get("tag_object_sha")
        or b.get("root_tag_object_sha") != ledger["root_anchor"].get("tag_object_sha")
        or b.get("root_target_sha") != subject.get("sha")
        or b.get("current_writer_unique") is not True
        or b.get("independent_checkout") is not True
        or b.get("session_a_local_runtime_present") is not False
        or b.get("no_duplicate_writer_created") is not True
        or b.get("no_runtime_ledger_write") is not True
        or b.get("no_stale_mutation") is not True
        or b.get("open_ledger_count") != 1
        or b.get("provider_authority") is not False
        or b.get("monetary_budget_usd") != 0
        or b.get("secrets") != "FORBIDDEN"
        or authority.get("checkpoint_stale") is not True
        or authority.get("duplicate_writer_allowed") is not False
        or authority.get("provider_authority") is not False
        or authority.get("resume_context_allowed") is not False
        or authority.get("next_step") != "FRESH_AUTHORITY_RESOLUTION_REQUIRED"
        or restored.get("checkpoint_digest") != ledger.get("checkpoint_digest")
        or restored.get("event_hash") != ledger.get("event_hash")
        or b_rec.get("provider_authority") is not False
        or b_rec.get("stale") is not True
        or b_rec.get("stale_main") is not True
        or b_rec.get("actual_main_sha") != subject.get("sha")
        or b_rec.get("next_step") != "FRESH_AUTHORITY_RESOLUTION_REQUIRED"
    ):
        reasons.append("LIVE_CERT_SESSION_CONSUMER_B_EVIDENCE_MISMATCH")


def _verify_upgrade_certification(client: Any, certification: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    provider = certification.get("provider") if isinstance(certification.get("provider"), dict) else {}
    subject = certification.get("subject") if isinstance(certification.get("subject"), dict) else {}
    framework = certification.get("framework") if isinstance(certification.get("framework"), dict) else {}
    consumer = certification.get("consumer") if isinstance(certification.get("consumer"), dict) else {}
    repo = str(provider.get("repository") or "")
    if repo != getattr(client, "repo", None):
        return {"verified": False, "reason_codes": ["LIVE_CERT_PROVIDER_REPOSITORY_MISMATCH"]}
    try:
        run = client.get(f"/repos/{repo}/actions/runs/{int(provider.get('workflow_run_id'))}")
        check = client.get(f"/repos/{repo}/check-runs/{int(provider.get('check_run_id'))}")
        target_commit = client.get(f"/repos/{repo}/git/commits/{framework.get('target_sha')}")
        source_commit = client.get(f"/repos/{repo}/git/commits/{framework.get('source_sha')}")
        consumer_client = _provider_client(client, str(consumer.get("repository") or ""))
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
    return {"verified": not reasons, "reason_codes": list(dict.fromkeys(reasons)), "workflow_run_id": run.get("id"), "check_run_id": check.get("id"), "subject_sha": subject.get("sha")}


def _verify_session_certification(client: Any, certification: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    self_host = certification.get("self_host") if isinstance(certification.get("self_host"), dict) else {}
    connected = certification.get("connected_consumer") if isinstance(certification.get("connected_consumer"), dict) else {}
    self_repo = str(self_host.get("repository") or "")
    if self_repo != getattr(client, "repo", None):
        return {"verified": False, "reason_codes": ["LIVE_CERT_SESSION_SELF_PROVIDER_REPOSITORY_MISMATCH"]}
    try:
        self_client = _provider_client(client, self_repo)
        con_client = _provider_client(client, str(connected.get("repository") or ""))
        defect = connected.get("accepted_defect") if isinstance(connected.get("accepted_defect"), dict) else {}
        defect_client = _provider_client(client, str(defect.get("repository") or ""))
    except Exception as exc:
        return {"verified": False, "reason_codes": ["LIVE_CERT_SESSION_CLIENT_INIT_FAILED:" + type(exc).__name__]}

    _verify_commit_tree(self_client, self_host["subject"]["sha"], self_host["subject"]["tree"], reasons, "LIVE_CERT_SESSION_SELF_SUBJECT")
    _verify_commit_tree(con_client, connected["subject"]["sha"], connected["subject"]["tree"], reasons, "LIVE_CERT_SESSION_CONSUMER_SUBJECT")
    _verify_commit_tree(self_client, connected["installed_framework"]["sha"], connected["installed_framework"]["tree"], reasons, "LIVE_CERT_SESSION_INSTALLED_FRAMEWORK")
    _verify_issue_and_comment(self_client, self_host["issue"], reasons, "LIVE_CERT_SESSION_SELF_PROOF")
    _verify_issue_and_comment(con_client, connected["issue"], reasons, "LIVE_CERT_SESSION_CONSUMER_PROOF")
    _verify_ledger(self_client, self_host["ledger"], reasons, "LIVE_CERT_SESSION_SELF_LEDGER", expect_singleton=True)
    _verify_ledger(con_client, connected["ledger"], reasons, "LIVE_CERT_SESSION_CONSUMER_LEDGER", expect_singleton=True)
    _verify_tag(self_client, self_host["ledger"]["event_anchor"], reasons, "LIVE_CERT_SESSION_SELF_EVENT_ANCHOR")
    _verify_tag(con_client, connected["ledger"]["root_anchor"], reasons, "LIVE_CERT_SESSION_CONSUMER_ROOT_ANCHOR")
    _verify_tag(con_client, connected["ledger"]["event_anchor"], reasons, "LIVE_CERT_SESSION_CONSUMER_EVENT_ANCHOR")
    for label, lane_client, run in (
        ("LIVE_CERT_SESSION_SELF_A", self_client, self_host["session_a"]),
        ("LIVE_CERT_SESSION_SELF_B", self_client, self_host["session_b"]),
        ("LIVE_CERT_SESSION_CONSUMER_A", con_client, connected["session_a"]),
        ("LIVE_CERT_SESSION_CONSUMER_B", con_client, connected["session_b"]),
    ):
        _verify_run(lane_client, run, reasons, label)

    duplicate = connected["legacy_duplicate"]
    try:
        dup_issue = con_client.get(f"/repos/{con_client.repo}/issues/{int(duplicate.get('issue_number'))}")
    except Exception as exc:
        reasons.append("LIVE_CERT_SESSION_DUPLICATE_LEDGER_READBACK_FAILED:" + type(exc).__name__)
    else:
        if dup_issue.get("number") != duplicate.get("issue_number") or dup_issue.get("state") != "closed" or dup_issue.get("state_reason") != "duplicate" or dup_issue.get("comments") != 0:
            reasons.append("LIVE_CERT_SESSION_DUPLICATE_LEDGER_MISMATCH")

    try:
        defect_issue = defect_client.get(f"/repos/{defect_client.repo}/issues/{int(defect.get('issue_number'))}")
    except Exception as exc:
        reasons.append("LIVE_CERT_SESSION_DEFECT_READBACK_FAILED:" + type(exc).__name__)
    else:
        if defect_issue.get("number") != defect.get("issue_number") or defect_issue.get("state") != "closed":
            reasons.append("LIVE_CERT_SESSION_DEFECT_NOT_CLOSED")

    self_a_blob = _provider_blob_json(self_client, self_host["session_a"]["evidence_blob_sha"], reasons, "LIVE_CERT_SESSION_SELF_A")
    self_b_blob = _provider_blob_json(self_client, self_host["session_b"]["evidence_blob_sha"], reasons, "LIVE_CERT_SESSION_SELF_B")
    con_a_blob = _provider_blob_json(con_client, connected["session_a"]["evidence_blob_sha"], reasons, "LIVE_CERT_SESSION_CONSUMER_A")
    con_b_blob = _provider_blob_json(con_client, connected["session_b"]["evidence_blob_sha"], reasons, "LIVE_CERT_SESSION_CONSUMER_B")
    if self_a_blob and self_b_blob:
        _verify_self_host_blob_evidence(certification, self_a_blob, self_b_blob, reasons)
    if con_a_blob and con_b_blob:
        _verify_consumer_blob_evidence(certification, con_a_blob, con_b_blob, reasons)

    if self_host["session_a"]["run_id"] == self_host["session_b"]["run_id"]:
        reasons.append("LIVE_CERT_SESSION_SELF_RUNS_NOT_DISTINCT")
    if connected["session_a"]["run_id"] == connected["session_b"]["run_id"]:
        reasons.append("LIVE_CERT_SESSION_CONSUMER_RUNS_NOT_DISTINCT")
    if connected["session_a"]["runner_name"] == connected["session_b"]["runner_name"]:
        reasons.append("LIVE_CERT_SESSION_CONSUMER_RUNNERS_NOT_DISTINCT")

    return {
        "verified": not reasons,
        "reason_codes": list(dict.fromkeys(reasons)),
        "self_host_run_ids": [self_host["session_a"]["run_id"], self_host["session_b"]["run_id"]],
        "consumer_run_ids": [connected["session_a"]["run_id"], connected["session_b"]["run_id"]],
        "subject_sha": connected["subject"]["sha"],
    }


def verify_provider_certification(client: Any, certification: dict[str, Any]) -> dict[str, Any]:
    """Fresh provider readback for one durable certification."""
    evidence_class = str(certification.get("evidence_class") or "")
    if evidence_class == UPGRADE_CLASS:
        return _verify_upgrade_certification(client, certification)
    if evidence_class == SESSION_CLASS:
        return _verify_session_certification(client, certification)
    return {"verified": False, "reason_codes": ["LIVE_CERT_EVIDENCE_CLASS_UNSUPPORTED"]}
