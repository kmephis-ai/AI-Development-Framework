# Real External Consumer Upgrade Proof — UPGRADE-003

UPGRADE-003 проверяет уже реализованный lifecycle не на framework self-test и не на synthetic reference consumer, а на **exact read-only snapshot реального внешнего product repository**. Для первого canary используется PrihRashOnline-v2, но proof не устанавливает ADWF в его `main` и не создаёт там ветки, PR или deployment.

## Truth boundary

Цель proof — доказать generic lifecycle:

`exact consumer → adoption A → upgrade B → rollback A → retry B`

при byte-exact сохранности всех pre-existing tracked regular consumer/shared files. PASS означает только, что exact внешний snapshot выдержал isolated provider cycle. Он **не означает**, что сам внешний repository уже управляется ADWF или production-сертифицирован.

## Source isolation

External consumer root обязан быть clean Git toplevel с exact SHA40/tree SHA40. Harness читает только `git ls-files` и SHA-256 tracked regular files. Symlink/non-file tracked paths блокируются fail-closed.

Mutable lifecycle не запускается в source checkout. Harness создаёт private disposable root и копирует туда только tracked regular bytes. `.git`, ignored/untracked secrets, credentials и provider metadata в mutation root не копируются.

После всего цикла external source, framework A и framework B повторно проверяются как exact clean Git identities.

## Preservation proof

До adoption строится baseline set `path → SHA-256`; в report не сохраняется содержимое файлов. На границах:

1. `ADOPTION_A`;
2. `UPGRADE_B`;
3. `ROLLBACK_A`;
4. `RETRY_B`

каждый baseline path обязан остаться regular file с тем же digest. Report хранит aggregate preservation digest + file count для каждой границы. Любое исчезновение, symlink/type substitution или byte drift блокирует proof.

`PRESERVE_SHARED` и `preserved_sha256` остаются verification-only facts. Они не дают write/delete authority.

## Existing lifecycle reused

Harness не создаёт второй upgrade engine. Он композирует canonical primitives:

- Managed Surface `plan_adoption` / transactional `apply_adoption`;
- Consumer Profile + deterministic `apps-script` Project Pack;
- UPGRADE-001 compatibility + sealed READY plan;
- UPGRADE-002 transactional `apply_upgrade`;
- committed `rollback_upgrade`;
- deterministic retry после `ROLLED_BACK`.

Consumer Profile может быть byte-identical между A/B. UPGRADE_FIX-001 требует, чтобы такой profile был verification-only при committed rollback.

## Provider evidence

Real proof должен выполняться GitHub-hosted provider workflow на exact candidate/merge SHA. External PrihRash fetch является read-only; proof не требует Google Apps Script API, Drive API, OAuth или deployment.

Feature diff не добавляет permanent workflow/ruleset authority. Одноразовый support workflow используется только как provider evidence transport и не входит в candidate tree.

Report `EXTERNAL_CONSUMER_UPGRADE_PROOF` self-sealed и содержит только safe metadata:

- external repository name + exact SHA/tree;
- A/B exact SHA/tree;
- apps-script pack/profile digests;
- compatibility/plan/transaction identities;
- four preservation checkpoint digests;
- provider run/check identity;
- `write_back_performed=false`.

Local absolute paths, secrets и Google/user business data в report запрещены архитектурой payload.

## Fail-closed cases

Proof блокируется при substituted SHA/tree, dirty checkout, tracked symlink/non-file, A=B, source mutation during copy, adoption/upgrade not READY, preservation mismatch, profile/pack mismatch, failed rollback/retry или tampered report seal.

Canonical Capability Truth остаётся `LIVE_NOT_VERIFIED` до фактического exact provider proof. Само наличие harness/tests не является live evidence.
