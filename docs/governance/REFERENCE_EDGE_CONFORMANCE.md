# Reference Edge Controller Conformance v1

## Назначение

`EDGEREF-001` добавляет третий heterogeneous reference class ADWF: локальный JavaScript Edge Controller. Он проверяет, что тот же lifecycle/control plane способен безопасно обслужить non-browser device-oriented repository, не подключаясь к физическому устройству.

Это **reference conformance**, а не сертификат конкретного контроллера. PASS не означает, что выполнялись SSH, hardware I/O, установка пакетов на устройство, production deployment или vendor-cloud API.

## Project Pack `edge-controller`

Marker: `edge-controller.json`. Pack имеет precedence над generic `node` и намеренно ограничен:

- `monetary_budget_usd = 0`;
- `secrets = FORBIDDEN`;
- `network = NONE`;
- `environment = PROCESS_MINIMAL`;
- разрешены только deterministic local `lint`, `unit`, `build`;
- `install`, `start`, browser preview, SSH/device deployment отсутствуют и semantic validator блокирует их добавление.

Reference fixture не имеет external package dependencies. Все проверки исполняются установленным runner-side Node.js без `npm install`/registry/network.

## End-to-end proof

`reference_edge_conformance.py` использует существующий общий conformance engine и выполняет:

1. создание отдельного synthetic Git consumer из framework-owned fixture;
2. exact-source transactional adoption;
3. materialization sealed Consumer Profile + exact `edge-controller` pack digest;
4. commit operational consumer revision;
5. `ProjectExecutionSession` для local-only lint/unit/build и deterministic rule behavior;
6. explicit preview truth `NOT_APPLICABLE / EDGE_CONTROLLER_NO_BROWSER_PREVIEW`;
7. guarded detach;
8. byte-for-byte preservation digest consumer-owned seed files;
9. re-adoption с новым transaction identity;
10. idempotent consumer profile;
11. strict-schema + self-sealed `REFERENCE_CONFORMANCE_REPORT`.

Functional proof проверяет pure controller rule function на фиксированном наборе событий. Он не подменяет physical runtime.

## Fail-closed truth boundary

Report обязан фиксировать:

- `device_runtime_executed = false`;
- `device_deployment_performed = false`;
- `ssh_required = false`;
- `external_network_required = false`;
- `REAL_EDGE_DEVICE_RUNTIME_NOT_EXECUTED`;
- `SSH_OR_DEVICE_DEPLOYMENT_NOT_EXECUTED`;
- `DEVICE_PROVIDER_NOT_VERIFIED`;
- `REFERENCE_NOT_LIVE_PROVIDER_EVIDENCE`.

Подмена class/pack, ложный browser preview, изменение device truth, расширение pack network/install/start authority, mutation tracked source либо tamper report seal блокируются тестами/validator.

## Capability Truth

`REFERENCE_EDGE_CONFORMANCE = LIVE_NOT_VERIFIED`.

Для `LIVE_VERIFIED` потребуется отдельный реальный downstream edge consumer + device/provider runtime evidence, привязанные к exact consumer revision и exact Project Pack digest. Synthetic fixture, GitHub CI и локальный Node shim такого доказательства не создают.

## Non-goals

EDGEREF-001 не изменяет реальные consumer repositories, не подключается к контроллерам, не делает deploy и не вводит vendor-specific SSH/file-system contract. Конкретные Wiren Board/другие edge adapters должны строиться поверх этого класса отдельными work units с собственной live boundary.
