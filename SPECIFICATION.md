# AI Development Framework v1.6 — нормативная спецификация

## Цель

ADWF v1.6 закрывает интеграционные разрывы аудита v1.5 и сводит framework к пяти связанным элементам:

1. **DurableState** — Durable Orchestrator + private Work Memory;
2. **TrustedContext** — Effective Policy + Evidence + Cost + Identity + Provider Readback;
3. **ProviderAdapter** — GitHub/GitLab HTTP contracts;
4. **ActionExecutorRegistry** — один canonical executor на durable phase;
5. **ExecutiveProjection** — Portal/Roadmap/Portfolio/Issue projections.

## Обязательные инварианты v1.6

- public GitHub standard hosted runner — default mandatory path;
- mandatory AI/API calls = 0;
- money budget mandatory automation = 0;
- caller не создаёт VERIFIED факты;
- PR не может self-authorize trust-boundary modification;
- Durable Orchestrator — единственный workflow SSOT;
- новая owner task не изменяет active Brief; она queue/supersede через отдельное действие;
- Continue привязан к exact Brief/SHA/Preview/Policy/provider identity и будит controller;
- Preview exact revision attested;
- release external version равна canonical internal version;
- public runtime persistence не содержит arbitrary private owner/agent text;
- ruleset readback проверяет bypass/source/check contexts;
- generated GitHub workflows равны Pipeline IR projection;
- missing live provider/deployment facts = `NOT_VERIFIED`.

## ActionExecutorRegistry

Фазы: `RECONCILE, AUTHORIZE, CLAIM, WORKSPACE, EXECUTE, OPEN_PR, CI, REVIEW, PREVIEW, OWNER_ACCEPTANCE, MERGE, PROMOTE, OBSERVE, DONE, CLEANUP, NEXT, RECOVERY`.

Creative Agent — optional adapter. Если adapter отсутствует, `EXECUTE/RECOVERY = WAITING_AGENT`; framework не подменяет генерацию кода фиктивным PASS.

## GitHub trust split

`ADWF PR` исполняет exact PR head с read-only token/no secrets. `ADWF Control` checkout default branch, не исполняет PR code и через API подтверждает run/PR/checks/diff/approval. Для trust-boundary PR `governance-gate` обязателен.

## GitHub bootstrap

Seed checks → prove one GitHub Actions integration → branch ruleset → runtime-anchor tag ruleset → Project Pack governance PR → owner approval/merge → readback. До конца цепочки статус не должен быть `VERIFIED`.

## Runtime persistence

Public checkpoint — safe projection, не Work Memory. Hash-chain плюс provider object chain плюс protected annotated tag anchors обнаруживают удаление/перезапись истории. Private memory не публикуется.

## Preview

Built-in Playwright target: desktop 1440×900, mobile 390×844. Local preview source must be exact checked-out SHA. В hosted PR safe marker из job log считается только данными недоверенного job, пока trusted controller не подтвердит exact workflow run, все три required checks и единый GitHub Actions source, после чего сохраняет provider-bound preview attestation. Remote source требует отдельный deployment attestation/readback. Screenshot hashes и preview digest привязаны к subject SHA.

## Release

Auto release — двухфазная модель: `plan/prepare version-bump` → ordinary governance cycle → build/publish exact merged internal version. Quantity of Issues не определяет SemVer.

## Performance evidence

Minimum default sample count 30; cancellation metric требует не менее 10 superseded samples. Queue показывается отдельно и не проваливает framework budget. Evidence содержит per-impact groups и отдельную per-pack projection; неизвестный pack или недостаточная выборка не превращаются в PASS.

## Delivery

Adapters: `NONE`, `COMMAND`, `REFERENCE_LOCAL`. `REFERENCE_LOCAL` нужен только для deterministic reference-cycle contract и всегда сообщает `production_verified=false`. `COMMAND` не доверяет exit code: adapter обязан записать exact `subject_sha`, 64-hex artifact digest, `provider_readback=true`, readback id и evidence refs; отсутствие structured attestation = `NOT_VERIFIED`.

## Capability traceability

`.adwf/capability-traceability.json` является machine-readable картой «заявление → entrypoint → production path → verification → live boundary». Статусы `LIVE_NOT_VERIFIED`/`OPTIONAL_ADAPTER` запрещают документации выдавать локально недоказанную возможность за эксплуатационный VERIFIED.

## Критерий завершения package-level v1.6

Все unit/adversarial/contract tests, CI/pipeline/docs/framework/capability validators и Linux functional smoke PASS; Package Integrity/Configuration VERIFIED; Control Plane/Product Health остаются NOT_VERIFIED без живого GitHub/reference product.
