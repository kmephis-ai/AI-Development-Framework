# Consumer-aware CI Routing v1

`CONSUMER_ROUTE-001` разделяет две модели выполнения без смешения доверительных границ:

- **ADWF self-host** продолжает выполнять полный framework contract suite, docs, package/config health, self-test и существующие project gates;
- **installed consumer** валидирует связку Consumer Profile → Installation Record → Operational Binding → Native Gate Binding, сверяет все `managed_by_adwf=true` bytes с durable installation record и принимает PR/Main только по exact-SHA provider-native checks.

Режим нельзя переключить только добавлением `.adwf-consumer/*` в PR. Для PR/Main transition проверяется exact predecessor commit: self-host predecessor может остаться только self-host; уже подключённый consumer — только consumer; `UNMANAGED_PREINSTALL → CONSUMER_NATIVE` разрешён только для первого корректного adoption consumer-проекта.

Consumer route не запускает глобальную self-host проверку чужих `.github/workflows/**`/документации и не использует generic Project Pack commands вместо native checks. Делегирование получает только `contents: read` + `checks: read`, использует ephemeral `github.token`, имеет budget `0` и не получает workflow/ruleset/deployment/data mutation authority.

Missing, tampered или incomplete bindings, drift ADWF-managed bytes, repository mismatch, stale/wrong SHA, failing/duplicate check или wrong GitHub App блокируются fail-closed. Pending/missing native check может ожидаться только в bounded polling window: по умолчанию до 30 exact provider readback attempts с интервалом 10 секунд (максимум около 5 минут). Каждый attempt использует те же exact-SHA/check/app/success требования; истечение окна остаётся BLOCK, а не retry-as-success.

Это generic implementation capability. Реальный PrihRash считается подключённым только после отдельного provider proof в `PrihRashOnline-v2#287`.
