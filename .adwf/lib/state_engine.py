"""Единственная машина переходов ADWF."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TransitionDecision:
    result: str
    from_state: str
    to_state: str
    reason_codes: tuple[str, ...]
    missing_preconditions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_transition(
    item: dict[str, Any],
    to_state: str,
    machine: dict[str, Any],
    predicates: dict[str, Any],
    *,
    expected_state: str | None = None,
) -> TransitionDecision:
    current = str(item.get("state", "UNKNOWN"))
    states = set(machine.get("states", []))
    if current not in states or to_state not in states:
        return TransitionDecision("BLOCK", current, to_state, ("UNKNOWN_STATE",))
    if expected_state is not None and current != expected_state:
        return TransitionDecision("BLOCK", current, to_state, ("EXPECTED_STATE_MISMATCH",))
    if to_state not in machine.get("transitions", {}).get(current, []):
        return TransitionDecision("BLOCK", current, to_state, ("TRANSITION_NOT_ALLOWED",))
    key = f"{current}->{to_state}"
    required = tuple(machine.get("preconditions", {}).get(key, []))
    missing = tuple(name for name in required if predicates.get(name) is not True)
    if missing:
        return TransitionDecision("BLOCK", current, to_state, ("PRECONDITION_NOT_TRUE",), missing)
    return TransitionDecision("ALLOW", current, to_state, ())


def apply_transition(
    item: dict[str, Any],
    to_state: str,
    machine: dict[str, Any],
    predicates: dict[str, Any],
    *,
    expected_state: str | None = None,
) -> tuple[dict[str, Any], TransitionDecision]:
    decision = evaluate_transition(item, to_state, machine, predicates, expected_state=expected_state)
    if decision.result != "ALLOW":
        return dict(item), decision
    updated = dict(item)
    updated["state"] = to_state
    return updated, decision

