"""Единый исполняемый policy engine ADWF v1.6.

Правила могут исполняться только из проверенного Effective Policy IR. Встроенные
константы остаются bootstrap/fail-closed fallback для проверки самого компилятора
и обратной совместимости локальных pure-function tests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

AUTONOMY_RANK = {f"A{i}": i for i in range(5)}
RISK_RANK = {f"R{i}": i for i in range(5)}
SAFE_HEALTH = {"VERIFIED", "HEALTHY"}
PASS = "PASS"

ACTION_MIN_AUTONOMY = {
    "inspect": "A0",
    "plan": "A0",
    "reconcile": "A0",
    "edit": "A1",
    "test": "A1",
    "continue": "A1",
    "review": "A1",
    "verify": "A1",
    "observe": "A1",
    "record_incident": "A1",
    "commit": "A2",
    "push": "A2",
    "open_pr": "A2",
    "claim": "A2",
    "repair": "A2",
    "cleanup": "A2",
    "merge": "A3",
    "deploy_dev": "A3",
    "promote": "A3",
    "rollback": "A3",
    "close_issue": "A3",
    "deploy_prod": "A4",
    "delete": "A4",
    "trust_change": "A4",
    "owner_accept": "A4",
    "certify_recipe": "A4",
}

REASON_RU = {
    "UNKNOWN_ACTION": "Действие не описано политикой.",
    "INVALID_AUTONOMY": "Уровень автономности неизвестен.",
    "INVALID_RISK": "Класс риска неизвестен.",
    "UNKNOWN_OR_PAID_PROVIDER": "Provider неизвестен, отключён или потенциально платный.",
    "NON_ZERO_COST": "Операция может создать денежные расходы.",
    "HEALTH_NOT_SAFE": "Обязательный контур здоровья не подтверждён.",
    "PRODUCT_HEALTH_BLOCKS_FEATURE": "Состояние продукта не разрешает feature-разработку.",
    "WRITER_CONFLICT": "Обнаружен конфликт Writer/lease.",
    "GATE_NOT_PASS": "Обязательная проверка не имеет свежего PASS.",
    "SHA_NOT_EXACT": "Проверка относится не к текущему commit SHA.",
    "EVIDENCE_NOT_FRESH": "Evidence отсутствует или устарел.",
    "AUTONOMY_TOO_LOW": "Текущий уровень автономности не разрешает действие без владельца.",
    "RISK_ABOVE_CEILING": "Риск выше разрешённого автономного потолка.",
    "R4_OR_TRUST_CHANGE": "Критический риск или изменение trust boundary требует владельца.",
    "DESTRUCTIVE_ACTION": "Разрушительное действие требует отдельного разрешения владельца.",
    "PRODUCTION_DEPLOY": "Production deploy требует отдельного разрешения владельца.",
    "A4_NEVER_AUTOMATIC": "A4 запрещено включать автоматически.",
    "POLICY_IR_INVALID": "Исполняемая политика отсутствует, повреждена или не соответствует контракту.",
    "POLICY_HASH_MISMATCH": "Решение относится не к текущей исполняемой политике.",
    "PROVIDER_FACTS_NOT_FRESH": "Сведения о стоимости или возможностях provider устарели.",
    "OWNER_ACCEPTANCE_NOT_EXACT": "Решение владельца не относится к текущему preview и commit.",
}


@dataclass(frozen=True)
class DecisionContext:
    action: str
    autonomy: str = "A1"
    risk: str = "R0"
    max_autonomous_risk: str = "R1"
    work_type: str = "feature"
    health: dict[str, str] = field(default_factory=dict)
    gates: dict[str, str] = field(default_factory=dict)
    required_gates: tuple[str, ...] = ()
    exact_sha: bool = False
    evidence_fresh: bool = False
    human_approved: bool = False
    destructive: bool = False
    trust_change: bool = False
    writer_conflict: bool = False
    provider_allowed: bool = True
    provider_potentially_paid: bool = False
    projected_cost: float = 0.0
    expected_policy_hash: str | None = None
    provider_facts_fresh: bool = True
    owner_acceptance_exact: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DecisionContext":
        payload = dict(value)
        unknown = set(payload) - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError("UNKNOWN_DECISION_CONTEXT_FIELDS:" + ",".join(sorted(unknown)))
        payload["required_gates"] = tuple(payload.get("required_gates", ()))
        return cls(**payload)


@dataclass(frozen=True)
class Decision:
    result: str
    reason_codes: tuple[str, ...]
    message_ru: str
    policy_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decision(result: str, reasons: list[str], policy_hash: str | None = None) -> Decision:
    unique = tuple(dict.fromkeys(reasons))
    message = "Разрешено политикой." if not unique else " ".join(REASON_RU.get(code, code) for code in unique)
    return Decision(result, unique, message, policy_hash)


def _validated_rules(policy_ir: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    if policy_ir is None:
        return {
            "action_min_autonomy": ACTION_MIN_AUTONOMY,
            "autonomy_rank": AUTONOMY_RANK,
            "risk_rank": RISK_RANK,
            "safe_health": sorted(SAFE_HEALTH),
            "non_mutating_actions": ["inspect", "plan", "reconcile", "review", "verify", "observe"],
            "control_plane_optional_actions": ["edit", "test"],
            "product_health_exempt_work_types": ["recovery", "verification", "governance"],
            "evidence_actions": ["merge", "deploy_dev", "deploy_prod", "promote", "rollback", "close_issue"],
            "always_human_actions": ["delete", "deploy_prod", "trust_change", "owner_accept", "certify_recipe"],
        }, None
    required = {
        "action_min_autonomy", "autonomy_rank", "risk_rank", "safe_health",
        "non_mutating_actions", "control_plane_optional_actions",
        "product_health_exempt_work_types", "evidence_actions", "always_human_actions",
    }
    rules = policy_ir.get("rules")
    policy_hash = policy_ir.get("policy_hash")
    if not isinstance(rules, dict) or set(rules) != required:
        raise ValueError("POLICY_IR_INVALID")
    if not isinstance(policy_hash, str) or len(policy_hash) != 64:
        raise ValueError("POLICY_IR_INVALID")
    if rules.get("action_min_autonomy") != ACTION_MIN_AUTONOMY:
        raise ValueError("POLICY_IR_INVALID")
    if rules.get("autonomy_rank") != AUTONOMY_RANK or rules.get("risk_rank") != RISK_RANK:
        raise ValueError("POLICY_IR_INVALID")
    return rules, policy_hash


def evaluate_permission(context: DecisionContext, policy_ir: dict[str, Any] | None = None) -> Decision:
    """Вернуть ALLOW/BLOCK/HUMAN_REQUIRED. Неизвестное всегда закрывает контур."""
    try:
        rules, policy_hash = _validated_rules(policy_ir)
    except (TypeError, ValueError):
        return _decision("BLOCK", ["POLICY_IR_INVALID"])
    autonomy_rank = rules["autonomy_rank"]
    risk_rank = rules["risk_rank"]
    action_min = rules["action_min_autonomy"]
    safe_health = set(rules["safe_health"])
    reasons: list[str] = []
    if context.action not in action_min:
        return _decision("BLOCK", ["UNKNOWN_ACTION"], policy_hash)
    if context.autonomy not in autonomy_rank:
        return _decision("BLOCK", ["INVALID_AUTONOMY"], policy_hash)
    if context.risk not in risk_rank or context.max_autonomous_risk not in risk_rank:
        return _decision("BLOCK", ["INVALID_RISK"], policy_hash)
    if context.expected_policy_hash is not None and context.expected_policy_hash != policy_hash:
        reasons.append("POLICY_HASH_MISMATCH")
    if not context.provider_facts_fresh:
        reasons.append("PROVIDER_FACTS_NOT_FRESH")
    if not context.provider_allowed or context.provider_potentially_paid:
        reasons.append("UNKNOWN_OR_PAID_PROVIDER")
    if context.projected_cost > 0:
        reasons.append("NON_ZERO_COST")
    if reasons:
        return _decision("BLOCK", reasons, policy_hash)

    mutation = context.action not in set(rules["non_mutating_actions"])
    if mutation:
        required_health = ["package_integrity", "config_health"]
        if context.action not in set(rules["control_plane_optional_actions"]):
            required_health.append("control_plane_health")
        for name in required_health:
            if context.health.get(name) not in safe_health:
                reasons.append("HEALTH_NOT_SAFE")
                break
        if context.work_type not in set(rules["product_health_exempt_work_types"]) and context.health.get("product_health") not in safe_health:
            reasons.append("PRODUCT_HEALTH_BLOCKS_FEATURE")
    if context.writer_conflict:
        reasons.append("WRITER_CONFLICT")
    if context.action in set(rules["evidence_actions"]):
        if any(context.gates.get(name) != PASS for name in context.required_gates):
            reasons.append("GATE_NOT_PASS")
        if not context.exact_sha:
            reasons.append("SHA_NOT_EXACT")
        if not context.evidence_fresh:
            reasons.append("EVIDENCE_NOT_FRESH")
    if reasons:
        return _decision("BLOCK", reasons, policy_hash)

    human_reasons: list[str] = []
    if context.destructive or context.action == "delete":
        human_reasons.append("DESTRUCTIVE_ACTION")
    if context.action == "deploy_prod":
        human_reasons.append("PRODUCTION_DEPLOY")
    if context.trust_change or context.risk == "R4" or context.action == "trust_change":
        human_reasons.append("R4_OR_TRUST_CHANGE")
    if context.action == "owner_accept" and not context.owner_acceptance_exact:
        human_reasons.append("OWNER_ACCEPTANCE_NOT_EXACT")
    if context.autonomy == "A4":
        human_reasons.append("A4_NEVER_AUTOMATIC")
    if risk_rank[context.risk] > risk_rank[context.max_autonomous_risk]:
        human_reasons.append("RISK_ABOVE_CEILING")
    required = action_min[context.action]
    if autonomy_rank[context.autonomy] < autonomy_rank[required]:
        human_reasons.append("AUTONOMY_TOO_LOW")
    if required == "A4" or context.action in set(rules["always_human_actions"]):
        human_reasons.append("A4_NEVER_AUTOMATIC")
    if human_reasons and not context.human_approved:
        return _decision("HUMAN_REQUIRED", human_reasons, policy_hash)
    return _decision("ALLOW", [], policy_hash)
