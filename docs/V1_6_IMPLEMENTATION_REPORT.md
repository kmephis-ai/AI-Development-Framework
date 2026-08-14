# ADWF v1.6 — remediation / implementation traceability

**Цель релиза:** закрыть все code/integration findings независимого аудита v1.5, не маскируя отсутствие живого GitHub как локальный `VERIFIED`.

## Finding → исправление

| Аудит v1.5 | v1.6 remediation | Package-level статус |
|---|---|---|
| P0 Full Loop не замкнут | `ActionExecutorRegistry` для всех durable phases + исполняющий `RuntimeSupervisor`; creative phases имеют explicit `WAITING_AGENT`, если adapter не настроен | IMPLEMENTED / optional agent boundary |
| P0 Owner Intent не будит hosted cycle | `OwnerIntentService` создаёт Issue/checkpoint и `ControllerWakeupPort` dispatch | IMPLEMENTED; live provider не проверен локально |
| P0 trusted-gate self-attestation | default-branch diff classifier + `adwf/governance-gate` + exact-HEAD separate admin approval для trust changes | IMPLEMENTED |
| P0 несколько SSOT | Durable Orchestrator authoritative; Work Memory = private handoff; Project State/Dashboard/labels = projections; legacy orchestrator удалён из production workflow | IMPLEMENTED |
| P0 новая задача портит active Brief | active-run check выполняется до mutation и под cross-platform owner-intent lock; новая задача queue-only | IMPLEMENTED |
| P0 ПРОДОЛЖИТЬ не продолжает | `OwnerAuthorityAdapter` пишет exact phase result, принудительно обновляет provider owner readback даже при том же SHA, запускает Supervisor и dispatch | IMPLEMENTED |
| P0 Preview не доказывает revision | local exact git HEAD; hosted PR marker проходит trusted provider-log bridge только после exact trusted/governance gates; remote path требует deployment readback | IMPLEMENTED; live hosted readback не проверен локально |
| P0 Auto Release противоречит версии | top-level `adwf release --auto`; transactional version-bump prepare; external build читает internal version; mismatch блокируется | IMPLEMENTED |
| P1 Project Packs proposal-only | pack materialization + governance bootstrap PR + idempotent readback | IMPLEMENTED |
| P1 Public Runtime Ledger privacy/anchor | safe projection без raw task/Work Memory/stderr + provider object binding + protected annotated-tag anchors | IMPLEMENTED; live tag ruleset не проверен локально |
| P1 Ruleset verifier incomplete | no bypass, PR/force-push/delete, exact checks, strict mode, single GitHub Actions integration id, staged seed checks | IMPLEMENTED; live ruleset не проверен локально |
| P1 Pipeline IR не SSOT | GitHub workflows теперь детерминированно генерируются из IR; drift блокируется; непайплайновые governance settings остаются canonical config, а не притворяются IR-owned | IMPLEMENTED в заявленном pipeline scope |
| P1 Performance Plane contract-only | trusted GitHub run/job collector; queue/execution/TTFF/flake/superseded cancellation; window; per-impact + per-pack evidence | IMPLEMENTED; достаточная live sample window отсутствует локально |
| P1 Agent return/wakeup отсутствует | bounded Agent Inbox request/result, strict binding, consume → supervisor wakeup | IMPLEMENTED / optional agent adapter |
| P1 Delivery/observation adapters отсутствуют | `REFERENCE_LOCAL` contract + strict `COMMAND` exact-SHA structured attestation; exit code alone не evidence | IMPLEMENTED / product adapter boundary |
| P1 Windows certification | generated Linux/Windows hosted functional HTTP smoke | LIVE_NOT_VERIFIED до GitHub |

## Дополнительные integrity fixes, найденные при реализации v1.6

1. **Stale owner readback:** same-SHA Assurance/Provider cache теперь принудительно обновляется на `OWNER_ACCEPTANCE`/`MERGE`.
2. **Preview cross-run gap:** одноразовый PR runner и trusted controller связаны через GitHub job-log readback; log marker не считается evidence без trusted/governance exact-HEAD checks.
3. **CI app-source split:** CI executor требует все три required checks от одного GitHub Actions app id и реальные evidence refs.
4. **Delivery false-positive:** `COMMAND` adapter больше не может подтвердить promotion/observation одним `exit 0`.
5. **Concurrent owner intent:** создание active run/queue защищено единым cross-platform lock.
6. **Capability drift:** machine-readable `.adwf/capability-traceability.json` + `validate_capabilities.py` связывают claims с production wiring и live boundaries.

## Архитектурная связность

Канонический путь v1.6:

`Owner Intent → Durable Orchestrator → Runtime Supervisor → ActionExecutorRegistry → Provider/Agent/Preview/Owner/Delivery adapters → Trusted Context → durable transition → Executive projections`.

Creative Agent намеренно остаётся заменяемым. Без configured adapter framework возвращает `WAITING_AGENT`, а не симулирует автономную генерацию кода. Production deploy также project-specific: отсутствие required adapter блокирует, а `REFERENCE_LOCAL` никогда не выставляет `production_verified=true`.

## Capability truth

См. `.adwf/capability-traceability.json`. `IMPLEMENTED` означает наличие production path и package verification; `LIVE_NOT_VERIFIED` означает, что code path готов, но эксплуатационный факт требует внешнего provider run; `OPTIONAL_ADAPTER` означает, что generic contract подключён, но конкретный AI/deployment provider выбирается проектом.

## Что локальный пакет принципиально не может доказать

- фактическое создание/readback GitHub rulesets;
- реальный GitHub-hosted Windows job;
- реальный GitHub check app/integration id;
- поведение `workflow_run`/job-log API в конкретном repository;
- production deployment конкретного продукта;
- длительную performance sample window и 30-дневную эксплуатацию.

Поэтому package tests не заменяют live provider evidence. `Control Plane` и `Product Health` обязаны оставаться `NOT_VERIFIED` до будущего переноса в GitHub и reference cycle.
