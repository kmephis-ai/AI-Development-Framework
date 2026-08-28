# GOV-034 Provider Ops end-to-end proof

Этот bounded documentation change служит реальным source-only fixture для проверки уже merged `MATERIALIZE_PROJECTIONS` gateway.

До provider-hosted materialization deterministic projections намеренно не изменяются. Trusted controller должен самостоятельно пересчитать `.adwf/docs-registry.json`, `MANIFEST.json` и `SHA256SUMS.txt`, создать ровно один force-free child commit на действующей writer branch и вернуть provider-readback evidence.

Документ не предоставляет новых полномочий, не меняет policy/rulesets/permissions, не затрагивает consumer/product runtime и не создаёт денежного обязательства.
