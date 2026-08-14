# ADWS v1.6 — стандарт Executive Autopilot

## 1. Роли

**Владелец** определяет полезность, приоритет, публикацию, юридические условия и необратимые решения. **Creative Agent** предлагает/создаёт изменения, но не выдаёт себе PASS. **Trusted Controller** исполняется из default-branch code и независимо читает provider facts. **Durable Orchestrator** — единственный workflow SSOT.

## 2. Канонический цикл

<!-- adwf-doc: skip(reason=state-machine-summary) -->
```text
RECONCILE → AUTHORIZE → CLAIM → WORKSPACE → EXECUTE → OPEN_PR → CI → REVIEW → PREVIEW → OWNER_ACCEPTANCE → MERGE → PROMOTE → OBSERVE → DONE → CLEANUP → NEXT
                         ↘ RECOVERY при проверенном failure
```

Каждая фаза имеет один `ActionExecutorRegistry` executor или явный wait/human-required result. Silent skip запрещён.

## 3. Trust boundary

Код PR считается недоверенным, включая изменённые им workflow/evaluator. `fast-feedback` — сигнал от PR plane. `adwf/trusted-gate` публикуется default-branch controller только после API readback exact SHA. Trust-boundary diff требует `adwf/governance-gate` с отдельным exact-HEAD admin approval.

## 4. Evidence

Положительный факт нельзя передавать caller-полем. Trusted Context собирается из Effective Policy, AssuranceSnapshot, provider readback, cost/evidence и exact identity. Новый SHA или preview digest делает старое acceptance непригодным.

## 5. Состояние

Durable Orchestrator — authoritative state. Work Memory — private handoff context. Issue labels, Dashboard, Roadmap, Portfolio и project-state — projections с source run/revision. Legacy orchestration не выполняется production workflow.

## 6. Preview

Loopback capture должен выполняться из checkout, где `git rev-parse HEAD == subject_sha`. Remote preview требует provider readback `deployed_sha == subject_sha`. Manifest хранит screenshots, environment и digest; отсутствие этих доказательств = `NOT_VERIFIED`.

## 7. Release

`release --auto` определяет SemVer impact, но не строит архив с новой версией поверх старого source tree. Сначала version-bump transaction/PR, gates, owner acceptance и merge; затем build exact internal version, tag, release, provider readback.

## 8. FREE_ONLY

Mandatory AI/API calls = 0. Unknown/metered/paid/larger runners fail closed. Cache/artifact — ускоритель, не evidence.

## 9. Performance

Execution, queue, TTFF, flake и superseded cancellation измеряются отдельно. Provider queue не считается скоростью ADWF. Budget становится PASS только после достаточной выборки.

## 10. Честные N/A

Deploy/observe могут быть `N/A` только если project config **явно** объявляет их неприменимыми. Если deployment/observation required, но adapter отсутствует — `HUMAN_REQUIRED`, не PASS.
