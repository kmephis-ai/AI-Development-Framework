# Decision / Requirement Traceability v1

TRACE-001 добавляет ADWF долговременную «память причин», но не создаёт вторую базу истины для уже существующих подсистем.

## Что является canonical truth

`.adwf/decision-requirement-traceability.json` хранит только то, чего раньше не было как first-class durable contract: ссылку на Owner Intent, Requirement и архитектурное/продуктовое Decision, а также typed edges между ними и внешними canonical identities.

Граф **не копирует** содержимое Capability Truth, Roadmap, `AIWorkPackage`/`AIWorkResult` или Evidence Graph. Для них он хранит только проверяемые references. Approval decision из `GitHubOwnerDecisionStore` тоже не равен архитектурному Decision: SHA-bound Owner-Attestation остаётся отдельным trust-boundary механизмом.

## Как читать цепочку

Владелец и AI должны уметь пройти путь: «зачем это вообще понадобилось → какое требование возникло → какое решение принято → какую capability оно создаёт → какой work unit это реализует → каким evidence подтверждён результат».

Canonical TRACE-001 chain сейчас заканчивается на `WORKREF-TRACE-001`. Это **намеренно** даёт `TRACE COVERAGE: INCOMPLETE`: отсутствие post-merge evidence нельзя превращать в VERIFIED только потому, что код и тесты локально PASS.

## Supersession вместо тихого переписывания

Опубликованные records, references и edges immutable между revisions. Новое Requirement или Decision добавляется новой записью с большей `version` и typed supersession edge на предыдущую запись. Старый объект остаётся в истории. Две конкурирующие замены одного record блокируются как ambiguous.

Такое правило позволяет восстановить не только текущий ответ, но и историю того, **почему** решение изменилось.

## Fail-closed validation

`validate_traceability.py` блокирует duplicate/dangling nodes, неверные endpoint types, self-reference/cycles, неизвестные capability/roadmap references, повреждённые digests, подмену source документа Owner Intent, illegal/ambiguous supersession и изменение уже опубликованных объектов.

Evidence reference считается verified только если существует в валидном append-only Evidence Graph, имеет `PASS`, совпадает по `subject` и exact SHA и не истёк. Строка с похожим ID или локальный claim результата evidence не заменяет.

## Статусы projection

`INCOMPLETE` означает, что chain структурно не доведена до evidence. `STRUCTURED_NOT_VERIFIED` означает, что evidence edge уже есть, но live evidence отсутствует, stale, forged или не PASS. Только `VERIFIED` означает полный typed path с валидным downstream evidence.

## Граница v1

TRACE-001 не меняет owner approval semantics, orchestration, provider policy, rulesets или required checks. Consumer Lifecycle, Managed Surfaces, detach/recovery и историческая массовая миграция остаются за пределами этого work unit.
