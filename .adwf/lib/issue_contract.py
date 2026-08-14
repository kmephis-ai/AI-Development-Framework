"""Строгий контракт: один Roadmap ID = один GitHub/GitLab Issue = один Writer."""
from __future__ import annotations

from typing import Any
import re

ROADMAP_ID = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
RISK_RANK = {f"R{i}": i for i in range(5)}
FORM_HEADINGS = {
    "roadmap_id": "Roadmap ID", "goal": "Цель", "value": "Зачем это владельцу или продукту",
    "scope": "Что входит в работу", "out_of_scope": "Что точно не входит",
    "acceptance_criteria": "Критерии приёмки", "verification_plan": "План проверки и evidence",
    "dependencies": "Зависимости", "dependencies_resolved": "Зависимости проверены",
    "conflict_domains": "Контур конфликта", "type": "Тип работы", "priority": "Приоритет",
    "roadmap_order": "Порядок в Roadmap", "risk": "Риск",
    "product_impact": "Влияет на реальный продукт", "human_required": "Требуется решение владельца",
}

FIELDS = {
    "roadmap_id": re.compile(r"^Roadmap-ID:\s*([A-Z][A-Z0-9_]*-\d+)\s*$", re.MULTILINE),
    "issue_number": re.compile(r"^Issue:\s*#(\d+)\s*$", re.MULTILINE),
    "writer_lease": re.compile(r"^Writer-Lease:\s*([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\s*$", re.MULTILINE | re.IGNORECASE),
    "risk": re.compile(r"^Risk:\s*(R[0-4])\s*$", re.MULTILINE),
}

ISSUE_MARKER = re.compile(
    r"<!-- ADWF-CONTRACT\s+Roadmap-ID:\s*([^\s]+)\s+Writer:\s*([A-Za-z0-9_.@:-]+)\s+Writer-Lease:\s*([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\s+Workspace:\s*([a-z0-9][a-z0-9-]{2,96})\s+State:\s*([^\s]+)\s+Heartbeat:\s*([^\s]+)\s+Expires:\s*([^\s]+)\s+-->",
    re.IGNORECASE,
)

REQUIRED_SECTIONS = (
    "## Контракт",
    "## Что изменено и зачем",
    "## Scope",
    "## Проверки",
    "## Risk / rollback",
    "## Trust boundary",
)


def parse_pr_contract(body: str) -> dict[str, Any]:
    errors: list[str] = []
    values: dict[str, Any] = {}
    for section in REQUIRED_SECTIONS:
        if body.count(section) != 1:
            errors.append(f"SECTION_COUNT:{section}")
    for name, pattern in FIELDS.items():
        matches = pattern.findall(body)
        if len(matches) != 1:
            errors.append(f"FIELD_COUNT:{name}:{len(matches)}")
        else:
            values[name] = int(matches[0]) if name == "issue_number" else matches[0]
    if re.search(r"^Roadmap-ID:\s*(CHANGE_ME|TBD)", body, re.MULTILINE):
        errors.append("ROADMAP_ID_PLACEHOLDER")
    return {"valid": not errors, "errors": errors, **values}


def parse_issue_marker(body: str) -> dict[str, Any]:
    matches = ISSUE_MARKER.findall(body)
    if len(matches) != 1:
        return {"valid": False, "errors": [f"ISSUE_MARKER_COUNT:{len(matches)}"]}
    roadmap_id, writer_id, lease_id, workspace_id, state, heartbeat_at, expires_at = matches[0]
    errors: list[str] = []
    if ROADMAP_ID.fullmatch(roadmap_id) is None:
        errors.append("ISSUE_MARKER_ROADMAP_ID_INVALID")
    if state not in {"CLAIMED", "IN_PROGRESS", "REVIEW", "VERIFICATION", "RECOVERY", "DONE"}:
        errors.append("ISSUE_MARKER_STATE_INVALID")
    return {"valid": not errors, "errors": errors, "roadmap_id": roadmap_id, "writer_id": writer_id,
            "lease_id": lease_id.lower(), "workspace_id": workspace_id, "state": state,
            "heartbeat_at": heartbeat_at, "expires_at": expires_at}


def replace_issue_marker_state(body: str, state: str) -> str:
    marker = parse_issue_marker(body)
    if not marker["valid"]:
        raise ValueError("ISSUE_MARKER_INVALID:" + ",".join(marker["errors"]))
    replacement = (
        f"<!-- ADWF-CONTRACT Roadmap-ID: {marker['roadmap_id']} Writer: {marker['writer_id']} "
        f"Writer-Lease: {marker['lease_id']} Workspace: {marker['workspace_id']} State: {state} "
        f"Heartbeat: {marker['heartbeat_at']} Expires: {marker['expires_at']} -->"
    )
    return ISSUE_MARKER.sub(replacement, body, count=1)


def pr_attestations(body: str) -> list[str]:
    required = (
        "Tests выполнены либо `N/A` обоснован",
        "Documentation Impact проверен",
        "PR не ослабляет ADWF/CI/security/autonomy/permissions",
    )
    errors = []
    for text in required:
        if re.search(rf"^- \[[xX]\]\s+{re.escape(text)}\s*$", body, re.MULTILINE) is None:
            errors.append("PR_ATTESTATION_MISSING:" + text)
    if re.search(r"^\s*-?\s*Scope drift:\s*HIGH\s*$", body, re.MULTILINE | re.IGNORECASE):
        errors.append("PR_SCOPE_DRIFT_HIGH")
    return errors


def validate_one_to_one(contract: dict[str, Any], issue: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    if not contract.get("valid"):
        errors.extend(contract.get("errors", []))
        return errors
    if issue is not None:
        if str(issue.get("roadmap_id")) != str(contract.get("roadmap_id")):
            errors.append("ROADMAP_ID_MISMATCH")
        if int(issue.get("number", -1)) != int(contract.get("issue_number", -2)):
            errors.append("ISSUE_NUMBER_MISMATCH")
        if issue.get("state") not in {"CLAIMED", "IN_PROGRESS", "REVIEW", "VERIFICATION"}:
            errors.append("ISSUE_STATE_NOT_ACTIVE")
        if str(issue.get("lease_id", "")).lower() != str(contract.get("writer_lease", "")).lower():
            errors.append("LEASE_MISMATCH")
    return errors


def _form_sections(body: str) -> tuple[dict[str, str], list[str]]:
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", body, re.MULTILINE))
    values: dict[str, str] = {}
    duplicates: list[str] = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value = re.sub(r"<!--.*?-->", "", body[match.end():end], flags=re.DOTALL).strip()
        if heading in values:
            duplicates.append(heading)
        values[heading] = value
    return values, duplicates


def _list(value: str, *, split_commas: bool = False) -> list[str]:
    source = value.replace(",", "\n") if split_commas else value
    result = []
    for line in source.splitlines():
        cleaned = re.sub(r"^(?:[-*+]\s+|[0-9]+[.)]\s+)", "", line.strip())
        if cleaned and cleaned not in {"NONE", "_No response_"}:
            result.append(cleaned)
    return result


def _yes_no(value: str, field: str, errors: list[str]) -> bool:
    normalized = value.strip().upper()
    if normalized not in {"YES", "NO"}:
        errors.append(f"ISSUE_FORM_BOOLEAN_INVALID:{field}")
    return normalized == "YES"


def parse_issue_form(body: str, *, number: Any, title: str, state: str, max_autonomous_risk: str = "R1") -> tuple[dict[str, Any], list[str]]:
    """Преобразовать GitHub/GitLab Issue Form body в строгий provider-neutral item."""
    sections, duplicates = _form_sections(body)
    errors = [f"ISSUE_FORM_HEADING_DUPLICATE:{name}" for name in duplicates]
    raw: dict[str, str] = {}
    for field, heading in FORM_HEADINGS.items():
        value = sections.get(heading, "").strip()
        if not value or value == "_No response_":
            errors.append(f"ISSUE_FORM_FIELD_MISSING:{field}")
        raw[field] = value
    roadmap_id = raw["roadmap_id"]
    if ROADMAP_ID.fullmatch(roadmap_id) is None:
        errors.append("ISSUE_FORM_ROADMAP_ID_INVALID")
    if roadmap_id and not title.strip().startswith(f"[{roadmap_id}]"):
        errors.append("ISSUE_TITLE_ROADMAP_ID_MISMATCH")
    try:
        issue_number = int(number)
        if issue_number < 1:
            raise ValueError
    except (TypeError, ValueError):
        issue_number = 0
        errors.append("ISSUE_NUMBER_INVALID")
    try:
        roadmap_order = int(raw["roadmap_order"])
        if roadmap_order < 0:
            raise ValueError
    except ValueError:
        roadmap_order = -1
        errors.append("ISSUE_FORM_ROADMAP_ORDER_INVALID")
    dependencies_resolved = _yes_no(raw["dependencies_resolved"], "dependencies_resolved", errors)
    human_required = _yes_no(raw["human_required"], "human_required", errors)
    product_impact = _yes_no(raw["product_impact"], "product_impact", errors)
    risk = raw["risk"]
    autonomy_allowed = (
        not human_required and dependencies_resolved and risk in RISK_RANK
        and max_autonomous_risk in RISK_RANK and RISK_RANK[risk] <= RISK_RANK[max_autonomous_risk]
    )
    issue = {
        "id": roadmap_id, "roadmap_id": roadmap_id, "number": issue_number, "title": title.strip(),
        "state": state, "priority": raw["priority"], "risk": risk, "type": raw["type"], "goal": raw["goal"],
        "acceptance_criteria": _list(raw["acceptance_criteria"]),
        "verification_plan": _list(raw["verification_plan"]),
        "conflict_domains": _list(raw["conflict_domains"], split_commas=True),
        "dependencies": _list(raw["dependencies"], split_commas=True),
        "dependencies_resolved": dependencies_resolved, "human_required": human_required,
        "autonomy_allowed": autonomy_allowed, "product_impact": product_impact, "roadmap_order": roadmap_order,
    }
    return issue, list(dict.fromkeys(errors))
