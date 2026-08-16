# Consumer Framework Upgrade Transaction v1

## Назначение

UPGRADE-002 добавляет mutating слой поверх sealed `UPGRADE-001` compatibility result и `READY` plan. Executor переводит уже подключённый consumer project с **exact ADWF revision A** на **exact revision B** только в пределах заранее доказанной framework-private authority и сохраняет возможность детерминированного rollback/recovery.

Главный инвариант:

**NO READY PLAN → NO WRITE. NO PROVEN OWNERSHIP → NO REPLACE/REMOVE. NO RECOVERY PROVENANCE → FAIL CLOSED.**

## Truth boundary

UPGRADE-002 доказывает реализацию transaction/recovery semantics на детерминированных и adversarial fixtures. Capability остаётся `LIVE_NOT_VERIFIED`: безопасный production/consumer upgrade будет заявлен только после отдельного real-consumer evidence для exact последовательности `A → B → rollback → B`.

Executor не делает PrihRashOnline или любой другой consumer автоматически «готовым к обновлению» только по факту зелёных self-tests.

## Exact входы и preflight

Перед первой записью executor повторно проверяет:

- clean exact source revision A и target revision B;
- self-sealed UPGRADE-001 compatibility result;
- self-sealed `READY` plan, exact bound к compatibility;
- source/target MANIFEST SHA-256;
- exact consumer root identity;
- trusted source Managed Surface snapshot/provenance;
- source Consumer Profile и детерминированный target profile;
- отсутствие unsupported semantic migrations;
- фактические digest/type/ownership preconditions каждого plan entry.

Stale plan, forged seal, checkout substitution, drift, collision, symlink/type ambiguity или неизвестная mutation action блокируют apply до получения новой authority.

## Mutation authority

Executor использует закрытый whitelist:

| Plan action | Authority |
|---|---|
| `KEEP_EXACT` | verify-only, no write |
| `PRESERVE_SHARED` | verify-only, no write |
| `PRESERVE_PREEXISTING` | verify-only, no write |
| `CREATE_PLANNED` | atomic no-replace create exact B file |
| `REPLACE_PLANNED` | exact A managed private preimage → quarantine → exact B |
| `REMOVE_PLANNED` | exact A managed private preimage → quarantine → absence |

`PRESERVE_SHARED` разрешён только когда A и B требуют один и тот же exact package digest. Для `PRESERVE_PREEXISTING` `LIFECYCLE-007` дополнительно несёт immutable `preserved_sha256`: package digest связывает A/B contract, а фактические consumer bytes проверяются по preserved preimage. Такой path не staging-ится, не quarantine-ится, не заменяется и не удаляется; preserved drift блокирует fail-closed.

## Durable journal и provenance

Каждая транзакция имеет deterministic id и self-sealed journal, привязанный к A/B revisions, source/target manifests, plan/compatibility seals, source snapshot digest и consumer root identity.

До destructive remove исходные exact bytes сохраняются в transaction-specific quarantine. Staging и install используют no-replace semantics; установленный target остаётся provenance-linked к stage до записи journal state, что позволяет отличить принадлежащий транзакции файл от постороннего даже в crash window.

Runtime directories, consumer target parents и quarantine paths проверяются на symlink/non-directory ambiguity. Recovery не следует через подменённый parent symlink и не удаляет foreign content.

## Consumer Profile

Переход Consumer Profile входит в ту же transaction boundary:

1. source profile обязан быть exact A;
2. target profile вычисляется детерминированно из B;
3. source profile bytes quarantined до remove;
4. target profile устанавливается no-replace;
5. rollback восстанавливает exact source bytes;
6. final B snapshot публикуется только после exact B profile postcondition.

## Commit, rollback и recovery

`COMMITTED` разрешён только после exact B postconditions для всех managed paths и profile, затем публикуется новый Managed Surface snapshot, bound к transaction id и target revision B.

При exception/crash journal остаётся recoverable. Recovery идёт в обратном порядке и может удалить B path только при transaction provenance. Если target/quarantine/runtime объект tampered или содержит foreign content, состояние становится `RECOVERY_BLOCKED`; данные сохраняются для разбирательства вместо destructive guess.

Explicit rollback committed-транзакции использует тот же conservative recovery engine и возвращает exact A. После полного `ROLLED_BACK` повторный apply разрешён только через новый полный A-preflight. Повторный apply после `COMMITTED` проверяет exact B state и возвращает `ALREADY_COMMITTED` без write.

## Chained upgrades

Новый B snapshot может стать source provenance для следующего B→C только если его exact transaction journal существует и имеет `COMMITTED`, а stored snapshot bytes/digest/revision/manifest совпадают. При проверке прошлого journal используется **source framework B contract**, а не новый target C — target version не может ретроспективно переопределить provenance предыдущей транзакции.

## Migration safety

Migration registry остаётся metadata contract. UPGRADE-002 не исполняет arbitrary scripts/plugins. Migration id допустим только при наличии встроенного versioned test-covered handler в executor. В v1 builtin handler set пуст; любой migration id fail-closed блокирует apply.

## CLI

`.adwf/scripts/apply_consumer_upgrade.py` предоставляет только явные subcommands:

- `apply` — exact compatibility/plan/snapshot → transaction apply;
- `recover` — восстановление interrupted transaction по transaction id;
- `rollback` — явный rollback committed transaction.

Planning CLI UPGRADE-001 остаётся read-only; mutating operation никогда не запускается неявно из planning.

## Adversarial evidence

Focused suite проверяет commit/idempotence, rollback→retry, stale/forged inputs, checkout substitution, consumer drift, create collision, symlink/type ambiguity, changed shared path, unsupported migration, replace/remove/profile crash windows, crash after B snapshot before commit, journal/quarantine tamper, foreign target preservation, quarantine parent symlink и chained B→C provenance.

Эти tests являются implementation evidence. Следующий rolling-wave unit должен добавить real external consumer exact-revision `A → B → rollback → B` evidence и только затем может повысить live truth.
## Unchanged Consumer Profile rollback semantics

Если exact A и B дают byte-identical `.adwf-consumer/profile.json`, transaction journal фиксирует profile как `UNCHANGED`: committed rollback **только проверяет** exact source/target digest и не получает quarantine/restore authority. Отсутствие profile quarantine в этом состоянии является нормой, а не поводом для восстановления. Любой drift такого profile блокирует rollback fail-closed без удаления или перезаписи consumer bytes. Это regression invariant `UPGRADE_FIX-001`.
