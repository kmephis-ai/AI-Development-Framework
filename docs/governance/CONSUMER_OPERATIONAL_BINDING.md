# Consumer Operational State Binding v1

## Назначение

Установленный в продукт ADWF не должен считать собственные `.adwf/roadmap.json` и `.adwf/project-state.json` дорожной картой и состоянием consumer-проекта. Для реального consumer используется отдельный consumer-owned self-sealed binding `.adwf-consumer/operations.json`.

Ключевой принцип: **framework Roadmap ≠ consumer Roadmap; ADWF связывается с существующим источником истины продукта, а не копирует его.**

## Режимы

- `SELF_HOST_CANONICAL` — только self-host ADWF без Consumer Profile/Installation Record. Сохраняются прежние `.adwf/roadmap.json` и project-state semantics.
- `CONSUMER_NATIVE` — установленный consumer. Требуется валидный operational binding; отсутствие или неоднозначность блокируются fail-closed.

В `CONSUMER_NATIVE` v1 binding хранит только безопасные ссылки: путь к native Markdown Roadmap и identity GitHub Issues repository. Markdown не исполняется и не превращается автоматически во второй Roadmap JSON.

## Trust boundary

Binding связан с exact Consumer Profile и Consumer Installation Record по SHA-256, проверяет repository identity, запрещает traversal/symlink и запрещает использовать `.adwf/**`, `.adwf-runtime/**` или `.adwf-consumer/**` как native Roadmap.

Binding имеет `mutation_authority=NONE_BINDING_IS_REFERENCE_ONLY`, бюджет 0 и `secrets=FORBIDDEN`. Он не даёт права изменять consumer files, policy, providers, CI, secrets или managed ownership.

## Fresh-session semantics

После удаления `.adwf-runtime` чистая provider-сессия может восстановить `CONSUMER_NATIVE` по tracked Consumer Profile + Installation Record + Operational Binding. `roadmap-view` при этом не читает framework self-host Roadmap и возвращает bounded native-source descriptor с `NATIVE_SOURCE_BOUND_NOT_MATERIALIZED` до отдельного adapter/gate work unit.

## Не входит в v1

- парсинг произвольного Markdown в ADWF DAG;
- копирование GitHub Issues в `.adwf/roadmap.json`;
- consumer-aware CI/native gate delegation;
- реальная установка в PrihRash;
- изменение финансовой логики или данных.
