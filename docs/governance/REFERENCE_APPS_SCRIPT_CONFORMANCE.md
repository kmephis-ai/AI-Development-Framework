# Reference Apps Script / Data-Centric Consumer Conformance

`ASREF-001` — второй bounded unit этапа **v1.9.0 Heterogeneous Conformance & Human-by-Exception**. Он проверяет ADWF lifecycle на отдельном Apps Script/data-centric consumer, но намеренно не выдаёт локальный test shim за реальный Google Apps Script runtime.

## Project Pack

Canonical pack: `.adwf/packs/apps-script.json`.

- detection marker: `appsscript.json`;
- precedence: `apps-script` стоит перед generic `node`, поэтому наличие `package.json` не меняет consumer class;
- обязательные команды: local `lint`, `unit`, `build` через consumer-owned npm scripts;
- `install` и `start` отсутствуют;
- preview отсутствует;
- `monetary_budget_usd = 0`, `secrets = FORBIDDEN`, `network = NONE`, `environment = PROCESS_MINIMAL`.

Для built-in `apps-script` definition эти ограничения являются fail-closed semantic contract: pack не может молча превратиться в registry/deploy/preview path.

## Reference consumer

Canonical fixture находится в `.adwf/reference-consumers/apps-script/` и содержит consumer-owned:

- `appsscript.json`;
- `Code.gs` с representative spreadsheet-style logic;
- deterministic `fixtures/operations.json`;
- `package.json` без dependencies/devDependencies;
- `scripts/check.mjs`.

`package.json` присутствует специально для проверки pack precedence. Mandatory scripts используют только установленный Node.js runtime и не требуют `npm install`, `clasp`, OAuth, Apps Script API, Drive API, Google Cloud project или deployment.

Unit gate исполняет `Code.gs` через Node `vm` и предоставляет только узкую fake `SpreadsheetApp.getActiveSpreadsheet()` boundary с in-memory rows. Это adapter test бизнес-логики и интеграционной границы, **не Google API emulator и не Google runtime evidence**.

## Conformance chain

`reference_apps_script_conformance.py` использует те же authoritative primitives, что WEBREF:

1. exact clean framework SHA/tree + manifest digest;
2. independent consumer Git seed HEAD/tree;
3. transactional adoption + durable snapshot;
4. deterministic detection `apps-script > node`;
5. sealed consumer profile + exact pack digest;
6. operational consumer HEAD/tree;
7. lint/unit/build в disposable exact-revision clone через `ProjectExecutionSession`;
8. sealed execution evidence с `declared_network=NONE` и без inherited secret-like environment;
9. functional binding report → exact gate execution ID + digests `Code.gs`, manifest и fixture;
10. canonical source mutation check;
11. guarded detach с сохранением consumer-owned Apps Script/data/profile files;
12. deterministic distinct-plan re-adoption и idempotent profile materialization.

Общий `REFERENCE_CONFORMANCE_REPORT` расширен вторым consumer class; отдельный evidence framework не создаётся. Для data-centric class browser preview сохраняет общий обязательный report shape, но все browser evidence fields имеют `null`/empty sentinel values, а status/capture mode честно фиксируются как `NOT_APPLICABLE` с reason `DATA_CENTRIC_NO_BROWSER_PREVIEW`. Web class при этом продолжает требовать PASS, exact head/pack binding и непустые screenshot digests.

## Fail-closed expectations

ASREF блокирует или сохраняет безопасно:

- fallback на generic `node` при наличии Apps Script marker;
- stale/tampered consumer profile и substituted pack digest;
- dirty source/head drift;
- tracked mutation из pack command;
- symlink/type collision Managed Surface;
- forged report/cross-evidence binding;
- external package dependency в reference fixture;
- расширение built-in Apps Script pack до package-install, network или preview runtime path.

## Ограничения PASS

Report обязан явно содержать:

- `SHARED_GUARDED_MERGE_NOT_IMPLEMENTED`;
- `NETWORK_DECLARATION_ONLY_NOT_ENFORCED`;
- `REFERENCE_NOT_LIVE_PROVIDER_EVIDENCE`;
- `READOPTION_REQUIRES_DISTINCT_PLAN_IDENTITY`;
- `GOOGLE_APPS_SCRIPT_RUNTIME_NOT_EXECUTED`;
- `GOOGLE_PROVIDER_NOT_VERIFIED`;
- `NO_MANDATORY_EXTERNAL_DEPLOYMENT`.

`network=NONE` — declaration, а не portable OS/domain sandbox. ASREF не утверждает, что произвольный consumer физически не может открыть сеть; он доказывает, что canonical reference path не требует сети/credentials/deployment и выполняется в существующем ProjectExecution safety envelope.

## Truth boundary

`REFERENCE_APPS_SCRIPT_CONFORMANCE` остаётся `LIVE_NOT_VERIFIED`.

Для `LIVE_VERIFIED` нужен отдельный реальный downstream Apps Script consumer и доказательство actual Google runtime/provider cycle, привязанное к exact consumer revision и pack digest. Synthetic fixture, local shim, CI и provider-side reference run такого доказательства не заменяют.

ASREF-001 также не доказывает edge/automation (Wiren Board) class. Он остаётся третьим независимым reference unit.
