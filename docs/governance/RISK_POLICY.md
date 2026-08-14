# Политика риска

| Уровень | Смысл | Примеры | Обработка |
|---|---|---|---|
| R0 | Тривиальный | typo, безопасная документация | высокая автоматизация |
| R1 | Низкий | локальный bugfix, test, небольшой UI | A3 после зрелых gates |
| R2 | Средний | новая capability, API change, multi-component | independent review |
| R3 | Высокий | auth, migration, critical logic, release infra | дополнительный gate / human по policy |
| R4 | Критический | destructive data, IAM, credentials, billing, trust boundary | Human Gate обязателен |

Риск повышается при неопределённом rollback, production data impact, auth/security, широком blast radius, отсутствии tests, невоспроизводимом state, secrets/IAM/billing. Нельзя понижать risk ради auto-merge.
