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

`plan_adoption()` — read-only.

Он cryptographically проверяет `MANIFEST.json` + `SHA256SUMS.txt`, exact 40-char source revision, canonical relative paths и отсутствие source symlinks. Для consumer target формируется одна из truth states:

- `ABSENT` → `CREATE_PLANNED`;
- `EXACT` → `KEEP_EXACT`;
- `COLLISION` → `BLOCK`;
- `SYMLINK` → `BLOCK`;
- `NON_FILE` → `BLOCK`.

Никакого `--apply` в LIFECYCLE-001 нет.

## Snapshot

`snapshot_from_adoption_plan()` создаёт expected post-adoption ownership snapshot только из `READY` plan.

Консервативное правило provenance:

- путь, который **уже существовал exact** до adoption, не становится автоматически `managed_by_adwf=true`;
- ADWF может auto-own только то, чего не было до adoption и что будущий apply executor действительно создаст.

Это специально жертвует агрессивной очисткой ради сохранности consumer project.

## Detach plan

`plan_detach()` также read-only.

Для `FRAMEWORK_PRIVATE` + `managed_by_adwf=true`:

- exact installed digest → `REMOVE_ELIGIBLE`;
- уже отсутствует → `ALREADY_ABSENT`;
- drift/symlink/non-file → `PRESERVE_BLOCK`.

Для `SHARED_GUARDED` → всегда `PRESERVE_SHARED`.

Для pre-existing exact paths → `PRESERVE_PREEXISTING`.

План никогда не удаляет файл сам.

## CLI

Проверить canonical contract:

```bash
python .adwf/scripts/validate_managed_surface.py
```

Построить read-only adoption plan для consumer checkout:

```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --source-revision <EXACT_40_CHAR_SHA>
```

Построить read-only detach plan по trusted snapshot:

```bash
python .adwf/scripts/validate_managed_surface.py \
  --consumer-root /path/to/project \
  --detach-snapshot /path/to/managed-surface-snapshot.json
```

## Что намеренно ещё не реализовано

LIFECYCLE-001 **не** выполняет:

- запись/adoption;
- upgrade;
- migration consumer data;
- rollback;
- удаление/detach;
- Project Pack SDK;
- Apps Script/edge conformance.

Следующий lifecycle work unit может строить safe adoption/update executor только поверх этого ownership contract и обязан записывать provenance snapshot транзакционно.

## Truth boundary

Наличие schemas, planner и tests означает **implementation**, но не live consumer proof.

Capability `MANAGED_SURFACE_CONTRACT` остаётся `LIVE_NOT_VERIFIED`, пока отдельный реальный consumer repository не пройдёт adoption/detach evidence cycle. Unit/self-tests или успешный merge не являются таким live evidence.
