# Executive Autopilot v1.6 — связанная архитектура

<!-- adwf-doc: skip(reason=architecture-diagram) -->
```text
Owner Portal / CLI
  ↓ OwnerIntentService
Durable Orchestrator (SSOT) ↔ Private Work Memory
  ↓
Runtime Supervisor → ActionExecutorRegistry
  ├─ GitHub Provider / Rulesets / PR / Merge
  ├─ Creative Agent Adapter / Agent Inbox
  ├─ Impact-aware CI
  ├─ Playwright exact-SHA Preview → provider-log readback bridge
  ├─ OwnerAuthorityAdapter
  ├─ Release Transaction
  └─ Delivery / Observation Adapter
  ↓
Trusted Context Compiler ← Provider Readback + Evidence + Cost + Policy
  ↓
Durable transition
  ↓
Executive projections / safe remote checkpoint
```

Ни Dashboard, ни GitHub labels, ни Work Memory не имеют права отдельно менять workflow phase. Remote ledger нужен для восстановления hosted runner и хранит safe state projection; private handoff остаётся вне public Issue.


## Проверяемость заявлений

Каждая крупная capability имеет запись в `.adwf/capability-traceability.json`. Наличие модуля без production wiring не допускает статус `IMPLEMENTED`; внешне недоказуемые свойства остаются `LIVE_NOT_VERIFIED`.
