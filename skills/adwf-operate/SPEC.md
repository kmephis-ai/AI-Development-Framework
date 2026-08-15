# Contract: adwf-operate

Назначение: bounded router для release/incident/upgrade/cost/runtime operations.

Инварианты:
- FREE_ONLY и provider cost truth проверяются deterministic policy;
- runtime operation не должна автоматически расширять permissions;
- rollback/recovery не означает обход required checks;
- при отсутствии специализированного leaf Skill используется core ADWF procedure.
