# Политика автономности

| Уровень | Автоматически разрешённые действия |
| --- | --- |
| A0 | read-only inspect и plan |
| A1 | локальные edit и test; без commit/push/PR/claim |
| A2 | A1 + commit, push, open PR и claim одного R0/R1 Issue |
| A3 | A2 + managed merge и DEV deploy после exact-SHA gates |
| A4 | автоматических действий нет; human-only boundary |

ADWF v1.6 стартует в A2. Повышение требует отдельной live certification: свежие Package/Config/Control/Product Health, end-to-end R0/R1 proof, exact-SHA preview/review, owner acceptance там, где требуется, rollback drill и нулевой cost risk.

Effective permission вычисляется engine, а не текстом: minimum из action minimum, active autonomy, risk ceiling, work type, Health, gates, exact SHA, evidence freshness, Writer conflict, provider и cost. Human approval не может превратить stale evidence, unknown provider или non-zero monetary cost в `ALLOW`.
