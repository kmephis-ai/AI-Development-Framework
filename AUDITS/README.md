# ADWF Audit History

Этот каталог хранит долговременную историю независимых аудитов и принятых по ним архитектурных решений.

## Обязательный протокол для следующего AI-аудита

Перед новым аудитом:

1. Прочитать `AUDITS/audit-history.json`.
2. Прочитать последний audit report, указанный в registry.
3. Заново получить фактический `main`, provider/runtime state, CI, rulesets, releases и другие live facts.
4. Не использовать прошлый audit report как доказательство текущего состояния.
5. Сравнить текущую реальность с предыдущими findings/decisions и отдельно показать delta: что исправлено, что осталось, что устарело, какие новые проблемы появились.
6. Не предлагать заново ранее `DEFER/REJECT` решения без нового consumer evidence или изменения исходных ограничений.
7. Не считать принятую рекомендацию реализованной без code/provider/runtime evidence.
8. Если новое решение отменяет старое — явно записать supersession и причину.

## Семантика статусов

`ACCEPTED_AS_PLANNING_BASELINE` означает только то, что владелец принял выводы аудита как направление развития.

Это **не** означает автоматически `IMPLEMENTED`, `LIVE_VERIFIED`, `RELEASED` или `FOUNDATION_READY`.

## Текущий baseline

Последний принятый аудит: `MEGA-AUDIT-2026-08-22`.

Ключевое решение: сохранить ценное governance/control-plane ядро ADWF, но временно остановить расширение архитектуры ради Truth & Safety Reset — автономность/continuity, достоверность evidence, снижение process tax и один реально доказуемый lifecycle важнее fleet, multi-writer, skill factory и большого UI.

Consumer-задачи и другие существующие Roadmap items **не удаляются**: они остаются долгосрочной очередью за maturity gates. Ближайший технический P0 после canonical audit adoption — `SELFTEST_COVERAGE-001 / #253`.

Пост-аудитный delta: finding P0-3 о terminal Runtime Ledger resurrection исправлен GOV-030 и защищённо вошёл в `main@4cd9e6eaa8b36ddc1ec4476c51b77671a6fc5275`. Остальные findings этого аудита не считаются автоматически исправленными.

Предыдущий `ADWF-FOUNDATION-2026-08-15` остаётся историческим baseline и не удаляется. Mega Audit уточняет текущий порядок работ; live/provider facts по-прежнему требуют fresh readback в каждой новой транзакции.
