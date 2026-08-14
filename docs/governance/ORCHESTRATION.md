# Orchestration Policy

- Default: один active Writer во всём проекте. Reviewer может работать read-only параллельно.
- Один Issue имеет один TTL lease с owner, base SHA, heartbeat и conflict domains.
- `READY` недостаточно: `dependencies_resolved`, spec freshness, product policy и permission MUST быть подтверждены.
- Expired lease переводит незавершённый Issue в `RECOVERY`; silent reassignment запрещён.
- При двух Writer, label/Project/state mismatch или PR/lease mismatch feature progression останавливается до reconcile.
- Merge integration сериализован. После изменения `main` stale evidence пересоздаётся.
- Batch разрешён только для заранее заданных независимых read-only/verification задач.
- Orchestrator не меняет product strategy и не создаёт незапрошенный scope.
- Полный phase journal имеет revision CAS, idempotency key, deadline, attempt/cycle budget и hash-chain.
- Каждый provider adapter выполняет ровно один предложенный шаг; после restart продолжает только проверенное состояние.
- UI/product change не проходит Preview/Owner Acceptance без exact SHA и digest; новый commit делает acceptance `STALE`.
- Retry допустим только для transient failure; deterministic test failure идёт прямо в Recovery.
