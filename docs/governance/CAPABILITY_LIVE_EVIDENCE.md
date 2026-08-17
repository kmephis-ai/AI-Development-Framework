# Capability Live Evidence Certification v1

## Зачем это нужно

`LIVE_VERIFIED` — это самый сильный факт в Capability Truth: он означает, что capability доказана не только кодом, unit/self-tests или reference fixture, а реальным внешним provider evidence. Поэтому строка вида `github:run/123` сама по себе больше не является доказательством.

GOV-021 вводит долговечную certification projection поверх уже существующих Evidence Graph/provider primitives. Она не заменяет Evidence Graph и не меняет его TTL/freshness semantics. Operational evidence может устаревать; immutable certification фиксирует исторически состоявшееся реальное доказательство exact revision.

## Канонические источники

- `.adwf/capability-traceability.json` — какой capability заявлен `LIVE_VERIFIED`;
- `.adwf/capability-live-evidence.json` — strict self-sealed certification records;
- `.adwf/schemas/capability-live-evidence-certification.schema.json` — contract;
- `.adwf/lib/capability_live_evidence.py` — offline validation + authenticated provider readback;
- `.adwf/scripts/validate_capabilities.py` — fail-closed resolution `live_evidence → certification`;
- trusted default-branch controller — свежий GitHub readback при изменении certification/truth surfaces.

## Два разных уровня проверки

### Offline / deterministic

Локальный self-test и Windows/Linux CI без сети проверяют schema, certification/registry SHA-256 seals, exact capability scope, exact Git SHA/tree identities, report digest, A/B transition claims, `external_source_unchanged=true` и `write_back_performed=false`.

Это делает certification переносимой и не превращает обычный self-test в зависимость от GitHub availability.

### Trusted provider readback

Когда PR меняет Capability Truth или live-certification surface, controller выполняется из trusted BASE и перечитывает provider facts заново. Применимость gate определяется из уже provider-verified exact BASE→HEAD changed-path set, восстановленного по immutable Git commit/tree objects; отдельный повторный PR-files readback не является authority. Для real external consumer upgrade proof controller проверяет exact GitHub workflow run, exact check-run/app, exact check output/report digest, exact ADWF source/target commit trees и exact external consumer commit tree.

Если exact diff/ancestry/tree completeness не доказаны, live-evidence gate остаётся `NOT_VERIFIED` и trusted controller должен дойти до публикации явного failing required context, а не завершиться до `_publish()`. Candidate не может ослабить этот gate собственной версией verifier: controller использует код и schema из exact PR BASE. Изменение самого controller остаётся owner-reserved trust-boundary change.

## Сертифицированный UPGRADE-003 proof

Первая certification `CERT-UPGRADE-003-PRIHRASH-EXTERNAL` фиксирует успешный provider proof:

- ADWF target `b2b9c76471c35458306f0dc54cf5be0c744e1787`, tree `c43c973f16b9cabc0115c1fa4830761f4d51a098`;
- GitHub run `31964580894`;
- check `adwf/external-consumer-upgrade-proof` / `95207824231` / GitHub Actions app `15368`;
- report SHA-256 `8f5443da480c0fc980585adf0d93acc386056449ed909d58d6dd475de26e7752`;
- external consumer `kmephis-ai/PrihRashOnline-v2@00659d0e423e4baf222b056e732b576887200891`, tree `c47ddccf2b13f50fc171f005ef3197fce20b25c5`;
- exact A `91056fc9153b3b3275a99c64adaf245122073bca` → B target → rollback A → retry B;
- external source unchanged; никакого write-back в PrihRash.

Эта certification разрешает `LIVE_VERIFIED` только для `CONSUMER_FRAMEWORK_UPGRADE_PLANNING` и `CONSUMER_FRAMEWORK_UPGRADE_TRANSACTION`.

## Что это НЕ доказывает

PrihRash не становится ADWF-managed или production-certified. Certification не доказывает Google Apps Script runtime, Google APIs, бизнес-данные, Edge/Wiren Board или Human-by-Exception programme completion. Она доказывает только generic consumer framework upgrade lifecycle на exact реальном внешнем snapshot в disposable provider workspace.
