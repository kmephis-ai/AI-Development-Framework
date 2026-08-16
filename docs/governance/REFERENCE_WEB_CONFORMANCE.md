# Reference Web Consumer Conformance

`WEBREF-001` — первый bounded unit этапа **v1.9.0 Heterogeneous Conformance & Human-by-Exception**. Его цель — проверить уже реализованный ADWF lifecycle на отдельном standard-web consumer, а не на self-host репозитории самого framework.

## Что считается reference consumer

Canonical fixture находится в `.adwf/reference-consumers/web/`. Это минимальный consumer-owned Web product:

- `package.json` содержит React dependency marker, поэтому existing Project Pack SDK детерминированно выбирает `react`;
- lint/test/build scripts используют только установленный Node.js и не требуют package registry;
- dev server использует built-in Node HTTP и слушает только loopback `127.0.0.1:4173`;
- consumer-owned `index.html`, `WEBREF.md`, `consumer-data.json` и scripts должны пережить guarded detach.

Fixture намеренно **не** создаёт собственные ADWF `SHARED_GUARDED` paths до adoption. Managed Surface v1 умеет fail-closed сохранить differing shared paths, но ещё не умеет их merge/overwrite.

## Canonical conformance chain

`reference_web_conformance.py` создаёт отдельный временный Git repository и связывает один отчёт со следующими authoritative primitives:

1. exact clean framework source SHA/tree + `MANIFEST.json` digest;
2. consumer seed HEAD/tree;
3. transactional adoption + durable transaction-bound snapshot;
4. sealed `.adwf-consumer/profile.json` и exact React Project Pack digest;
5. operational consumer HEAD/tree;
6. lint/unit/build через `ProjectExecutionSession` в disposable independent exact-revision Git clone;
7. exact-revision loopback preview + preview attestation + ProjectExecution evidence;
8. проверка, что canonical consumer checkout не был молча изменён runtime commands;
9. guarded detach из original adoption snapshot;
10. доказательство сохранности consumer-owned files и consumer profile;
11. fail-closed probe текущего same-revision re-adoption contract после committed detach.

Aggregate `REFERENCE_CONFORMANCE_REPORT` не копирует authoritative evidence: он хранит exact SHA/digest/transaction/execution references и self-sealed `report_sha256`.

## Browser modes

Production CLI использует `LIVE_BROWSER`: preview должен пройти реальный browser capture через canonical `preview_engine`.

`SIMULATED_TEST` разрешён только bounded unit tests, где miniature exact framework source предоставляет deterministic fake capture adapter. Такой отчёт нельзя интерпретировать как browser/runtime live evidence.

## Fail-closed adversarial expectations

Conformance обязана блокироваться или безопасно сохранять данные при:

- dirty/drifted framework source;
- symlink/type collision в managed surface;
- stale/tampered consumer profile или substituted Project Pack digest;
- tracked mutation, выполненной Project Pack command;
- forged report digest;
- preview/gate evidence, привязанном не к тому consumer HEAD или pack digest.

## Ограничения, которые PASS обязан показывать явно

Каждый v1 report содержит как минимум:

- `SHARED_GUARDED_MERGE_NOT_IMPLEMENTED` — differing pre-existing shared paths всё ещё блокируют adoption; WEBREF не расширяет write authority;
- `NETWORK_DECLARATION_ONLY_NOT_ENFORCED` — Project Pack network policy является декларацией; portable OS/domain sandbox не заявляется;
- `REFERENCE_NOT_LIVE_PROVIDER_EVIDENCE` — synthetic/reference fixture и CI не заменяют реальный downstream consumer/provider cycle;
- `READOPTION_REQUIRES_DISTINCT_PLAN_IDENTITY` — reference fixture успешно проходит same-revision re-adoption только потому, что preserved `SHARED_GUARDED` `.gitignore` меняет новый plan (`CREATE_PLANNED` → `KEEP_EXACT`) и тем самым создаёт другой transaction identity. WEBREF требует, чтобы второй transaction ID отличался от первого, и не превращает этот частный путь в общее обещание re-install для любого consumer.

Если у другого consumer post-detach plan останется идентичен прежнему, durable COMMITTED journal может корректно заблокировать повторное использование stale provenance. Универсальный lifecycle epoch/upgrade contract остаётся отдельной будущей задачей.

## Truth boundary

`REFERENCE_WEB_CONFORMANCE` остаётся `LIVE_NOT_VERIFIED`.

Даже provider-side successful synthetic Web run доказывает implementation/reference conformance, но **не** повышает автоматически `MANAGED_SURFACE_CONTRACT`, `PROJECT_PACKS`, `PROJECT_RUNTIME_SAFETY` или `CONSUMER_PROFILE_OVERLAY` до `LIVE_VERIFIED`. Их live boundary требует отдельный реальный downstream consumer repository/provider evidence cycle.

WEBREF-001 сам по себе не доказывает другие consumer classes. `ASREF-001` добавляет отдельный synthetic Apps Script/data-centric proof через тот же report/evidence model; это не превращает Web evidence в Apps Script evidence и не даёт live Google runtime claim. Edge/automation остаётся третьим независимым conformance unit.
