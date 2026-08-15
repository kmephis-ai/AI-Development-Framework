# ADWF Foundation Architecture Audit — 2026-08-15

**Audit ID:** `ADWF-FOUNDATION-2026-08-15`  
**Repository:** `kmephis-ai/AI-Development-Framework`  
**Factual baseline:** `main@8f47de35957297037b2516d19e9b9775ace1e734`  
**Mode:** `READ_ONLY_DESIGN_ONLY`  
**Decision status:** `ACCEPTED_AS_PLANNING_BASELINE`  

> Этот документ фиксирует выводы и принятые направления. Он не является доказательством текущего live state. Перед каждым следующим аудитом фактическое состояние GitHub/CI/provider/runtime должно перечитываться заново.

## 1. Executive verdict

ADWF уже имеет сильное реальное ядро: fail-closed governance, protected `main` без bypass, exact-SHA trust, trusted/untrusted separation, Durable Orchestrator, Runtime Supervisor, Work Memory, provider readback, evidence/policy/cost/healing mechanisms, Project Packs, migrations и adversarial tests.

Главная проблема следующего этапа — не нехватка новых подсистем, а незавершённое связывание существующих компонентов в доказанный consumer lifecycle.

Стратегическое решение:

- **не переписывать ADWF**;
- **не строить новые subsystems при возможности расширить существующие**;
- превратить существующий framework в Engineering Operating System через несколько фундаментальных контрактов и live consumer evidence;
- после `FOUNDATION_READY` сделать развитие framework преимущественно consumer-driven.

## 2. Подтверждённые сильные стороны baseline

На baseline аудита были подтверждены:

- защищённый `main` без bypass;
- обязательные `fast-feedback`, `adwf/governance-gate`, `adwf/trusted-gate`;
- immutable runtime-anchor ruleset;
- successful `ADWF Main`;
- successful Ubuntu/Windows platform smoke;
- FREE_ONLY provider policy для текущего public GitHub contour;
- реальная реализация orchestration/policy/evidence/healing/preview/pack/migration механизмов;
- adversarial fixtures для false progress, stale review, blocked dependencies, trust downgrade, conflicts, cycles, architecture drift, debt budget, oversized work, false PASS и split brain.

## 3. Главные gaps

### P0 — Capability/State Truth

Текущий capability catalogue слишком легко смешивает наличие теста с фактической live verification. Требуется Capability Truth Model v2 с явными состояниями минимум:

`NOT_DESIGNED → DESIGNED_ONLY → PARTIAL → IMPLEMENTED → LIVE_NOT_VERIFIED → LIVE_VERIFIED → DEPRECATED/BLOCKED`.

### P0 — Roadmap Intelligence

Детекторы Roadmap Quality уже существуют, но executive progress/projection пока должен быть связан с capability/outcome truth, critical path и evidence, а не с количеством закрытых элементов.

### P0 — AI Work Contracts

Текущий Issue/Work Memory contract необходимо эволюционировать в first-class `AIWorkPackage` и `AIWorkResult` с exact `base_sha`, allowed/forbidden surfaces, acceptance, verification и evidence requirements.

### P0 — Decision/Requirement Traceability

Нужна долговременная цепочка:

`Owner Intent → Requirement → Decision → Capability → Feature → Work Unit → Evidence`.

Chat и runtime Work Memory не являются долгосрочным SSOT решений.

### P0 — Consumer Lifecycle / Portability

До Foundation Ready должны быть доказаны:

`bootstrap → adopt existing → update → migrate → rollback → detach → recover`.

Ключевой invariant: **PROJECT MUST OUTLIVE FRAMEWORK**.

### P0 — Heterogeneous Conformance

Нужно доказать работу минимум на трёх классах consumer projects:

1. standard software/web;
2. Apps Script/data-centric;
3. edge/automation (Wiren Board class).

### P0 — Foundation Gate

`FOUNDATION_READY` должен стать machine-verifiable gate, а не заявлением в документации.

## 4. Принятый release train

### v1.7.0 — Foundation Truth & Work Intelligence

Детализируется первым и только до AI-sized Work Units.

Цели:

- Capability Truth Model v2;
- Roadmap hierarchy/DAG;
- Verified Outcome Progress;
- `AIWorkPackage`;
- `AIWorkResult`;
- Decision Ledger/traceability;
- improved dynamic work sizing.

### v1.8.0 — Consumer Lifecycle & Portability

Цели:

- Managed Surface Contract;
- bootstrap/adoption;
- upgrade/migration/dry-run;
- rollback;
- detach;
- recovery;
- formal Project Pack SDK;
- environment/data safety baseline.

### v1.9.0 — Heterogeneous Conformance & Human-by-Exception

Цели:

- reference web consumer;
- reference Apps Script consumer;
- reference edge consumer;
- Functional Truth;
- Visual Truth;
- full owner-intent cycle;
- Agent Qualification baseline;
- supply-chain/data/geo hardening where evidence justifies it.

### v2.0.0 — Engineering OS Foundation Stable

Scope intentionally small:

- machine `FOUNDATION_READY_GATE`;
- final three-class conformance;
- recovery proof;
- compatibility/support policy;
- final adversarial audit;
- documentation consolidation;
- rule that framework evolution after 2.0 is primarily consumer-driven.

## 5. Critical path

1. Capability/State Truth.
2. Roadmap DAG + Verified Outcome Progress.
3. AI Work Contracts + Decision/Requirement Traceability.
4. Consumer Lifecycle + Managed Surfaces + Recovery.
5. Project Pack SDK + Environment/Data Safety.
6. Three-class Reference Consumer Conformance.
7. Human-by-Exception end-to-end proof.
8. Final adversarial audit + `FOUNDATION_READY`.

Security, Cost, Supply Chain and Recovery remain parallel mandatory constraints and may block Foundation Ready.

## 6. Rolling-wave planning decision

Не создавать сейчас сотни Issues на весь 2.x horizon.

Правило:

- long horizon хранится как Goals/Epics/Features;
- подробно декомпозируется только текущий release;
- GitHub Issue начинается на уровне самостоятельного AI Work Unit;
- agent-internal steps не становятся Issues без самостоятельного outcome/evidence;
- перед каждым следующим release decomposition пересчитывается по фактическому velocity, context pressure, CI cost, review findings и consumer evidence.

## 7. Что сознательно НЕ строить сейчас

Без новой реальной потребности отклонены или отложены:

- Kubernetes;
- Kafka/event mesh;
- service mesh;
- Redis;
- отдельная orchestration DB;
- vector DB для project memory;
- собственный Git/CI backend;
- обязательные paid/unknown AI APIs;
- sophisticated cross-repo distributed orchestration;
- predictive ML sizing;
- большой always-on observability stack.

Новый аудит не должен возвращать эти предложения как default recommendation без нового consumer evidence.

## 8. Audit programme

Следующие крупные независимые аудиты целесообразны только после появления нового evidence:

- после v1.7: Architecture & Complexity Review;
- во время/после v1.8: Lifecycle & Recovery Audit;
- после data/pack contracts: Security/Privacy/Supply-Chain Review;
- после v1.9: Reference Consumer Conformance Audit;
- перед 2.0: Final Adversarial Foundation Audit.

Не рекомендуется плодить отдельные большие аудиты без изменения factual baseline.

## 9. Foundation Ready minimum proof

`FOUNDATION_READY = PASS` только если одновременно доказаны:

- governance/trust gates;
- complete consumer lifecycle including detach/recover;
- full AI development lifecycle from Owner Intent to observed result;
- three reference consumer classes;
- protection from false progress;
- recoverability under interrupted/lost/corrupted state scenarios;
- security/data/cost constraints;
- Human-by-Exception owner experience without routine technical intervention.

## 10. Implementation truth at audit acceptance

Принятие этого аудита **не означает**, что рекомендации уже реализованы.

На момент принятия в частности ещё не доказаны как завершённые:

- Capability Truth Model v2;
- first-class AIWorkPackage/AIWorkResult;
- Managed Surface Contract;
- safe detach conformance;
- Apps Script reference pack;
- edge reference pack;
- three-class conformance lab;
- machine `FOUNDATION_READY` gate.

Следующие изменения должны обновлять статус только по repository/provider/runtime evidence.

## 11. Правило для следующего аудита

Следующий независимый аудит обязан начать не с чистого листа, а с вопроса:

> «Что изменилось относительно `ADWF-FOUNDATION-2026-08-15`, какие принятые решения выполнены, какие остаются открыты, какие были опровергнуты новой реальностью и появились ли новые gaps?»

При этом baseline SHA этого документа не должен использоваться как актуальный SHA будущего `main`.
