# Consumer Native Gate Binding v1

ADWF не заменяет существующий CI consumer-проекта. `.adwf-consumer/gates.json` — consumer-owned self-sealed **reference-only** контракт, который связывает `pr`, `main` и `runtime` phases с точным именем GitHub Check Run и GitHub App identity.

Делегирование означает только проверку evidence. Для `VERIFIED` ADWF требует exact subject SHA, ровно один matching check, `status=completed`, `conclusion=success` и точное совпадение `check_name + app_slug + app_id`. Missing, pending, failure, stale SHA, wrong app и duplicate/ambiguous identity дают `NOT_VERIFIED`.

Контракт связан с exact Consumer Profile, Consumer Installation Record и Consumer Operational Binding. Он имеет `monetary_budget_usd=0`, `secrets=FORBIDDEN`, `mutation_authority=NONE_BINDING_IS_REFERENCE_ONLY` и не может менять workflows, rulesets, deployments или product files.

Это generic capability. До отдельного provider proof на реальном PrihRash он не означает, что PrihRash уже ADWF-managed.
