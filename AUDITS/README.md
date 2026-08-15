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

Последний принятый аудит: `ADWF-FOUNDATION-2026-08-15`.

Ключевое решение: не переписывать ADWF и не наращивать Core бесконечно; завершать фундамент через Engineering Truth → Consumer Lifecycle → heterogeneous conformance → Human-by-Exception proof → `FOUNDATION_READY`.
