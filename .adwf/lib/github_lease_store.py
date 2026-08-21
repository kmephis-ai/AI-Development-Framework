"""GitHub-backed immutable CAS stream for ORCH writer lease arbitration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import copy
import hashlib
import json
import re

from .github_provider import GitHubClient
from .github_rulesets import verify_runtime_anchor_ruleset
from .lease_registry import acquire_registry_lease, empty_lease_registry, heartbeat_registry_lease, release_registry_lease, validate_lease_registry
from .provider_contracts import ProviderContractError
from .strict_json import loads as strict_loads

LEASE_ANCHOR_PREFIX = "adwf-runtime-anchor-lease-v1-"
LEASE_ANCHOR_ROLE = "ADWF_ORCH_LEASE_REGISTRY_V1"
LEASE_ANCHOR_EVENT_VERSION = 1
_TAG_RE = re.compile(r"^" + re.escape(LEASE_ANCHOR_PREFIX) + r"([0-9]{9})$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_text(value: Any) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _event_digest(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.pop("event_digest", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _tag_name(revision: int) -> str:
    if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 999_999_999:
        raise ValueError("LEASE_PROVIDER_REVISION_INVALID")
    return f"{LEASE_ANCHOR_PREFIX}{revision:09d}"


def _build_event(registry: dict[str, Any], previous_tag_object_sha: str | None) -> dict[str, Any]:
    findings = validate_lease_registry(registry)
    if findings:
        raise ValueError("LEASE_PROVIDER_STATE_INVALID:" + ",".join(findings))
    revision = registry["revision"]
    if revision < 1:
        raise ValueError("LEASE_PROVIDER_REVISION_INVALID")
    if previous_tag_object_sha is not None and _SHA40_RE.fullmatch(previous_tag_object_sha) is None:
        raise ValueError("LEASE_PROVIDER_PREVIOUS_ANCHOR_INVALID")
    event = {"schema_version": LEASE_ANCHOR_EVENT_VERSION, "role": LEASE_ANCHOR_ROLE, "revision": revision, "previous_tag_object_sha": previous_tag_object_sha, "registry": copy.deepcopy(registry), "event_digest": ""}
    event["event_digest"] = _event_digest(event)
    return event


def _parse_event(message: str) -> dict[str, Any]:
    try:
        value = strict_loads(message)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("LEASE_PROVIDER_EVENT_JSON_INVALID") from exc
    required = {"schema_version", "role", "revision", "previous_tag_object_sha", "registry", "event_digest"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("LEASE_PROVIDER_EVENT_FIELDS_INVALID")
    if value.get("schema_version") != LEASE_ANCHOR_EVENT_VERSION or value.get("role") != LEASE_ANCHOR_ROLE:
        raise ValueError("LEASE_PROVIDER_EVENT_IDENTITY_INVALID")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("LEASE_PROVIDER_EVENT_REVISION_INVALID")
    previous = value.get("previous_tag_object_sha")
    if previous is not None and (not isinstance(previous, str) or _SHA40_RE.fullmatch(previous) is None):
        raise ValueError("LEASE_PROVIDER_EVENT_PREVIOUS_INVALID")
    digest = value.get("event_digest")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None or digest != _event_digest(value):
        raise ValueError("LEASE_PROVIDER_EVENT_INTEGRITY")
    registry = value.get("registry")
    findings = validate_lease_registry(registry)
    if findings:
        raise ValueError("LEASE_PROVIDER_STATE_INVALID:" + ",".join(findings))
    if registry.get("revision") != revision:
        raise ValueError("LEASE_PROVIDER_EVENT_REGISTRY_REVISION_MISMATCH")
    if _canonical_text(value) != message:
        raise ValueError("LEASE_PROVIDER_EVENT_NOT_CANONICAL")
    return value


@dataclass
class GitHubLeaseStore:
    client: GitHubClient
    anchor_prefix: str = LEASE_ANCHOR_PREFIX

    def __post_init__(self) -> None:
        if self.anchor_prefix != LEASE_ANCHOR_PREFIX:
            raise ValueError("LEASE_PROVIDER_ANCHOR_PREFIX_INVALID")

    def _default_branch_and_main(self) -> tuple[str, str]:
        info = self.client.repo_info()
        default = str(info.get("default_branch") or "")
        if not default:
            raise ValueError("LEASE_PROVIDER_DEFAULT_BRANCH_MISSING")
        branch = self.client.branch(default)
        sha = str((branch.get("commit") or {}).get("sha") or "")
        if _SHA40_RE.fullmatch(sha) is None:
            raise ValueError("LEASE_PROVIDER_MAIN_SHA_INVALID")
        return default, sha

    def _require_main(self, expected_main_sha: str) -> str:
        if _SHA40_RE.fullmatch(str(expected_main_sha)) is None:
            raise ValueError("LEASE_PROVIDER_EXPECTED_MAIN_SHA_INVALID")
        _, current = self._default_branch_and_main()
        if current != expected_main_sha:
            raise ValueError("LEASE_PROVIDER_MAIN_SHA_DRIFT")
        return current

    def _require_anchor_ruleset(self) -> dict[str, Any]:
        try:
            verified = verify_runtime_anchor_ruleset(self.client.rulesets())
        except Exception as exc:
            raise ValueError("LEASE_PROVIDER_ANCHOR_RULESET_READBACK_FAILED") from exc
        if verified.get("readback_verified") is not True:
            reasons = ",".join(str(item) for item in verified.get("reason_codes") or [])
            raise ValueError("LEASE_PROVIDER_ANCHOR_RULESET_NOT_VERIFIED:" + reasons)
        return verified

    def _matching_refs(self) -> list[tuple[int, str, dict[str, Any]]]:
        rows = self.client.matching_tag_refs(self.anchor_prefix)
        parsed: list[tuple[int, str, dict[str, Any]]] = []
        seen: set[int] = set()
        for ref in rows:
            name = str(ref.get("ref") or "").split("refs/tags/", 1)[-1]
            match = _TAG_RE.fullmatch(name)
            if match is None:
                raise ValueError("LEASE_PROVIDER_ANCHOR_NAME_INVALID:" + name[:120])
            revision = int(match.group(1))
            if revision in seen:
                raise ValueError("LEASE_PROVIDER_ANCHOR_REVISION_DUPLICATE")
            seen.add(revision)
            parsed.append((revision, name, ref))
        return sorted(parsed, key=lambda item: item[0])

    def _read_anchor(self, revision: int, name: str, ref: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        if name != _tag_name(revision):
            raise ValueError("LEASE_PROVIDER_ANCHOR_NAME_REVISION_MISMATCH")
        tag_object_sha = str((ref.get("object") or {}).get("sha") or "")
        if _SHA40_RE.fullmatch(tag_object_sha) is None:
            raise ValueError("LEASE_PROVIDER_ANCHOR_REF_INVALID")
        try:
            obj = self.client.tag_object(tag_object_sha)
        except Exception as exc:
            raise ValueError("LEASE_PROVIDER_ANCHOR_OBJECT_UNAVAILABLE") from exc
        if str(obj.get("sha") or "") != tag_object_sha:
            raise ValueError("LEASE_PROVIDER_ANCHOR_OBJECT_SHA_MISMATCH")
        if str(obj.get("tag") or "") != name:
            raise ValueError("LEASE_PROVIDER_ANCHOR_OBJECT_TAG_MISMATCH")
        target = obj.get("object") or {}
        target_sha = str(target.get("sha") or "")
        if _SHA40_RE.fullmatch(target_sha) is None or target.get("type") != "commit":
            raise ValueError("LEASE_PROVIDER_ANCHOR_TARGET_INVALID")
        message = obj.get("message")
        if not isinstance(message, str):
            raise ValueError("LEASE_PROVIDER_ANCHOR_MESSAGE_INVALID")
        event = _parse_event(message)
        if event["revision"] != revision:
            raise ValueError("LEASE_PROVIDER_ANCHOR_EVENT_REVISION_MISMATCH")
        registry = event["registry"]
        if registry.get("repository") != self.client.repo:
            raise ValueError("LEASE_PROVIDER_REPOSITORY_MISMATCH")
        if registry.get("observed_main_sha") != target_sha:
            raise ValueError("LEASE_PROVIDER_ANCHOR_MAIN_BINDING_MISMATCH")
        return event, tag_object_sha, target_sha

    def read(self, *, expected_main_sha: str, policy_max_parallel_writers: int) -> tuple[dict[str, Any], str | None]:
        self._require_main(expected_main_sha)
        self._require_anchor_ruleset()
        refs = self._matching_refs()
        if not refs:
            self._require_main(expected_main_sha)
            return empty_lease_registry(self.client.repo, expected_main_sha, max_parallel_writers=policy_max_parallel_writers), None
        previous: str | None = None
        latest: dict[str, Any] | None = None
        for expected_revision, (revision, name, ref) in enumerate(refs, 1):
            if revision != expected_revision:
                raise ValueError(f"LEASE_PROVIDER_ANCHOR_SEQUENCE_GAP:expected={expected_revision}:actual={revision}")
            event, object_sha, _ = self._read_anchor(revision, name, ref)
            if event["previous_tag_object_sha"] != previous:
                raise ValueError("LEASE_PROVIDER_ANCHOR_CHAIN_MISMATCH")
            previous = object_sha
            latest = copy.deepcopy(event["registry"])
        self._require_main(expected_main_sha)
        assert latest is not None
        if latest["max_parallel_writers"] != policy_max_parallel_writers:
            raise ValueError("LEASE_POLICY_CEILING_MISMATCH")
        return latest, previous

    def _append_cas(self, state: dict[str, Any], *, previous_tag_object_sha: str | None, expected_previous_revision: int, expected_main_sha: str) -> tuple[dict[str, Any], str]:
        findings = validate_lease_registry(state)
        if findings:
            raise ValueError("LEASE_PROVIDER_STATE_INVALID:" + ",".join(findings))
        if state["revision"] != expected_previous_revision + 1:
            raise ValueError("LEASE_PROVIDER_REVISION_TRANSITION_INVALID")
        if state["observed_main_sha"] != expected_main_sha:
            raise ValueError("LEASE_PROVIDER_STATE_MAIN_BINDING_INVALID")
        self._require_main(expected_main_sha)
        self._require_anchor_ruleset()
        name = _tag_name(state["revision"])
        event = _build_event(state, previous_tag_object_sha)
        message = _canonical_text(event)
        created = self.client.create_tag_object(name, expected_main_sha, message)
        tag_object_sha = str(created.get("sha") or "")
        if _SHA40_RE.fullmatch(tag_object_sha) is None:
            raise ValueError("LEASE_PROVIDER_ANCHOR_OBJECT_CREATE_FAILED")
        try:
            self.client.create_tag_ref(name, tag_object_sha)
        except ProviderContractError as exc:
            if str(exc) in {"PROVIDER_HTTP_409", "PROVIDER_HTTP_422"}:
                raise ValueError("LEASE_PROVIDER_CAS_CONFLICT") from exc
            raise
        try:
            exact_ref = self.client.tag_ref(name)
        except Exception as exc:
            raise ValueError("LEASE_PROVIDER_ANCHOR_REF_READBACK_FAILED") from exc
        read_event, read_object_sha, target_sha = self._read_anchor(state["revision"], name, exact_ref)
        if read_object_sha != tag_object_sha or target_sha != expected_main_sha or read_event != event:
            raise ValueError("LEASE_PROVIDER_READBACK_MISMATCH")
        self._require_main(expected_main_sha)
        self._require_anchor_ruleset()
        return copy.deepcopy(state), tag_object_sha

    def _persist_transition(self, state: dict[str, Any], *, previous_tag_object_sha: str | None, expected_previous_revision: int, expected_main_sha: str, policy_max_parallel_writers: int) -> tuple[dict[str, Any], str]:
        try:
            return self._append_cas(state, previous_tag_object_sha=previous_tag_object_sha, expected_previous_revision=expected_previous_revision, expected_main_sha=expected_main_sha)
        except ValueError as exc:
            if str(exc) != "LEASE_PROVIDER_CAS_CONFLICT":
                raise
            winner, winner_anchor = self.read(expected_main_sha=expected_main_sha, policy_max_parallel_writers=policy_max_parallel_writers)
            # 409/422 alone does not prove a competing winner. Only a provider
            # readback that advanced beyond our observed revision may be
            # classified as a lost CAS race.
            if winner["revision"] <= expected_previous_revision or winner_anchor is None:
                raise ValueError("LEASE_PROVIDER_CAS_CONFLICT_UNRESOLVED") from exc
            suffix = f":anchor={winner_anchor}"
            raise ValueError("LEASE_PROVIDER_CAS_LOST:revision=" + str(winner["revision"]) + suffix) from exc

    def acquire(self, *, expected_main_sha: str, policy_max_parallel_writers: int, issue_id: str, roadmap_id: str, worker_id: str, base_sha: str, branch: str, resources: list[dict[str, Any]], now=None, ttl_minutes: int = 120, lease_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any], str]:
        self._require_main(expected_main_sha)
        state, previous_anchor = self.read(expected_main_sha=expected_main_sha, policy_max_parallel_writers=policy_max_parallel_writers)
        active = [lease for lease in state["leases"] if lease["status"] == "ACTIVE"]
        if state["observed_main_sha"] != expected_main_sha and active:
            raise ValueError("LEASE_PROVIDER_RECONCILIATION_REQUIRED")
        updated, lease = acquire_registry_lease(state, expected_revision=state["revision"], observed_main_sha=expected_main_sha, policy_max_parallel_writers=policy_max_parallel_writers, issue_id=issue_id, roadmap_id=roadmap_id, worker_id=worker_id, base_sha=base_sha, branch=branch, resources=resources, now=now, ttl_minutes=ttl_minutes, lease_id=lease_id)
        persisted, anchor = self._persist_transition(updated, previous_tag_object_sha=previous_anchor, expected_previous_revision=state["revision"], expected_main_sha=expected_main_sha, policy_max_parallel_writers=policy_max_parallel_writers)
        return persisted, lease, anchor

    def heartbeat(self, *, expected_main_sha: str, policy_max_parallel_writers: int, lease_id: str, worker_id: str, now=None, ttl_minutes: int = 120) -> tuple[dict[str, Any], str]:
        self._require_main(expected_main_sha)
        state, previous_anchor = self.read(expected_main_sha=expected_main_sha, policy_max_parallel_writers=policy_max_parallel_writers)
        if state["observed_main_sha"] != expected_main_sha:
            raise ValueError("LEASE_PROVIDER_RECONCILIATION_REQUIRED")
        updated = heartbeat_registry_lease(state, expected_revision=state["revision"], lease_id=lease_id, worker_id=worker_id, observed_main_sha=expected_main_sha, now=now, ttl_minutes=ttl_minutes)
        return self._persist_transition(updated, previous_tag_object_sha=previous_anchor, expected_previous_revision=state["revision"], expected_main_sha=expected_main_sha, policy_max_parallel_writers=policy_max_parallel_writers)

    def release(self, *, expected_main_sha: str, policy_max_parallel_writers: int, lease_id: str, worker_id: str, provider_reconciled: bool, provider_reconciliation_ref: str, now=None) -> tuple[dict[str, Any], str]:
        self._require_main(expected_main_sha)
        self._require_anchor_ruleset()
        refs = self._matching_refs()
        if not refs:
            raise ValueError("LEASE_PROVIDER_RELEASE_WITHOUT_REGISTRY")
        previous: str | None = None
        state: dict[str, Any] | None = None
        for expected_revision, (revision, name, ref) in enumerate(refs, 1):
            if revision != expected_revision:
                raise ValueError("LEASE_PROVIDER_ANCHOR_SEQUENCE_GAP")
            event, object_sha, _ = self._read_anchor(revision, name, ref)
            if event["previous_tag_object_sha"] != previous:
                raise ValueError("LEASE_PROVIDER_ANCHOR_CHAIN_MISMATCH")
            previous = object_sha
            state = copy.deepcopy(event["registry"])
        assert state is not None
        if state["max_parallel_writers"] != policy_max_parallel_writers:
            raise ValueError("LEASE_POLICY_CEILING_MISMATCH")
        updated = release_registry_lease(state, expected_revision=state["revision"], lease_id=lease_id, worker_id=worker_id, observed_main_sha=expected_main_sha, provider_reconciled=provider_reconciled, provider_reconciliation_ref=provider_reconciliation_ref, now=now)
        return self._persist_transition(updated, previous_tag_object_sha=previous, expected_previous_revision=state["revision"], expected_main_sha=expected_main_sha, policy_max_parallel_writers=policy_max_parallel_writers)
