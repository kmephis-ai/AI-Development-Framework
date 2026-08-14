# ADWF v1.4.0 — Implementation & Verification Report

## 1. Назначение

v1.4 переводит v1.3 из self-hosted-oriented reference architecture в **public-GitHub-first, zero-cost, fail-closed control plane**. Scope релиза строится напрямую по P0-аудиту: сначала убрать неправильный default и false trust, затем реализовать доказуемый reference path для владельца. P1 включён только там, где он нужен для целостности v1.4.

## 2. Что закрыто на уровне package contracts

1. `FREE_PUBLIC_GITHUB`: `GITHUB_HOSTED_STANDARD`, exact `ubuntu-24.04`, larger runners `BLOCK`, mandatory AI/API = `0`.
2. GitHub fast lane: один `fast-feedback` job; stale PR run отменяется.
3. Trusted lane: default-branch checkout only; PR code/artifacts не исполняются; provider API readback привязывает `adwf/trusted-gate` к exact PR HEAD.
4. `Pipeline IR` + drift validator: profile/config/request/workflows/required checks проверяются как единая конфигурация.
5. Strict JSON duplicate-key rejection для доверенных конфигурационных путей и новых provider/evidence contracts.
6. `Trusted Context Compiler`: caller request отделён от autonomy/health/gates/cost/owner facts; trusted decision содержит `policy_hash`.
7. `Evidence Resolver`: exact SHA + artifact digest + policy hash + producer/run readback + TTL + external anchor.
8. `AssuranceSnapshot`: CEO `Machine Verified` строится только из единого snapshot, а не из строк `state.gates`.
9. Cost/UI fail-closed: только явный zero-cost ALLOW даёт `$0 VERIFIED`; иначе `NOT_VERIFIED/BLOCK`.
10. Preview URL policy: HTTPS без credentials либо проверенный local artifact path; `//host`, HTTP, backslash и credential URLs блокируются.
11. Reference visual manifest: desktop/mobile digests, exact SHA, pinned browser/version/OS metadata и accessibility summary field.
12. Owner decision contract: actor, authority, nonce, source, provider/local authentication mode, exact SHA, preview digest, brief ID и policy hash.
13. Local Owner Portal: loopback-only, CSRF/session token, idempotent decision ledger, sandboxed executive dashboard, natural-language intent capture.
14. `adwf init`, `adwf start`, `adwf dashboard serve` как reference UX; Windows double-click launcher включён в package.
15. Provider HTTP contract layer: dependency injection, bounded retry, pagination, rate-limit, auth/CAS/timeout/malformed-response handling, mutation readback.
16. Local mock HTTP suite: никакой внешней сети/API для mandatory tests.
17. Executable docs: every code fence = `run | parse | skip(reason)`; internal links и version expectations проверяются.
18. Pinned/hash-locked `mypy` gate критического ядра подготовлен для GitHub CI с ratchet target set.
19. Transactional migration `v1.3 → v1.4`: `PREPARED → COMMITTED`, backup/readback, rollback и fault injection после каждой записи.
20. Existing v1.3 fail-closed ядро сохранено и проходит regression suite после final verification.

## 3. Что закрыто частично и требует live reference cycle

- Full loop contracts присутствуют, но release не объявляет доказанным production-autopilot до реального GitHub цикла `intent → PR → CI → preview → acceptance → merge/promotion → deployed revision → observation`.
- Reference visual manifest и Owner Portal есть; фактический Playwright screenshot adapter для каждого продуктового стека должен быть подключён/проверен в целевом проекте.
- Trusted GitHub controller проверяет exact HEAD через API, но конкретные rulesets/branch protection/Actions permissions становятся `VERIFIED` только после live readback.
- `mypy` gate встроен в hosted GitHub CI; локальный release environment может не иметь mypy и поэтому package verifier не должен подменять CI execution фиктивным PASS.
- GitLab остаётся secondary/optional provider: shared quota должна быть доказана и fail-closed; Board parity остаётся P1.

## 4. Сознательно остаётся `NOT_VERIFIED`

- live GitHub ruleset/readback конкретного repository;
- deployed production revision и observation window;
- 20 полных multi-stack cycles;
- 30 дней observation;
- usability study минимум 10 нетехнических владельцев;
- production-grade Safe Healing executor;
- progressive delivery/canary и metric-driven rollback;
- полный GitLab Board parity;
- multi-project executive/mobile/voice слой.

Эти пункты являются эксплуатационными release gates и не могут «позеленеть» от количества файлов или unit tests.

## 5. Ожидаемая диагностика чистого пакета

После final package verification ожидается:

- Package Integrity: `VERIFIED`;
- Configuration: `VERIFIED` для согласованного template/default;
- Control Plane: `NOT_VERIFIED` до provider readback;
- Product Health: `NOT_VERIFIED` до runtime/deployment evidence.

Такое состояние считается корректным и специально предотвращает false-green.

## 6. Статус релиза

**ADWF v1.4.0 — release candidate для public GitHub reference cycle.** Это существенно более близкий к автономной Enterprise-системе пакет, но не заявленные `9.5/10`: аудит определяет 9.5 как эксплуатационно доказанную характеристику после множества живых циклов и периода наблюдения.

## 7. Локальная verification этого release candidate

- `python -m unittest discover -s .adwf/tests -q`: **212/212 PASS**.
- `adwf self-test`: **212/212 PASS**.
- `validate_pipeline_ir.py`: **PASS**.
- `validate_ci.py`: **PASS**.
- `validate_docs.py`: **PASS**.
- `docs_freshness.py`: **PASS**.
- `validate_framework.py`: **PASS**.
- `doctor`: Package Integrity **VERIFIED**, Configuration **VERIFIED**, Control Plane **NOT_VERIFIED**, Product Health **NOT_VERIFIED**.
- `mypy` dependency set pinned/hash-locked and CI gate configured; сам mypy не был исполнен в локальной изолированной среде сборки, где отсутствует доступ к PyPI. Поэтому type-check result здесь **NOT_VERIFIED**, а не фиктивный PASS.
