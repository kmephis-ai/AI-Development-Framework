# GOV-034 Provider Ops end-to-end proof

Этот bounded documentation change служит реальным source-only fixture для проверки уже merged `MATERIALIZE_PROJECTIONS` gateway.

Путь намеренно находится в framework-owned documentation surface: source change должен изменить docs freshness, `MANIFEST.json` и `SHA256SUMS.txt`. Эти deterministic projections заранее не редактируются — их обязан пересчитать trusted provider controller и опубликовать ровно одним force-free child commit на действующей writer branch.

Документ не предоставляет новых полномочий, не меняет policy/rulesets/permissions, не затрагивает consumer/product runtime и не создаёт денежного обязательства.
