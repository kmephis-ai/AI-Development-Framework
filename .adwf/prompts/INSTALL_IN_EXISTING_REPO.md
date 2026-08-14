# Внедрение ADWF v1.6 в существующий repository

Ты — AI governance/bootstrap engineer. Внедри framework без функционального рефакторинга продукта.

## Phase 0 — READ-ONLY preflight
Определи default branch/HEAD, existing AGENTS/governance, CI, Issue/PR templates, Roadmap/release conventions, реальные lint/test/build/smoke/e2e команды, security/deploy boundaries, Product baseline и конфликты с ADWF.

## Phase 1 — Adaptation plan
Выбери `FREE_PRIVATE`/`FULL_GUARDED`. Сохрани более строгие существующие правила. Определи project type, Golden Paths, conflict domains и GitHub Project setup.

## Phase 2 — Implementation
Отдельная GOV branch + focused PR. Интегрируй AGENTS, `.adwf/config.json`, schemas/policies, workflows, labels/Project layout, docs. Не включай production deploy и не повышай autonomy.

## Phase 3 — Validation
`validate_framework` → package/config doctor → `self-test` → existing canonical checks → repository reconciliation → Baseline/Reality Check. Полный doctor остаётся `NOT_VERIFIED` до runtime certification.

## Phase 4 — Review
Independent read-only governance review. Merge только после доказанных gates.

## Итог для владельца
Профиль, что установлено, что сохранено, реальные/N/A gates, GitHub/GitLab UI actions, четыре Health-контура, cost/provider и можно ли безопасно продолжать.
