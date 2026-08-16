# Consumer Framework Upgrade Planning v1

## Назначение

UPGRADE-001 добавляет в ADWF первый machine-verifiable слой обновления уже подключённого consumer project с **exact framework revision A** на **exact framework revision B**. Этот слой только анализирует совместимость и строит детерминированный dry-run plan. Он не изменяет consumer repository и не имеет режима apply.

Главный инвариант: consumer project должен переживать развитие самого ADWF. Новая версия framework не получает право автоматически присваивать существующие consumer-owned, pre-existing или shared-guarded данные.

## Truth boundary

Статус `READY` в Consumer Framework Upgrade Plan означает только то, что при переданных exact входах planning/compatibility слой не обнаружил блокирующих или human-required переходов. `READY` **не означает**, что cross-revision upgrade уже безопасно применим.

Безопасный framework upgrade будет доказан только отдельным transactional executor/recovery и реальным consumer evidence для последовательности A → B → rollback → B. Поэтому capability UPGRADE-001 остаётся `LIVE_NOT_VERIFIED` даже при зелёных unit/CI/adversarial tests.

## Exact входы

План привязан как минимум к следующим данным:

- exact source ADWF commit SHA A;
- exact target ADWF commit SHA B;
- exact consumer root identity;
- trusted Managed Surface snapshot/provenance от revision A;
- sealed current consumer profile;
- source/target Project Pack identity и digest;
- optional pinned Skill bindings, если поведение consumer зависит от конкретных governed skills;
- source/target package MANIFEST digests.

Source и target framework checkout должны быть clean Git worktrees на заявленных exact SHA. Floating `latest`, подмена SHA или dirty target/source запрещены fail-closed.

## Классификация путей

Planner строит A↔B diff по framework package inventory и отдельно сверяет фактическое состояние consumer.

| Класс | Смысл | Planning authority |
|---|---|---|
| `UNCHANGED_FRAMEWORK_PRIVATE` | Framework-private файл совпадает в A и B | keep exact |
| `MODIFY_FRAMEWORK_PRIVATE` | Framework-private файл меняется | replace planned только при exact provenance и отсутствии drift |
| `ADD_FRAMEWORK_PRIVATE` | B добавляет новый private path | create planned только если consumer path отсутствует |
| `REMOVE_FRAMEWORK_PRIVATE` | B удаляет managed private path | remove planned только при exact provenance |
| `SHARED_GUARDED` | Путь shared/pre-existing либо меняет ownership | preserve-only; изменение требует отдельной authority |

Если target пытается занять уже существующий consumer path, planner возвращает `BLOCK`. Symlink, non-file parent, traversal или другая path/type ambiguity также блокируется.

## Consumer drift и ownership

Каждый source-managed path проверяется против snapshot A. Если фактический consumer файл больше не равен exact installed digest, это drift и planner не пытается угадать намерение владельца.

Pre-existing managed entries сохраняются. Shared-guarded paths сохраняются. Новая версия ADWF не получает silent overwrite authority над такими путями даже тогда, когда target package содержит новый вариант файла.

## Compatibility contracts

UPGRADE-001 проверяет переходы для framework config schema, Consumer Profile schema, Managed Surface snapshot schema, Project Pack и optional Skill bindings.

Равные contract identities считаются совместимыми без миграции. Изменённая identity должна иметь единственную явную запись в `.adwf/consumer-upgrade-migrations.json`. Неизвестный переход становится `HUMAN_REQUIRED`; отсутствующая или невалидная migration registry блокирует анализ. Произвольные migration scripts не исполняются.

Несовместимый target Consumer Profile contract или tampered source profile блокируется как invalid planning input. Project Pack/Skill digest substitution без explicit migration record не превращается в PASS.

## Self-sealed evidence

Compatibility result и upgrade plan имеют собственные SHA-256 seals. Валидатор пересчитывает seal, derived status и exact binding между compatibility и plan. Подмена `PASS/READY`, SHA, entries, rollback prerequisites или findings после построения документа блокируется детерминированно.

Оба документа фиксируют `write_performed=false`.

## Rollback prerequisites

Хотя UPGRADE-001 ничего не применяет, plan заранее вычисляет минимальные prerequisites для будущего transactional rollback:

- exact source revision A должен оставаться доступен;
- source package MANIFEST должен оставаться доступен и проверяем;
- для заменяемых/удаляемых managed private paths фиксируются source digests, которые будущий executor обязан уметь восстановить;
- для новых target private paths фиксируются exact digests, которые будущий rollback сможет безопасно удалить только при отсутствии последующего drift.

Это prerequisite contract, а не реализация rollback executor.

## CLI contract

`.adwf/scripts/plan_consumer_upgrade.py` является read-only entrypoint. Он принимает source/target roots, exact revisions, consumer root, snapshot и optional Skill bindings и печатает JSON bundle из compatibility result и plan.

Exit code `0` разрешён только для `READY`. `HUMAN_REQUIRED`, `BLOCK` или invalid/tampered input возвращают non-zero и machine-readable BLOCK/HUMAN_REQUIRED result. CLI не содержит флага apply.

## Evidence strength

Focused adversarial tests проверяют stale source snapshot, substituted target revision, consumer drift, consumer-owned collision, path/type ambiguity, stale consumer profile, target profile schema incompatibility, missing migration record, private replacement rollback prerequisites, shared preservation, Project Pack/Skill substitution и forged seals.

Эти проверки доказывают implementation UPGRADE-001, но не live consumer upgrade. Следующий mutating work unit может быть материализован только после полной сертификации UPGRADE-001 и обязан сохранить этот truth boundary.
