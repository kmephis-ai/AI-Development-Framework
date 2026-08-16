# AI Work Contracts: `AIWorkPackage` и `AIWorkResult`

## Назначение

AI Work Contracts превращают creative AI handoff из свободного текста в ограниченный machine-verifiable контракт. Они не создают новый orchestrator: существующие Durable Orchestrator, Runtime Supervisor, Work Memory и Agent Inbox остаются единой цепочкой выполнения.

Контракт решает две разные задачи:

- `AIWorkPackage` фиксирует **что именно разрешено сделать** и от какого exact Git состояния;
- `AIWorkResult` фиксирует **что creative executor заявляет, что сделал**.

Заявление результата не является доказательством. Даже schema-valid `PASS` остаётся `LOW_TRUST` до независимой trusted/provider verification.

## `AIWorkPackage`

Package компилируется из durable runtime facts и Work Memory для creative phases `EXECUTE` / `RECOVERY`. Он связывает:

- `run_id`, `roadmap_id`, `issue_id`, revision и phase;
- exact `base_sha`;
- work type и risk;
- цель, acceptance criteria и verification plan;
- conflict domains;
- positive allowlist `allowed_write_surfaces`;
- `forbidden_write_surfaces`, которые всегда имеют приоритет над allowlist;
- обязательные evidence claims;
- `monetary_budget_usd = 0`;
- immutable `package_id` и `package_digest`.

Если exact base отсутствует, package не создаётся. Если run state уже содержит более узкие write/evidence ограничения, compiler использует их. Compatibility default `allowed_write_surfaces=["**"]` означает только широкий proposal scope для существующих consumers; он **не** разрешает merge или обход trust gates. `.git/**` и `.adwf-runtime/**` запрещены по умолчанию, а downstream PR classifier/ruleset по-прежнему контролирует framework trust surfaces.

## `AIWorkResult`

Result обязан быть привязан к exact `package_id`, `package_digest` и `base_sha`. Для `PASS` проверяются как минимум:

- валидный produced `head_sha`;
- declared changed paths;
- каждый changed path входит в positive allowlist и не входит в forbidden surfaces;
- verification claims присутствуют;
- все required evidence claims объявлены;
- при наличии изменений produced head не равен base;
- `cost_usd = 0`.

`result_id` и `result_digest` создаёт trusted canonicalizer. Low-trust executor не задаёт canonical timestamp и не может подменить package identity.

## Runtime Supervisor и Action Envelope

Для creative phase Action Envelope v3 включает canonical `work_package` и его digest. Runtime Supervisor не создаёт параллельный state machine: package является bounded contract текущей orchestration phase.

Command-based creative adapter получает request path через существующий `ADWF_ACTION_REQUEST`. Его result проходит contract canonicalization. Contract-invalid `PASS` превращается в fail-closed результат и не продвигает run.

## GitHub Agent Inbox

Agent Inbox остаётся low-trust каналом. Публичный request публикует только безопасную projection package:

- identity/digest;
- exact base SHA;
- phase/work type/risk;
- allowed/forbidden surfaces;
- required evidence;
- zero-cost constraint.

Цель и acceptance criteria из Work Memory не копируются в публичную projection автоматически. Это уменьшает риск случайной публикации контекста проекта.

Agent result v3 должен вернуть package binding и bounded claims. Provider comment metadata сохраняется отдельно. Ни comment, ни actor, ни schema-valid result не являются trusted evidence сами по себе.

## Trust model

Цепочка истины:

`durable run/work memory → AIWorkPackage → low-trust creative execution → AIWorkResult claim → trusted CI/provider/evidence verification → orchestration decision`.

Нельзя сокращать её до `AI сказал PASS → DONE`.

Package ограничивает creative executor, но не выдаёт ему authority. Exact-SHA checks, protected branch rulesets, governance/trusted gates, Owner-Attestation и provider readback остаются независимыми enforcement layers.

## FREE_ONLY

Work Package и Result фиксируют нулевой денежный бюджет. Контракты не требуют внешнего AI API и не добавляют mandatory paid provider. Конкретные creative adapters остаются заменяемыми.

## Backward compatibility

Runtime Supervisor создаёт package нативно. GitHub Agent Inbox сохраняет bounded compatibility fallback для старого internal caller, если envelope ещё не содержит package; fallback компилируется детерминированно из существующего envelope + Work Memory и не расширяет authority.

## Что не входит в этот capability

AI Work Contracts не являются долговременным Decision/Requirement Ledger. Цепочка `Owner Intent → Requirement → Decision → Capability → Feature → Work Unit → Evidence` реализуется отдельным следующим capability, чтобы не смешивать execution contract и product/architecture traceability в один oversized work unit.

## Qualified Creative Agent invocation boundary

`AGENTQUAL-001` не повышает trust creative output. Command executor принимается только через versioned Creative Agent qualification registry/report; raw `ADWF_AGENT_COMMAND` без qualified adapter блокируется. Qualified command получает secret-filtered environment и exact `AIWorkPackage`, а возвращаемый `AIWorkResult` остаётся `LOW_TRUST` до downstream trusted/provider verification. `reference-local` является deterministic offline qualification fixture, а не внешним AI/provider evidence.
