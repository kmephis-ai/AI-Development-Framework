> **Исторический документ ADWF v1.5.** Для текущей версии используйте `docs/architecture/EXECUTIVE_AUTOPILOT_V1_6.md` и `docs/QUICKSTART_V1_6.md`.

# Executive Autopilot v1.5 — архитектура

## Главный поворот

v1.5 перестаёт считать один чат или один Actions runner местом, где «живёт проект». Проектовое состояние durable, а Creative Agent — заменяемый worker.

<!-- adwf-doc: skip(reason=architecture-diagram) -->
```text
Owner → Executive Portal → Product Brief/Roadmap
                         ↓
                  Runtime Supervisor
          ┌──────────────┼──────────────┐
          ↓              ↓              ↓
      Work Memory   Action Envelope   Remote Ledger
                         ↓
                   Creative/Provider
                         ↓
                deterministic evidence
                         ↓
                 Trusted Context
                         ↓
                 Durable Orchestrator
```

## Remote Runtime Ledger

GitHub-hosted runner одноразовый. Поэтому trusted workflow восстанавливает последний checkpoint из append-only GitHub Issue comments, проверяет hash-chain, исполняет bounded step и сохраняет следующий checkpoint. Ledger не является secret store и не должен содержать raw prompts/secrets/chain-of-thought.

## Action Envelope

Каждая фаза имеет deterministic idempotency key `run + phase + revision`. External adapter возвращает ограниченный Step Result: outcome, exact SHA, evidence refs, reason codes, cost и metadata. Неизвестные поля блокируются.

## Trusted Context

Caller передаёт идентификаторы, но не положительные факты. Provider visibility/rules/checks/runner/owner decision читаются через provider API. Evidence Resolver подтверждает producer, exact SHA, digest, TTL и policy hash. AssuranceSnapshot является единственной машинной проекцией для Executive status.

## Work Memory

Это не «память мыслей модели». Это сменный журнал проекта: brief, решения, ограничения, выполненное, verification, blocker, вопросы, следующий шаг, ссылки и handoff summaries.

## Preview

Reference engine — Playwright. `adwf preview` запускает desktop/mobile capture, console/network checks и простой accessibility smoke, затем строит exact-SHA manifest. Preview adapter исполняется вне trusted controller.

## CI

PR workflow сначала строит impact plan. Safety checks всегда выполняются, а дорогие внутренние suites включаются только при соответствующем изменении. Trusted controller остаётся API/readback lane и не исполняет PR code.

## Platform

Cross-platform lock abstraction использует `fcntl` только внутри POSIX backend и `msvcrt` на Windows. Platform smoke должен реально поднять Owner Portal и получить HTTP 200.
