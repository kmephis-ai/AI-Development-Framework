> **Исторический документ v1.5.** Текущий normative implementation report: `docs/V1_6_IMPLEMENTATION_REPORT.md`.

# AI Development Framework v1.5 — Implementation Report

## Scope релиза

v1.5 реализует архитектурную волну **Executive Autopilot** поверх v1.4: cross-platform runtime, Work Memory, Project Packs, impact-aware CI, functional platform smoke, Playwright Preview adapter, GitHub ruleset/readback, provider-authenticated owner decisions, production ProviderContract path, Runtime Supervisor, remote GitHub checkpoint ledger и semantic owner-gated release automation.

## Что проверяется package tests

Тестируется детерминированная логика, schemas, migrations, trust/cost/evidence contracts, Windows-safe import path, fake preview runner, ruleset verification, remote ledger hash-chain, owner decision store, impact routing и platform smoke script contract.

## Что остаётся live NOT_VERIFIED

Чистый ZIP не способен сам доказать внешние факты. До reference repository cycle остаются `NOT_VERIFIED`:

- реальный GitHub ruleset create/readback;
- реальные GitHub checks и runner provenance;
- настоящий browser capture конкретного проекта;
- authenticated owner decision через настоящий provider;
- external creative agent end-to-end;
- merge/release/deployment;
- exact deployed revision и observation.

## CEO-001

Главный полевой acceptance test следующего этапа: новый public repository + Windows owner + естественная задача → PR/CI/recovery/preview → `ПРОДОЛЖИТЬ` → release/deploy/observation без необходимости работать с Git/JSON/Actions.

Уровень `9.5/10` является эксплуатационной характеристикой и не присваивается данным package report.

## Финальная локальная верификация до упаковки

- deterministic/adversarial regression suite: **230/230 PASS**;
- CI static security: PASS;
- executable documentation contracts: PASS;
- Pipeline IR consistency: PASS;
- framework structure: PASS;
- Linux functional Owner Portal smoke: PASS.

Windows functional HTTP smoke включён в `adwf-platform-smoke.yml`, но текущая Linux-среда сборки не является доказательством фактического выполнения Windows runner. Поэтому cross-platform code capability тестируется локально/статически, а Windows provider execution остаётся полевым release gate.

## Осознанно не выдаётся за готовое

Optional Creative Agent API adapter не является обязательным `$0` компонентом. Пакет предоставляет durable Agent Inbox/Action Envelope для ChatGPT/Codex/Claude/человека и допускает отдельный optional provider adapter после authorization/cost guard. Полностью unattended генерация кода без какого-либо внешнего AI runtime не заявляется. GitLab Board parity и production-specific deployment packs остаются последующей P1/P2 работой; GitHub public является каноническим v1.5 путём.
