# ADWF Consumer AI Development Core v1

Этот файл содержит generic правила разработки connected consumer-проектов. Он управляется ADWF и не должен содержать product-specific факты конкретного consumer.

## Source of truth

- Перед существенным решением или mutation заново читать provider/runtime state: exact `main`, active Issues/PR, writers/leases, required checks, latest merges и relevant runtime evidence.
- Chat history, handover и durable docs — только hints/context; они не переопределяют свежую provider truth.
- Текущий writer, task, branch, SHA и lifecycle status не хранятся как durable instruction state.

## Execution discipline

- Fail closed: неизвестное не превращать в PASS.
- `FREE_ONLY`; mandatory monetary budget `$0`; paid/unknown mandatory providers запрещены.
- Не обходить rulesets, required checks, exact-SHA authority, review/merge policy или safety gates.
- Один writer на пересекающийся conflict domain; независимые read-only исследования могут идти параллельно.
- Использовать rolling-wave planning: один ближайший AI-sized work unit, затем fresh discovery.
- После mutation делать provider readback/CAS там, где provider это поддерживает.
- `DONE`, `LIVE_VERIFIED`, `PRODUCT_READY` и аналогичные claims требуют предусмотренного machine/provider/runtime evidence.

## Instruction layering

Effective repository instructions состоят из слоёв:

1. этот `FRAMEWORK_CORE`;
2. выбранный Project Pack из consumer profile;
3. consumer-owned `.adwf-consumer/INVARIANTS.md` с настоящими product-specific ограничениями;
4. root `AGENTS.md` как компактный router/entrypoint.

ADWF не получает права перезаписывать или удалять consumer invariants только потому, что framework обновился. Product-specific privacy, data, financial, business, architecture и irreversible-action boundaries остаются consumer-owned authority.

## Root router

Root `AGENTS.md` не должен становиться вторым Roadmap/runtime ledger. Для актуального task/writer/SHA он обязан направлять AI к fresh provider/runtime discovery. Новый или мигрированный router должен соответствовать `ADWF_CONSUMER_ROUTER_V1`.
