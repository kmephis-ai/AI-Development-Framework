"""Owner Experience: Product Brief, exact-preview acceptance и CEO projection.

Все функции детерминированы и работают без AI/API. ИИ может подготовить более
красивый текст интерактивно, но обязательный CI всегда имеет этот fallback.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .assurance import machine_verified as assurance_machine_verified
import copy
import hashlib
import re

from .incidents import digest, sanitize_text, sanitize_value


SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ACCEPTANCE_STATES = {"PENDING", "ACCEPTED", "CHANGES_REQUESTED", "DEFERRED", "STALE", "NOT_REQUIRED"}
BRIEF_ID = re.compile(r"^BRIEF-[0-9a-f]{16}$")


def _iso(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _require_sha(value: Any, code: str) -> str:
    text = str(value or "")
    if SHA.fullmatch(text) is None:
        raise ValueError(code)
    return text


def _require_digest(value: Any, code: str) -> str:
    text = str(value or "")
    if SHA256.fullmatch(text) is None:
        raise ValueError(code)
    return text


def compute_preview_digest(content: bytes | str) -> str:
    """Digest immutable preview manifest/archive bytes for exact owner binding."""
    raw = content if isinstance(content, bytes) else str(content).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def create_product_brief(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Создать краткую постановку на языке владельца, сохранив его intent."""
    if not isinstance(payload, dict):
        raise ValueError("PRODUCT_BRIEF_NOT_OBJECT")
    cleaned, redactions = sanitize_value(copy.deepcopy(payload))
    required_text = ("goal_ru", "value_ru", "outcome_ru")
    for key in required_text:
        if len(str(cleaned.get(key) or "").strip()) < 10:
            raise ValueError(f"PRODUCT_BRIEF_FIELD_MISSING:{key}")
    criteria = cleaned.get("acceptance_criteria_ru")
    if not isinstance(criteria, list) or not criteria or not all(isinstance(item, str) and len(item.strip()) >= 5 for item in criteria):
        raise ValueError("PRODUCT_BRIEF_ACCEPTANCE_INVALID")
    brief_source = {
        "goal_ru": str(cleaned["goal_ru"]).strip(),
        "value_ru": str(cleaned["value_ru"]).strip(),
        "outcome_ru": str(cleaned["outcome_ru"]).strip(),
        "acceptance_criteria_ru": [item.strip() for item in criteria],
        "visual_expectation_ru": str(cleaned.get("visual_expectation_ru") or "Визуальная приёмка не требуется.").strip(),
        "constraints_ru": [str(item).strip() for item in cleaned.get("constraints_ru", []) if str(item).strip()],
        "owner_request_original": str(cleaned.get("owner_request_original") or cleaned["goal_ru"]).strip(),
    }
    brief_id = str(cleaned.get("brief_id") or f"BRIEF-{digest(brief_source)[:16]}")
    if BRIEF_ID.fullmatch(brief_id) is None:
        raise ValueError("PRODUCT_BRIEF_ID_INVALID")
    return {
        "schema_version": 1,
        "brief_id": brief_id,
        **brief_source,
        "created_at": _iso(now),
        "redaction": {"status": "SANITIZED", "findings_count": len(redactions)},
    }


def create_preview(
    *, head_sha: str, preview_digest: str, created_at: str | None = None,
    valid_until: str | None = None, url: str | None = None,
    screenshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    exact_sha = _require_sha(head_sha, "PREVIEW_HEAD_SHA_INVALID")
    exact_digest = _require_digest(preview_digest, "PREVIEW_DIGEST_INVALID")
    cleaned_url, _ = sanitize_text(str(url)) if url else (None, [])
    safe_screenshots = []
    for item in screenshots or []:
        if not isinstance(item, dict):
            raise ValueError("PREVIEW_SCREENSHOT_INVALID")
        name, _ = sanitize_text(str(item.get("name") or "screenshot"))
        artifact, _ = sanitize_text(str(item.get("artifact") or ""))
        safe_screenshots.append({
            "name": name[:120],
            "digest": _require_digest(item.get("digest"), "PREVIEW_SCREENSHOT_DIGEST_INVALID"),
            "artifact": artifact[:500],
        })
    return {
        "status": "READY",
        "head_sha": exact_sha,
        "preview_digest": exact_digest,
        "url": cleaned_url[:1000] if cleaned_url else None,
        "screenshots": safe_screenshots,
        "created_at": created_at or _iso(),
        "valid_until": valid_until,
    }


def record_owner_acceptance(
    *, brief_id: str, decision: str, head_sha: str, preview_digest: str,
    actor: str | None = None, authority: str = "OWNER", nonce: str | None = None,
    source: str = "LOCAL_AUTHENTICATED", provider_readback: bool = False,
    policy_hash: str | None = None, decided_by: str | None = None,
    note_ru: str = "", now: datetime | None = None,
) -> dict[str, Any]:
    """Record an authenticated exact-SHA owner decision. Legacy decided_by is identity only."""
    status = str(decision).upper()
    if status not in {"ACCEPTED", "CHANGES_REQUESTED", "DEFERRED"}:
        raise ValueError("OWNER_DECISION_INVALID")
    if BRIEF_ID.fullmatch(str(brief_id)) is None:
        raise ValueError("OWNER_BRIEF_ID_INVALID")
    identity = str(actor or decided_by or "").strip()
    if len(identity) < 2:
        raise ValueError("OWNER_IDENTITY_MISSING")
    if str(authority).upper() != "OWNER":
        raise ValueError("OWNER_AUTHORITY_INVALID")
    nonce_text = str(nonce or "").strip()
    if len(nonce_text) < 16:
        raise ValueError("OWNER_NONCE_MISSING")
    if source not in {"LOCAL_AUTHENTICATED", "GITHUB_AUTHENTICATED"}:
        raise ValueError("OWNER_SOURCE_UNTRUSTED")
    if source == "GITHUB_AUTHENTICATED" and provider_readback is not True:
        raise ValueError("OWNER_PROVIDER_READBACK_REQUIRED")
    if not policy_hash or len(str(policy_hash)) != 64:
        raise ValueError("OWNER_POLICY_HASH_REQUIRED")
    safe_identity, _ = sanitize_text(identity)
    safe_note, _ = sanitize_text(str(note_ru).strip())
    return {
        "status": status, "brief_id": str(brief_id),
        "head_sha": _require_sha(head_sha, "OWNER_ACCEPTANCE_SHA_INVALID"),
        "preview_digest": _require_digest(preview_digest, "OWNER_ACCEPTANCE_PREVIEW_INVALID"),
        "policy_hash": str(policy_hash), "authority": "OWNER", "nonce": nonce_text[:200],
        "source": source, "provider_readback": bool(provider_readback),
        "decided_at": _iso(now), "decided_by": safe_identity[:120],
        "note_ru": safe_note[:2000], "stale_reason": None,
    }


def evaluate_owner_acceptance(
    acceptance: dict[str, Any] | None, *, current_head_sha: str | None,
    current_preview_digest: str | None,
) -> dict[str, Any]:
    """Новый commit или preview делает прежнее решение STALE."""
    if not acceptance:
        return {
            "status": "PENDING", "brief_id": None, "head_sha": None,
            "preview_digest": None, "decided_at": None, "decided_by": None,
            "note_ru": "", "stale_reason": None,
        }
    result = copy.deepcopy(acceptance)
    if result.get("status") not in ACCEPTANCE_STATES:
        result["status"] = "STALE"
        result["stale_reason"] = "ACCEPTANCE_STATUS_INVALID"
        return result
    if result.get("status") in {"NOT_REQUIRED", "PENDING"}:
        return result
    reasons = []
    if result.get("head_sha") != current_head_sha:
        reasons.append("HEAD_SHA_CHANGED")
    if result.get("preview_digest") != current_preview_digest:
        reasons.append("PREVIEW_DIGEST_CHANGED")
    if reasons:
        result["status"] = "STALE"
        result["stale_reason"] = ",".join(reasons)
    return result


def render_human_changelog(
    changes: list[dict[str, Any]], *, limitations_ru: list[str] | None = None,
    owner_action_ru: str | None = None, preview_url: str | None = None,
) -> str:
    """Собрать reader-oriented changelog из структурированных фактов."""
    normalized = []
    for item in changes:
        if not isinstance(item, dict):
            raise ValueError("CHANGELOG_ITEM_INVALID")
        summary = str(item.get("summary_ru") or "").strip()
        verification = str(item.get("verification_ru") or "").strip()
        value = str(item.get("value_ru") or "").strip()
        if len(summary) < 5 or len(verification) < 5:
            raise ValueError("CHANGELOG_FACTS_INCOMPLETE")
        normalized.append({"summary": summary, "verification": verification, "value": value})
    if not normalized:
        normalized = [{
            "summary": "Выполнено внутреннее техническое обновление.",
            "verification": "Результат требует проверки в целевом проекте.",
            "value": "Пользовательские изменения не заявлены.",
        }]
    lines = ["## Что изменилось", ""]
    lines.extend(f"- {item['summary']}" for item in normalized)
    lines.extend(["", "## Что проверено", ""])
    lines.extend(f"- {item['verification']}" for item in normalized)
    lines.extend(["", "## Что это даёт", ""])
    values = [item["value"] for item in normalized if item["value"]]
    lines.extend([f"- {item}" for item in values] or ["- Изменение не влияет на привычный сценарий владельца."])
    lines.extend(["", "## Известные ограничения", ""])
    lines.extend([f"- {item}" for item in (limitations_ru or [])] or ["- Не зарегистрированы."])
    lines.extend(["", "## Нужно ли ваше действие", "", f"- {owner_action_ru or 'Нет.'}"])
    if preview_url:
        lines.extend(["", "## Посмотреть результат", "", f"- {preview_url}"])
    return "\n".join(lines) + "\n"


def _machine_verified(state: dict[str, Any], preview: dict[str, Any] | None) -> str:
    if not preview:
        return "NOT_VERIFIED"
    main_sha = state.get("main", {}).get("head")
    if preview.get("head_sha") != main_sha:
        return "STALE"
    snapshot = state.get("assurance_snapshot")
    if not isinstance(snapshot, dict):
        return "NOT_VERIFIED"
    return assurance_machine_verified(snapshot, expected_sha=str(main_sha or ""))


def ceo_control_center_projection(state: dict[str, Any]) -> dict[str, Any]:
    experience = state.get("owner_experience") or {}
    preview = experience.get("current_preview") if isinstance(experience.get("current_preview"), dict) else None
    acceptance = evaluate_owner_acceptance(
        experience.get("acceptance") if isinstance(experience.get("acceptance"), dict) else None,
        current_head_sha=state.get("main", {}).get("head"),
        current_preview_digest=preview.get("preview_digest") if preview else None,
    )
    product_health = state.get("health", {}).get("product", "NOT_VERIFIED")
    product_status = {
        "VERIFIED": "Продукт проверен",
        "HEALTHY": "Продукт работает стабильно",
        "DEGRADED": "Есть ограничение, основная работа продолжается",
        "BROKEN": "Обнаружена проблема, новые изменения остановлены",
        "STALE": "Нужна повторная проверка продукта",
    }.get(product_health, "Состояние продукта ещё не подтверждено")
    incidents = state.get("incident_knowledge") or {}
    healing = state.get("safe_healing") or {}
    decision = {
        "ACCEPTED": "Результат принят; дополнительных действий не требуется.",
        "CHANGES_REQUESTED": "ИИ должен учесть замечание и показать новую версию.",
        "DEFERRED": "Решение отложено владельцем.",
        "STALE": "Показать владельцу свежий результат для повторной приёмки.",
        "NOT_REQUIRED": "Приёмка владельца для этого изменения не требуется.",
    }.get(acceptance["status"], "Откройте результат. Если всё устраивает, нажмите «ПРОДОЛЖИТЬ»; при проблеме Portal предложит простой выбор.")
    return {
        "product_status_ru": product_status,
        "what_changed_ru": experience.get("release_summary_ru") or "Понятное резюме результата ещё не подготовлено.",
        "preview_status": (
            "STALE" if preview and preview.get("head_sha") != state.get("main", {}).get("head")
            else preview.get("status") if preview else "NOT_VERIFIED"
        ),
        "preview_url": preview.get("url") if preview else None,
        "preview_head_sha": preview.get("head_sha") if preview else None,
        "machine_verified": _machine_verified(state, preview),
        "owner_acceptance": acceptance["status"],
        "owner_decision_ru": decision,
        "open_incidents": incidents.get("open_count", 0),
        "repeated_incidents": incidents.get("repeated_count", 0),
        "healing_status": healing.get("status", "NOT_CONFIGURED"),
        "healing_level": healing.get("level"),
        "circuit_open": bool(healing.get("circuit_open", False)),
    }


def render_ceo_control_center(state: dict[str, Any]) -> str:
    view = ceo_control_center_projection(state)
    preview = view["preview_url"] or "Предпросмотр пока не создан"
    level = view["healing_level"] or "NONE"
    return "\n".join([
        "## Продукт для владельца",
        "",
        f"- **Сейчас:** {view['product_status_ru']}.",
        f"- **Что изменилось:** {view['what_changed_ru']}",
        f"- **Посмотреть результат:** {preview}",
        f"- **Проверка автоматикой:** `{view['machine_verified']}`",
        f"- **Решение владельца:** `{view['owner_acceptance']}` — {view['owner_decision_ru']}",
        f"- **Инциденты:** открытых `{view['open_incidents']}`, повторных `{view['repeated_incidents']}`",
        f"- **Самовосстановление:** `{view['healing_status']}` / уровень `{level}` / circuit breaker `{'OPEN' if view['circuit_open'] else 'CLOSED'}`",
        "",
    ])
