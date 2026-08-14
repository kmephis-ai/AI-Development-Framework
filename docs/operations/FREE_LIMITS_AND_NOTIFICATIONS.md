# ADWF v1.6 — FREE_ONLY и уведомления

## Обязательный путь

Канонический профиль — `FREE_PUBLIC_GITHUB`. Mandatory correctness gates не требуют AI/API. Larger/metered/unknown capability блокируется, пока владелец отдельно не одобрит optional paid path.

## Cost truth

Панель различает:

- `$0 VERIFIED` — только когда provider/cost facts подтверждены;
- `NOT_VERIFIED` — нет свежих фактов;
- `BLOCK` — действие нарушает budget/policy.

Нормативный ноль не заменяет observed provider usage.

## Уведомления

CI/provider event может создать trusted wakeup event Runtime Supervisor. Публичный комментарий Issue/PR не является authorization для AI. Внешний creative agent получает bounded Action Envelope с exact run/phase/revision и budget.
