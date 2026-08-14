# Руководство владельца: человек + AI в ADWF v1.6

Владелец формулирует эффект и визуальный результат. AI отвечает за исследование/реализацию в разрешённой workspace. ADWF отвечает за durable state, permissions и проверяемый workflow. GitHub отвечает за external journal/readback.

## Не нужно делать владельцу

По целевой модели владелец не выбирает branch, не редактирует CI YAML, не сверяет SHA, не создаёт tag и не чинит обычный transient failure вручную.

## Когда нужен владелец

- legal/publication/LICENSE;
- privacy/data risk;
- неоднозначный product choice;
- изменение trust boundary;
- irreversible/destructive production action;
- финальная визуальная/product acceptance, когда policy требует её.

## Долгая работа

Если задача длится несколько дней, Executive Portal читает Work Memory/Runtime state. Новый AI worker получает структурированный handoff, а не обязан «помнить предыдущую беседу».

## AI summary

Human-readable summary может генерироваться AI, но оно только объясняет deterministic facts. При недоступном AI system summary остаётся возможным без платного API.
