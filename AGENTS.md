# AGENTS.md — обязательные правила ADWF v1.6

Это короткая карта для любого AI/человека, который работает внутри ADWF. Полный контракт — `SPECIFICATION.md`.

## Язык

Human-facing Issues, PR summaries, roadmap, dashboard, reports и explanations — понятный русский без необходимости знать Git/CI/CD. Machine identifiers и code symbols остаются в требуемом tooling языке.

## Формула

- AI исследует, предлагает, пишет и объясняет.
- ADWF Runtime Supervisor организует durable workflow.
- Effective Policy разрешает/блокирует действие.
- Provider API и Evidence подтверждают external facts.
- Владелец принимает продуктовый смысл и необратимые решения.

## Нельзя

1. Превращать неизвестное в PASS.
2. Объявлять `Machine Verified` из текста агента/state string.
3. Расширять permissions созданным самим AI инструментом.
4. Передавать secret в prompt/Issue/PR/log/Work Memory.
5. Хранить raw hidden chain-of-thought как project context.
6. Считать закрытые Issues доказательством Product Done.
7. Делать paid/unknown AI mandatory correctness gate.
8. Обходить exact SHA/preview digest/policy hash owner decision.
9. Выполнять PR code в trusted controller.
10. Повторять deterministic failure без классификации причины.

## Writer discipline

Один активный Writer на conflict domain. Параллельны только независимые read-only lanes или явно непересекающиеся workspaces. Перед mutation требуется текущая policy authorization; после mutation — readback/CAS там, где provider поддерживает revision.

## Work Memory

Записывать только handoff facts: что нужно, что принято, что изменено, что проверено, blocker, вопросы, следующий шаг и ссылки. Не придумывать историю решения и не сохранять скрытое reasoning.

## Owner interaction

Не отправлять владельца в Actions/JSON/SHA, если система может сама диагностировать. По умолчанию показывать: состояние продукта, что изменилось, результат, roadmap и следующую безопасную кнопку.

## Release discipline

Автоматическая версия = SemVer по impact. Количество задач не определяет major/minor/patch. External release и irreversible promotion требуют предусмотренный policy/owner gate.
