## Контракт

Roadmap-ID: CHANGE_ME
Issue: #0
Writer-Lease: 00000000-0000-4000-8000-000000000000

## Что изменено и зачем

Краткое русскоязычное объяснение решения и причин.

## Scope

- Ожидаемые conflict domain:
- Фактически затронутые компоненты:
- Scope drift: NONE / EXPLAINED / HIGH

## Проверки

- [ ] Tests выполнены либо `N/A` обоснован
- [ ] CI относится к точному текущему HEAD SHA
- [ ] Independent Review относится к точному текущему HEAD SHA
- [ ] Documentation Impact проверен
- [ ] Для product-impact работы приложено свежее runtime evidence

## Risk / rollback

Risk: R0

Rollback: описать безопасный и проверенный возврат.

## Trust boundary

- [ ] PR не ослабляет ADWF/CI/security/autonomy/permissions
- [ ] Trust impact классифицирован trusted automation по политике из BASE revision
- Authorization: AUTO / OWNER_DECISION_REQUIRED / N/A
- [ ] Только если `Authorization=OWNER_DECISION_REQUIRED`: trust change вынесен в отдельный GOV PR, классифицирован R4 и human-gated

Для routine safe changes отдельная SHA-аттестация не требуется. Если trusted policy возвращает `OWNER_DECISION_REQUIRED`, automation должна показать владельцу понятную причину и запросить решение.
