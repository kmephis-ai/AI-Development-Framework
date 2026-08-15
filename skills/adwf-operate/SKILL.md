---
name: adwf-operate
description: Route ADWF release, incident, upgrade, rollback, cost and runtime-operation requests without loading unrelated development or governance Skills.
---

# ADWF Operate Router

Используй этот router для release, incident, upgrade, rollback, cost и runtime operations.

## Current route
- `adwf-local-git-mirror` — восстановление exact-SHA workspace как execution-recovery capability, если direct Git недоступен.

Для остальных operational intents пока используй core ADWF procedures. Не создавай фиктивный leaf Skill только ради покрытия каталога.

## Boundary
FREE_ONLY, provider eligibility, permissions, required checks и irreversible promotion остаются deterministic policy. Router не ослабляет gate и не превращает unknown provider/cost state в разрешение.
