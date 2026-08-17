# ADWF Consumer AI Development Core v1

Этот файл содержит generic правила разработки connected consumer-проектов. Он управляется ADWF и не должен содержать product-specific факты конкретного consumer.

## Source of truth

- Перед существенным решением или mutation заново читать provider/runtime state: exact `main`, active Issues/PR, writers/leases, required checks, latest merges и relevant runtime evidence.
- Chat history, handover, continuity checkpoint и durable docs — только hints/context; они не переопределяют свежую provider truth.
- Текущий writer, task, branch, SHA и lifecycle status не хранятся как durable instruction state.
- Stale checkpoint/SHA никогда не является write authority: перед resume/write требуется fresh authority resolution.

## Execution discipline

- Fail closed: неизвестное не превращать в PASS.
- `FREE_ONLY`; mandatory monetary budget `$0`; paid/unknown mandatory providers запрещены.
- Не обходить rulesets, required checks, exact-SHA authority, review/merge policy или safety gates.
- Один writer на пересекающийся conflict domain; независимые read-only исследования могут идти параллельно.
- Использовать rolling-wave planning: один ближайший AI-sized work unit, затем fresh discovery.
- После mutation делать provider readback/CAS там, где provider это поддерживает.
- `DONE`, `LIVE_VERIFIED`, `PRODUCT_READY` и аналогичные claims требуют предусмотренного machine/provider/runtime evidence.

### WORK_UNTIL_NATURAL_BOUNDARY

Каждый executor работает в режиме `WORK_UNTIL_NATURAL_BOUNDARY`: после fresh reconciliation и получения/возобновления разрешённой work authority он продолжает все последующие безопасные разрешённые transitions в той же usable session, а не завершает работу после произвольного промежуточного шага.

Нормальная цепочка может включать:

`fresh reconcile -> acquire/resume authority -> implement -> test -> repair -> materialize -> PR/update -> exact-head CI/evidence -> permitted merge/lifecycle transition -> post-merge/readback -> next Roadmap-authorized unit`.

Сам по себе commit, открытие PR, первый CI result, `PASS`, merge или завершение одного AI-sized substep **не являются Natural Boundary**, если существует следующий безопасный и разрешённый шаг, который можно выполнить в этой же session.

Natural Boundary существует только когда дальнейшее безопасное продвижение реально невозможно или запрещено, например:

- `HUMAN_REQUIRED`, R4 exact-SHA Owner-Attestation или required UAT;
- destructive/security/secrets boundary;
- required capability unavailable;
- внешний CI/provider/runtime действительно `queued/in_progress`, а другого безопасного полезного шага нет;
- текущая work authority/conflict-domain scope исчерпана и следующий work item ещё не разрешён;
- Roadmap end;
- фактический executor/tool-session limit.

Перед yield на реальной Natural Boundary executor должен, если доступен штатный provider-durable механизм, сохранить public-safe continuity checkpoint/handover и затем выполнить readback/CAS. Checkpoint содержит только проверяемые факты/refs и следующий разрешённый шаг; private chain-of-thought, secrets, unbounded chat transcript и private session identifiers в public durable state запрещены.

После resume новый executor обязан сначала заново сверить provider truth и work authority. Если provider продвинулся после checkpoint (например, commit/merge произошёл до аварийного завершения session), executor принимает новый provider state, не повторяет mutation только из-за stale checkpoint и формирует reconciled continuation state.

## Instruction layering

Effective repository instructions состоят из слоёв:

1. этот `FRAMEWORK_CORE`;
2. выбранный Project Pack из consumer profile;
3. consumer-owned `.adwf-consumer/INVARIANTS.md` с настоящими product-specific ограничениями;
4. root `AGENTS.md` как компактный router/entrypoint.

ADWF не получает права перезаписывать или удалять consumer invariants только потому, что framework обновился. Product-specific privacy, data, financial, business, architecture и irreversible-action boundaries остаются consumer-owned authority.

## Root router

Root `AGENTS.md` не должен становиться вторым Roadmap/runtime ledger. Для актуального task/writer/SHA он обязан направлять AI к fresh provider/runtime discovery. Новый или мигрированный router должен соответствовать `ADWF_CONSUMER_ROUTER_V1`.
