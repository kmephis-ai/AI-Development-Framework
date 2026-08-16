# Managed Surface Contract v1

`LIFECYCLE-001` открывает release horizon **v1.8 Consumer Lifecycle & Portability** с минимального безопасного фундамента: ADWF должен знать не только какие файлы входят в его release package, но и какие из них он вправе считать своими внутри consumer repository.

Ключевой invariant:

> **PROJECT MUST OUTLIVE FRAMEWORK.**

Удаление или обновление ADWF не должно превращаться в риск удаления пользовательского кода, данных или документации.

## Два разных вопроса — два разных источника истины

`MANIFEST.json` и `SHA256SUMS.txt` уже являются каноническим SSOT package integrity. Они отвечают на вопрос:

**«Какие framework-owned файлы входят в этот release ADWF и каковы их хэши?»**

Managed Surface Contract **не копирует этот список**. `.adwf/managed-surface-policy.json` добавляет только consumer-ownership semantics и отвечает на другой вопрос:

**«Что ADWF вправе создать, считать своим, заменить или предлагать удалить внутри проекта-потребителя?»**

Так предотвращается второй inventory SSOT.

## Ownership classes

### `FRAMEWORK_PRIVATE`

Путь входит в package manifest и не помечен как shared.

При adoption:

- отсутствующий путь можно только **предложить создать**;
- существующий exact файл сохраняется, но без доказательства provenance не присваивается ADWF автоматически;
- существующий файл с другим содержимым блокирует adoption;
- symlink/non-file collision блокирует adoption.

При detach:

- удалить можно **только в плане** и только файл, который snapshot доказуемо пометил `managed_by_adwf=true`;
- current digest обязан точно совпадать с installed digest;
- любое изменение, symlink или смена типа объекта переводит путь в preserve/block.

### `SHARED_GUARDED`

Это package paths, которые типично могут уже принадлежать продукту: например `README.md`, `VERSION`, `CHANGELOG.md`, `.gitignore`, `.gitattributes`, `AGENTS.md`, `SECURITY.md`, `.gitlab-ci.yml`.

Они никогда не удаляются автоматически в v1. Даже если ADWF когда-то создал такой файл, detach plan возвращает `PRESERVE_SHARED`.

### `CONSUMER_OWNED`

Default для любого пути, которого нет в package manifest.

Такой путь вообще не входит в managed deletion plan. Код приложения, данные, `package.json`, `pyproject.toml` и другие project files остаются вне lifecycle authority ADWF, пока отдельный будущий contract явно не докажет обратное.

## Adoption plan

`plan_adoption()` — read-only. Сам planner и после LIFECYCLE-002 ничего не пишет.

Он cryptographically проверяет `MANIFEST.json` + `SHA256SUMS.txt`, exact 40-char source revision, canonical relative paths и отсутствие source symlinks. Для consumer target формируется одна из truth states:

- `ABSENT` → `CREATE_PLANNED`;
- `EXACT` → `KEEP_EXACT`;
- `COLLISION` → `BLOCK`;
- `SYMLINK` → `BLOCK`;
- `NON_FILE` → `BLOCK`.

Никакого `--apply` в LIFECYCLE-001 нет.

## Transactional adoption apply — LIFECYCLE-002

Запись разрешена только явным `--apply` и только поверх `READY` adoption plan. Planner остаётся read-only.

`apply_adoption()`:

- повторно проверяет source `MANIFEST.json`/`SHA256SUMS.txt`, требует чистый source Git worktree и совпадение фактического `HEAD` с exact `source_revision`; dirty/untracked source bytes не могут выдаваться за старый commit;
- принимает только plan, который полностью совпадает с текущим package inventory, ownership и source digests; forged READY plan блокируется;
- для `KEEP_EXACT` только повторно доказывает exact target и никогда не присваивает provenance;
- для `CREATE_PLANNED` перед каждой записью заново проверяет parent chain и запрещает symlink/non-directory escape;
- создаёт fully-written staging file в том же filesystem и публикует target через no-replace hard-link. Если target появился между plan и apply, он **не перезаписывается**;
- хранит self-sealed SHA-256 transaction journal под `.adwf-runtime/managed-surface/transactions/`, привязанный к exact source revision, manifest digest, canonical plan digest и consumer-root digest; изменение journal без canonical reseal детектируется как tamper;
- после каждого шага выполняет postcondition readback; повторный apply committed transaction является idempotent;
- сам adoption executor не выполняет detach и не расширяет свою mutation authority за пределы создания отсутствующих paths.

Если filesystem не может доказуемо выполнить no-replace create, apply блокируется вместо fallback на overwrite-capable write.

## Snapshot

`snapshot_from_adoption_plan()` создаёт expected post-adoption ownership snapshot только из `READY` plan. LIFECYCLE-002 дополнительно сохраняет transaction-bound snapshot под `.adwf-runtime/managed-surface/snapshots/` **только после полного postcondition readback**.

Transaction snapshot содержит optional binding fields `transaction_id`, `plan_sha256`, `consumer_root_sha256`; старый read-only v1 snapshot без этих полей остаётся schema-compatible.

Консервативное правило provenance:

- путь, который **уже существовал exact** до adoption, не становится автоматически `managed_by_adwf=true`;
- ADWF auto-own только файл, который plan видел `ABSENT` и текущая transaction реально создала;
- failed/partial apply не создаёт committed snapshot.

Это специально жертвует агрессивной очисткой ради сохранности consumer project.

## Rollback / recovery

При apply failure transaction переходит в recovery и удаляет только то, чьё создание доказано текущим journal/staging provenance.

- exact unchanged файл, созданный transaction, rollback-eligible;
- concurrent/foreign target не удаляется;
- если созданный ADWF файл был изменён после записи, recovery сохраняет его и возвращает `RECOVERY_BLOCKED`;
- созданные transaction directories удаляются только если они пусты; consumer files внутри них сохраняются;
- staging drift/symlink и ambiguous provenance блокируют destructive cleanup;
- отдельный `--recover-transaction <id>` повторяет recovery идемпотентно.

## Detach plan

`plan_detach()` также read-only.

Для `FRAMEWORK_PRIVATE` + `managed_by_adwf=true`:

- exact installed digest → `REMOVE_ELIGIBLE`;
- уже отсутствует → `ALREADY_ABSENT`;
- drift/symlink/non-file → `PRESERVE_BLOCK`.

Для `SHARED_GUARDED` → всегда `PRESERVE_SHARED`.

Для pre-existing exact paths → `PRESERVE_PREEXISTING`.

План никогда не удаляет файл сам.

## Transactional guarded detach — LIFECYCLE-003

Destructive mutation разрешена только явным `--detach-apply` поверх `READY` detach plan и **только** когда authority доказана durable transaction-bound snapshot от LIFECYCLE-002. Read-only `plan_detach()` остаётся отдельным planner и сам ничего не удаляет.

`apply_detach()`:

- повторно проверяет exact source Git revision, package integrity, snapshot schema и durable adoption journal/snapshot digest; legacy/unbound snapshot не получает delete authority;
- принимает только plan, который CAS-style совпадает с фактическим target state до начала новой transaction; forged/stale READY plan блокируется;
- разрешает mutation только для `FRAMEWORK_PRIVATE + managed_by_adwf=true + REMOVE_ELIGIBLE`; `SHARED_GUARDED`, pre-existing exact и unlisted/consumer-owned paths никогда не становятся delete targets;
- перед каждым destructive step заново проверяет parent chain, тип объекта и exact installed digest; symlink, non-file, drift или concurrent replacement переводят transaction в recovery/block вместо удаления неизвестных bytes;
- сначала атомарно переносит target в same-filesystem transaction quarantine, проверяет postcondition `target=ABSENT` и exact quarantine digest и только затем purges quarantine; это закрывает crash window между delete и durable journal;
- хранит отдельный self-sealed detach journal под `.adwf-runtime/managed-surface/detach-transactions/`, привязанный к adoption transaction, snapshot digest, detach-plan digest, source revision/manifest и consumer root; при recovery immutable authority заново сверяется с durable adoption snapshot, а quarantine path обязан быть детерминированным для конкретного managed path;
- поддерживает deterministic/idempotent resume: `PURGED`/`ALREADY_ABSENT` распознаются как уже выполненный progress той же transaction, а неизвестное состояние блокируется;
- при partial failure восстанавливает quarantined bytes без overwrite. Если exact managed bytes уже были безопасно purged, recovery может восстановить их только из cryptographically verified exact source revision;
- если concurrent replacement успел попасть в quarantine, recovery возвращает **эти** неизвестные bytes на target и оставляет `RECOVERY_BLOCKED/HUMAN_REQUIRED`, а не уничтожает их;
- после COMMITTED удаляет только доказанно adoption-created directories и только через `rmdir()` пустых каталогов; non-empty directories и consumer content сохраняются.

COMMITTED фиксируется только после полного readback: все `REMOVE_ELIGIBLE`/`ALREADY_ABSENT` должны быть действительно absent и ни один quarantine object не должен остаться. Partial failure не может создать ложный successful detach.

## CLI

Проверить canonical contract:

<!-- adwf-doc: skip(reason=validated-by-framework-contract-suite) -->
```bash
python .adwf/scripts/validate_managed_surface.py
```

Построить read-only adoption plan для consumer checkout:

<!-- adwf-doc: skip(reason=requires-consumer-checkout-and-exact-revision) -->
```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --source-revision <EXACT_40_CHAR_SHA>
```

Явно применить READY adoption transaction (dry-run остаётся default):

<!-- adwf-doc: skip(reason=requires-consumer-checkout-and-exact-source-git-revision) -->
```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --source-revision <EXACT_40_CHAR_SHA> \
  --apply
```

Восстановить/откатить незавершённую transaction:

<!-- adwf-doc: skip(reason=requires-existing-consumer-transaction-journal) -->
```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --recover-transaction <64_CHAR_TRANSACTION_ID>
```

Построить read-only detach plan по trusted snapshot:

<!-- adwf-doc: skip(reason=requires-consumer-checkout-and-trusted-snapshot) -->
```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --detach-snapshot /path/to/managed-surface-snapshot.json
```

Явно применить guarded detach transaction (default без `--detach-apply` остаётся dry-run):

<!-- adwf-doc: skip(reason=requires-transaction-bound-snapshot-and-exact-source-revision) -->
```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --source-revision <EXACT_40_CHAR_SHA> \
  --detach-snapshot /path/to/managed-surface-snapshot.json \
  --detach-apply
```

Восстановить незавершённую detach transaction из quarantine/exact verified source:

<!-- adwf-doc: skip(reason=requires-existing-detach-transaction-journal-and-exact-source-revision) -->
```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --source-revision <EXACT_40_CHAR_SHA> \
  --recover-detach-transaction <64_CHAR_TRANSACTION_ID>
```

## Что намеренно ещё не реализовано

После LIFECYCLE-003 всё ещё **не** реализованы:

- upgrade между ADWF revisions;
- migration consumer data;
- merge/overwrite `SHARED_GUARDED`;
- aggressive cleanup неизвестных/orphaned consumer paths;
- Project Pack SDK formalization;
- Apps Script/edge conformance.

LIFECYCLE-003 сознательно не превращает detach в «удалить всё ADWF-похожее»: delete authority существует только для provenance-bound exact managed private paths. Всё неоднозначное сохраняется.

## Truth boundary

Наличие schemas, planner, transaction executor и synthetic/fault-injection tests означает **implementation**, но не live consumer proof.

Capability `MANAGED_SURFACE_CONTRACT` остаётся `LIVE_NOT_VERIFIED`, пока отдельный реальный consumer repository не пройдёт transactional adoption + downstream detach/recovery evidence cycle. Unit/self-tests или успешный merge не являются таким live evidence.
