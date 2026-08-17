# Consumer Instruction Contract v1

## Назначение

`CONSUMER_INSTR-001` отделяет **унифицированную методику AI-разработки** от **реальных ограничений конкретного продукта**. Цель — не поддерживать независимый монолитный `AGENTS.md` в каждом consumer и не копировать self-host правила ADWF в продукт, а собирать effective instructions из явных слоёв с разным ownership.

## Слои

| Layer | Canonical source | Ownership | Назначение |
|---|---|---|---|
| `FRAMEWORK_CORE` | `.adwf/instructions/CORE.md` | `FRAMEWORK_PRIVATE` | generic fail-closed development/governance rules |
| `PROJECT_PACK` | selected Project Pack из consumer profile | `FRAMEWORK_PRIVATE` | stack/class-specific execution and safety declarations |
| `CONSUMER_INVARIANTS` | `.adwf-consumer/INVARIANTS.md` | `CONSUMER_OWNED` | privacy/data/business/financial/architecture/irreversible-action constraints конкретного продукта |
| repository router | root `AGENTS.md` | `SHARED_GUARDED`, consumer-preserved | короткая точка входа в слои и fresh provider/runtime discovery |

Machine-readable binding хранится в `.adwf/consumer-instruction-policy.json` и валидируется схемой `.adwf/schemas/consumer-instruction-policy.schema.json`.

## Live state не является durable instruction

Текущий Issue, writer, lease, branch, candidate SHA, merge state, CI result и runtime status должны читаться заново из provider/runtime state. Они не должны закрепляться в root router как постоянная инструкция.

Template `.adwf/instructions/AGENTS_ROUTER.template.md` использует contract marker `ADWF_CONSUMER_ROUTER_V1` и направляет AI к framework core, consumer invariants и live provider/runtime truth. Deterministic validator отклоняет новый/migrated router, если отсутствуют обязательные markers или в нём явно материализованы `CURRENT_WRITER`, `CURRENT_TASK` либо `CURRENT_SHA`.

## Consumer invariants

`.adwf-consumer/INVARIANTS.md` намеренно **не входит** в framework package inventory. ADWF package/upgrade/detach не получает write/delete authority над этим path. Попытка включить его в target framework package считается invalid instruction ownership и блокирует upgrade до write. Если consumer уже материализовал этот path, planning/apply принимают только обычный regular file через реальную directory parent-chain; symlink/non-directory в любом parent component, symlink на конечном path, directory или другой неоднозначный object type блокируется до upgrade write.

Consumer invariants должны содержать только долгоживущие product-specific ограничения. Общие правила Git/CI/writer lifecycle, generic fail-closed semantics и текущий operational state туда не дублируются.

## Legacy root `AGENTS.md` transition

Ранее подключённые consumer repositories могут иметь pre-existing root `AGENTS.md`, который ADWF уже пометил `managed_by_adwf=false` и сохранил отдельным `preserved_sha256`. Source A при этом может быть legacy revision, в котором Consumer Instruction Contract ещё отсутствует; authority для bounded transition берётся только из exact target B policy. В таком случае изменение **self-host/package bytes** `AGENTS.md` между source A и target B не должно само по себе требовать новой overwrite authority, потому что consumer bytes всё равно не записываются.

Это исключение узкое и разрешено только если target instruction policy валиден и одновременно выполняется всё:

- exact path равен policy router path `AGENTS.md`;
- source и target ownership остаются `SHARED_GUARDED`;
- target path продолжает существовать;
- router mode = `CONSUMER_PRESERVED`;
- source snapshot доказывает `managed_by_adwf=false`;
- current consumer bytes совпадают с sealed `preserved_sha256`.

Planner сохраняет action `PRESERVE_PREEXISTING`; transactional apply/rollback/retry только проверяют preserved bytes и никогда не записывают их.

Это **не** общее послабление `SHARED_GUARDED`: изменение pre-existing `README.md`, `SECURITY.md`, `.gitignore` или другого shared path по-прежнему даёт прежний `HUMAN_REQUIRED`, если отдельный contract не определяет иной bounded semantic transition.

## Migration boundary

Legacy monolithic consumer router может быть сохранён во время framework upgrade как доказанный consumer preimage, но это не делает его рекомендуемой конечной структурой. Его последующая декомпозиция на compact router + `.adwf-consumer/INVARIANTS.md` является consumer-owned migration и должна выполняться отдельным bounded work item с сохранением всех реальных product invariants.

Framework upgrade сам по себе не переписывает legacy consumer router.

## Fail-closed cases

До write блокируются как минимум:

- missing/tampered instruction policy или schema;
- framework core отсутствует в package либо ошибочно shared;
- router отсутствует в package либо перестал быть `SHARED_GUARDED`;
- `.adwf-consumer/INVARIANTS.md` появился в framework package/shared inventory;
- layer path collision;
- symlink/type ambiguity или consumer drift;
- router ownership transition;
- попытка распространить legacy-router exception на другой shared path.

## Evidence boundary

Unit/adversarial/CI evidence доказывает implementation этого контракта, но не означает, что конкретный consumer уже мигрировал свой монолитный router или что connected upgrade capability автоматически стал `LIVE_VERIFIED`. Реальный consumer должен пройти отдельный exact-SHA provider lifecycle и, при необходимости, отдельную consumer-owned instruction migration.
