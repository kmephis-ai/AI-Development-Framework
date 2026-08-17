# Repository AI Router

<!-- ADWF_CONSUMER_ROUTER_V1 -->

Этот файл — компактная точка входа, а не хранилище текущего состояния проекта.

- `FRAMEWORK_CORE: .adwf/instructions/CORE.md`
- `CONSUMER_INVARIANTS: .adwf-consumer/INVARIANTS.md`
- `PROJECT_PACK: consumer profile selected Project Pack`
- `LIVE_STATE: provider/runtime`

Перед mutation выполняйте fresh provider/runtime discovery. Не храните здесь `CURRENT_WRITER`, `CURRENT_TASK`, `CURRENT_SHA` или другой быстро устаревающий operational state.
