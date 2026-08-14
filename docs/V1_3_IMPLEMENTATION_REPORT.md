# ADWF v1.3 — отчёт о реализации и независимая оценка

Версия: `1.3.0`  
Codename: **Executive Autopilot & Evidence-Driven Recovery**  
Дата сборки: 2026-08-13

## 1. Итог для владельца

v1.3 переводит framework от набора сильных fail-closed компонентов к проверяемому ядру автономного цикла. Владелец формулирует результат обычным языком, а система хранит намерение, организует работу, доказывает точный результат и показывает одно продуктовое решение.

Ключевая формула:

> ИИ предлагает и создаёт → Effective Policy разрешает → CI/runtime доказывают → владелец принимает продуктовый результат.

Релиз не выдаёт пакетную проверку за здоровье установленного продукта. Чистый template имеет `Package Integrity = VERIFIED`, `Configuration = VERIFIED`, но `Control Plane` и `Product Health = NOT_VERIFIED` до подключения repository, deployment и свежего evidence.

## 2. Фактическая зрелость

| Контур | Оценка package-level | Что реально есть | Что требует live certification |
| --- | ---: | --- | --- |
| Policy / trust / cost | 8.5/10 | executable hash-bound policy, base-SHA diff, zero-cost guard | provider permissions, reviewer allowlist, quota facts |
| Orchestration core | 8/10 | durable phases, restart, idempotency, budgets, tamper detection | постоянно вызывающий adapter/dispatcher в целевом repo |
| Evidence / health | 8.5/10 | append-only exact-SHA/runtime graph, artifact digest, TTL | реальные product Golden Paths и deployment revision |
| Incident / recovery | 7.5/10 | sanitized store, fingerprint, recipes, H0–H6 authorization | external tail anchor, sandbox executor, production observation |
| Owner experience | 7.5/10 | Product Brief, preview/acceptance contract, Markdown+HTML panel | реальный preview adapter и owner decision writeback |
| GitHub implementation | 8/10 | trusted API classifier, provenance, CAS saga | repository installation and private-Free process discipline |
| GitLab implementation | 6/10 | local includes, runner domains, normalized reconciliation | parity trusted diff/saga/live end-to-end certification |

Сводная оценка framework package: **около 8/10**. Сводная оценка «автопилот уже работает в конкретном продукте» не выставляется до установки; template честно остаётся `NOT_VERIFIED`. Это принципиальное улучшение по сравнению с v1.2.1, где компонентное ядро было сильным, но замкнутый operating loop и experience learning почти отсутствовали.

## 3. Закрытые P0 дефекты v1.2.1

### 3.1 Fail-open continue

Ранее активный lease мог вернуть `CONTINUE_EXISTING` до policy evaluation и без свежего heartbeat. Теперь `authorize_next_action` является общей границей для claim/continue/review/recovery и проверяет:

- строгий queue contract;
- Package/Config/Control Health;
- provider status, freshness и cost `$0`;
- один Writer, lease identity, workspace identity и worker;
- TTL и heartbeat, включая future/stale timestamps;
- conflict domain и executable Effective Policy.

Stale lease нельзя продлить; reconciliation переводит работу в Recovery.

### 3.2 Ложнозелёный Product Health

Строки `runtime.smoke=PASS` больше не доказывают здоровье. Истина выводится из `.adwf-runtime/evidence/events.jsonl`:

- append-only sequence и SHA-256 chain;
- exact commit/runtime revision;
- artifact digest и provenance;
- TTL bounded policy;
- generated index, который обязан точно совпадать с log;
- latest failure supersedes older pass.

Повреждение chain/index/artifact даёт `BROKEN`; отсутствие evidence — `NOT_VERIFIED`.

### 3.3 Незамкнутая оркестрация

Добавлен provider-neutral Durable Orchestrator с полным циклом от reconcile до observation/cleanup/next. Каждый event имеет idempotency key и hash; state — revision CAS, deadline, max attempts/cycles. Retry требует явной transient classification. Новый Writer блокируется при активном или повреждённом journal.

Ядро не притворяется daemon: GitHub/GitLab/local adapter должен вызывать следующий шаг. Это остаётся обязательной задачей установки, а не скрытым обещанием framework.

### 3.4 Неатомарный GitHub transition

GitHub не предоставляет одной транзакции для Issue body+labels. v1.3 использует честную durable saga:

1. read current issue + revision;
2. ETag/CAS add target label;
3. patch только marker body;
4. remove old label;
5. readback exact postcondition.

После crash выполняется resume того же idempotency key. Неизвестное remote state или параллельная правка человека даёт `RECOVERY_REQUIRED`. Есть явная безопасная compensation.

### 3.5 Недоверенная self-attestation

Fast-feedback PR check больше не считается достаточным trusted gate. Default-branch controller через GitHub API:

- получает base/head SHA и весь changed-file set;
- читает trust policy именно из base SHA;
- проверяет protected diff content без checkout/execute PR code;
- блокирует смешанный trust+feature PR и gate weakening;
- требует уникальный fresh check от allowlisted GitHub App;
- требует latest independent exact-SHA approval от allowlisted reviewer.

Protected change проходит только отдельным GOV/R4 flow. Пустой allowlist блокирует, а не принимает любого пользователя.

## 4. Целевая архитектура

Шесть плоскостей разделяют виды истины:

1. **Owner Intent** — исходный запрос и Product Brief.
2. **Creative** — интерактивный ChatGPT/Codex, план, код, explanation, candidate repair.
3. **Deterministic Control** — policy, schemas, state machines, leases, trust/cost.
4. **Experience** — incidents, fingerprints, recipes, regressions, ADR, provenance.
5. **Delivery** — artifact, preview, acceptance, promotion, observation, rollback.
6. **Executive** — производная CEO projection.

SSOT разделён по типу:

- normative: goals, policies, schemas, ADR — versioned repository;
- operational: свежий provider/runtime snapshot;
- evidence: content-addressed exact-subject result с TTL;
- projection: dashboards, indices, generated files — полностью regenerable.

Конфликт источников одного типа даёт `CONFLICT`, а не молчаливый выбор «главного». Chat и agent memory являются cache/context.

## 5. Owner Experience

### Product Brief

Хранит исходный очищенный запрос, цель, ценность, ожидаемый outcome, наблюдаемые критерии, visual expectation и constraints. ИИ может улучшить формулировку, но исходное намерение остаётся отдельным фактом.

### Preview и acceptance

Preview contract содержит exact 40-char HEAD SHA, SHA-256 immutable manifest/archive, optional URL и screenshot digests. URL сам не является evidence. Acceptance связан с `brief_id + head_sha + preview_digest`; любое изменение делает его `STALE`.

### Control Center

Два бесплатных presentation слоя:

- `CONTROL_CENTER.md` и один обновляемый Dashboard Issue;
- self-contained `CONTROL_CENTER.html` без JS, внешних библиотек или hosted provider.

Главный слой показывает продукт, человеческий changelog, preview, machine verification, owner acceptance, incidents/healing, cost и одно действие. Технические доказательства скрыты вторым уровнем. Все HTML values экранируются; unsafe URL не становится ссылкой.

### Bug intake

Форма задаёт владельцу только: ожидание, фактический результат, место/время, влияние и optional screenshot/video. Normalizer sanitizes secrets/PII, создаёт стабильный fingerprint и отделяет факты от гипотез. Exact fingerprint может deduplicate; semantic similarity не закрывает Issue автоматически.

## 6. Safe Healing и организационное обучение

Корректный термин — не «модель переобучилась», а «система сохранила проверенный способ и применит его при точном повторении».

Ladder H0–H6 реализует observe, confirm, recreate, certified recipe, verify, propose и controlled rollback/promotion. Ограничения:

- zero cost, no mandatory AI/API;
- budgets attempts/time/actions/files;
- exact fingerprint, policy hash, tool/runtime versions, expiry;
- disposable sandbox/branch;
- protected paths и actual-diff verification;
- no shell/network/destructive action;
- circuit breaker и automatic quarantine после false heal.

Recipe lifecycle: `DRAFT → SHADOW → CERTIFIED → ACTIVE → QUARANTINED → RETIRED`. Certification требует independent approval и минимум двух shadow passes. Production policy, secrets, provider registry, workflow trust boundary, dependency source и visual baseline никогда не меняются failing job.

## 7. Сравнение с передовыми системами

### Vercel

Заимствовано разделение готовой сборки, preview, checks и production promotion. Не заимствована обязательная зависимость от hosted Vercel: default preview local/self-hosted/static, потому что pricing/quota может измениться.

### GitLab

Заимствованы local includes, DAG/needs, interruptible superseded jobs, resource groups, Review App contract и selective retry по причинам. Retry `always` отвергнут: deterministic failure нельзя маскировать повтором.

### Claude Code

Заимствованы harness-enforced permissions, deterministic lifecycle hooks, versioned repository instructions и независимый adversarial review. Agent Teams остаются optional accelerator: coordination/token cost не может быть основой correctness.

«Claude Engineer» — независимый OSS, не Anthropic product. Идея tool-gap detection преобразована в безопасный flow `TOOL_PROPOSAL → sandbox/tests → shadow → trust-gated PR`; runtime hot-load в trusted plane запрещён.

### Kubernetes / Argo Rollouts / Google SRE

Self-healing трактуется как возврат к желаемому состоянию, не исправление любого application bug restart'ом. Inconclusive analysis ставит процесс на pause/human decision. Incident closure требует impact, cause/evidence, mitigation, regression и отслеживаемые preventive actions.

## 8. FREE_ONLY и GitHub Free

Mandatory path не вызывает ChatGPT/Codex/Claude API. Интерактивный агент может использовать уже разрешённую пользователем подписку, но это separate optional capability и не условие merge.

Provider registry v3 различает:

- `FREE_VERIFIED`;
- `INCLUDED_QUOTA`;
- `CONDITIONAL_FREE`;
- `OWNER_PROVIDED`;
- `METERED`, `PAID`, `UNKNOWN`, `STALE` — всегда blocked.

Owner-provided self-hosted resource требует свежего attestation, что underlying control plane не является скрыто metered. Quota capability требует usage, storage, repository visibility, runner class и zero-overage evidence.

GitHub Free facts не смешиваются:

- 2,000 hosted Actions minutes/month — included quota для private repository;
- public standard runners бесплатны, но имеют technical limits;
- larger runners платные;
- 500 MB Actions artifacts/Packages storage и 10 GB repository cache — отдельные ограничения;
- private GitHub Free не считается имеющим бесплатное protected-branch/ruleset enforcement.

Значения plan/quota должны перепроверяться по официальным источникам перед включением; stale fact блокирует.

## 9. Performance engineering

- cheap deterministic failure first;
- cancel superseded PR, но не trusted transaction/release;
- read-only независимые checks параллельны, Writer/merge/promotion serial;
- lockfile-keyed cache не является correctness source;
- failure-only artifacts с retention один день;
- bounded API pagination/content size;
- no PR checkout in trusted controller;
- one active Writer reduces merge-conflict/rework cost;
- CI metrics: p50/p95, queue, time-to-first-failure, flake.

Effectiveness включает owner time-to-decision, human interruptions per completed outcome, repeat incident, recipe precision и false-heal/rollback rate. Один «auto-heal success rate» является опасной vanity metric.

## 10. Контракты и команды v1.3

Новые CLI paths:

- `orchestration-start/step/status`;
- `incident-normalize/record/summary`;
- `healing-evaluate/verify`;
- `owner-brief/preview/acceptance/changelog`;
- `ci-setup-plan`;
- `render-control-center --format md|html`.

CI Setup Assistant только читает проект, предлагает argv и задаёт максимум один человеческий вопрос. Он не пишет файлы, не включает provider и не требует AI/API.

## 11. Validation и release truth

Contract/adversarial suite покрывает policy matrix, provider status, private/public separation, trust diff/provenance, leases/heartbeat, durable orchestration, evidence graph, incidents, safe healing, owner acceptance, migration, remote saga, CI supply chain и negative canaries.

Финальная сертификация пакета должна подтвердить:

- все unit/adversarial tests PASS;
- static CI, policy compiler, docs freshness, schemas и manifest PASS;
- package doctor VERIFIED;
- full doctor ожидаемо NOT_VERIFIED только по live Control/Product facts;
- две независимые deterministic ZIP build дают один SHA-256;
- archive не содержит `.adwf-runtime`, pycache, locks, secrets или symlinks.

Точный test count и release SHA фиксируются в `.adwf/reports/adversarial-results.json` и release checksum после финальной сборки.

## 12. Остаточные ограничения и следующий приоритет

P0 для установки:

1. настроить реальные project gates, Golden Paths, preview и owner decision writeback;
2. указать trusted reviewers и сертифицировать runner isolation;
3. подключить adapter, который действительно ведёт Durable Orchestrator по всем фазам;
4. сохранить incident/evidence tail anchors вне перезаписываемого runtime каталога;
5. доказать exact deployed revision и observation window.

P1 следующей версии:

- GitLab parity для default-branch provider diff/provenance/saga;
- reference Playwright static preview adapter в pinned image;
- certified recipe executor с disposable sandbox и signed/externally anchored evidence;
- incident → regression/ADR/action-item automation;
- release adapter к last verified artifact с canary/observation;
- richer HTML owner decision writeback без обязательного hosted service.

P2:

- generated cross-incident knowledge graph;
- optional adversarial multi-agent diagnosis;
- cross-project export/import только sanitized patterns;
- DORA + owner/effectiveness scorecard.

## 13. Вердикт

v1.3 является существенным архитектурным переходом: правила больше не являются обещанием модели, Product Health — строкой состояния, а «самолечение» — бесконтрольным retry. Framework готов как проверяемый пакет и основание для controlled installation. Он не объявляет установленный продукт автономным или здоровым до фактической live certification.

Нормативные внешние источники перечислены в `docs/REFERENCES.md`; изменяемые provider facts имеют TTL и не превращаются в вечную истину документации.
