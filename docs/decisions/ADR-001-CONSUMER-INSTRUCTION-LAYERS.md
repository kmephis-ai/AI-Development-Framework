# ADR-001 — Layered consumer AI instructions

## Status

Accepted by owner on 2026-08-17; implementation tracked by `CONSUMER_INSTR-001` / GitHub Issue #132.

## Context

Первый real connected PrihRash upgrade обнаружил `HUMAN_REQUIRED` только из-за того, что pre-existing consumer-owned root `AGENTS.md` сохранялся byte-identical, а package copy ADWF изменилась между revisions. Первоначально рассматривалась exact preservation authority (#130), но owner review признал это неверной abstraction: она сохраняла бы монолитные, постепенно расходящиеся project rules вместо унификации developer-governance.

## Decision

ADWF определяет generic `FRAMEWORK_CORE` и Project Pack rules. Каждый consumer хранит отдельно только свои durable `CONSUMER_INVARIANTS`. Root `AGENTS.md` становится consumer-preserved compact router. Current task/writer/SHA/runtime state всегда получается fresh из provider/runtime и не является durable instruction content.

Framework upgrade не получает authority переписывать consumer invariants или preserved router. Изменение package/self-host `AGENTS.md` не требует human authority для уже доказанного pre-existing router, если target instruction policy подтверждает неизменную shared-preserved semantic role; остальные shared-path transitions остаются fail-closed.

## Consequences

- `UPGRADE-AUTH-001` / #130 закрыт как superseded wrong abstraction до появления candidate code.
- Новые consumers получают единый development-governance слой ADWF без копирования monolithic project playbooks.
- Legacy consumers требуют отдельной bounded migration из монолитного `AGENTS.md` в compact router + consumer invariants; upgrade не выполняет эту миграцию скрыто.
