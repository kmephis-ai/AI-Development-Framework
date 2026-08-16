# Consumer Project Profile Overlay Contract v1

`LIFECYCLE-005` завершает consumer bootstrap без изменения immutable framework package identity. После adoption ADWF package остаётся тем же exact package, а consumer-specific identity хранится отдельно.

Ключевой invariant:

> **CONSUMER CONFIGURATION IS NOT FRAMEWORK PACKAGE IDENTITY.**

И одновременно сохраняется общий lifecycle invariant:

> **PROJECT MUST OUTLIVE FRAMEWORK.**

## Почему нельзя писать consumer identity в `.adwf/config.json`

`.adwf/config.json` входит в framework package/Managed Surface provenance. Если после adoption переписать его именем consumer project или Project Pack projection, файл перестаёт совпадать с installed digest. Это ломает trusted adoption snapshot и превращает последующий detach/recovery в неоднозначную операцию.

Поэтому canonical `.adwf/config.json` остаётся package template truth. Consumer-specific состояние хранится в:

`.adwf-consumer/profile.json`

Этот путь **не входит** в `MANIFEST.json` и по Managed Surface semantics является `CONSUMER_OWNED`.

## Разрешённая поверхность overlay

Profile может задавать только bounded consumer-specific projection:

- `project.name`;
- `project.default_branch`;
- `project.type` из exact detected Project Pack;
- `project.runtime_product=true`;
- `project.repository_visibility`;
- project gate command projection;
- selected Project Pack ID и exact digest;
- runtime commands;
- preview declaration;
- safety declaration.

Profile не может задавать или переопределять:

- policy/fail mode;
- provider settings;
- cost/monetary authority;
- trust/risk/owner authority;
- autonomy;
- workflows/rulesets/permissions;
- release settings;
- Managed Surface ownership.

Schema использует `additionalProperties=false`, а effective-config loader копирует только explicit allowlisted sections. Unknown/forbidden field блокирует profile вместо расширения authority.

## Cryptographic binding

Profile self-sealed SHA-256 и привязан к:

- SHA-256 текущего canonical `.adwf/config.json`;
- exact current Project Pack digest;
- deterministic command/runtime/preview/safety projection текущего validated pack.

При каждом load binding пересчитывается. Stale framework config, pack substitution, projection drift или tamper возвращают deterministic BLOCK/HUMAN_REQUIRED вместо молчаливого принятия старой consumer configuration.

Self-seal обнаруживает локальную подмену, но сам по себе не является provider attestation.

## Effective config

`load_effective_config()` сначала валидирует canonical `.adwf/config.json`, затем optional consumer profile и строит in-memory effective config. На диск merged config не записывается.

Разрешено заменить только:

- `project`;
- bounded project gate `commands`;
- bounded `project_packs` projection.

После merge весь effective config снова валидируется существующей `config.schema.json`. Все governance/provider/cost/trust sections берутся только из canonical framework config.

Framework self-host без profile продолжает получать canonical config без изменения поведения.

## Plan/apply semantics

Profile materialization explicit и plan-first:

- отсутствующий profile → `READY_TO_APPLY`;
- existing exact valid profile → `ALREADY_MATERIALIZED`;
- malformed/foreign/different profile → `HUMAN_REQUIRED`;
- symlink/non-file/concurrent collision → BLOCK/HUMAN_REQUIRED.

Apply создаёт только новый `.adwf-consumer/profile.json` через no-overwrite create и выполняет readback. Existing foreign bytes никогда не перезаписываются.

`materialize_project_pack()` больше не переписывает `.adwf/config.json`, `.adwf/effective-policy.json`, `MANIFEST.json` или `SHA256SUMS.txt` ради consumer identity.

## Runtime integration

Consumer-facing paths читают effective config там, где нужна consumer identity/Project Pack state:

- Project Pack materialization/binding;
- project gates;
- preview;
- ENVSAFE exact pack binding;
- GitHub bootstrap self-host/product decision;
- configuration/product health.

Так adopted consumer больше не попадает под framework self-host exemption после explicit profile bootstrap, а ENVSAFE runtime становится достижим по canonical lifecycle path.

## Managed Surface / detach

Consumer profile не входит в package inventory и поэтому не получает `FRAMEWORK_PRIVATE` ownership даже если ADWF создал его во время bootstrap. Guarded detach plan не содержит `.adwf-consumer/profile.json`, а detach executor не имеет authority удалять этот path.

Adversarial lifecycle test проверяет exact chain:

`adoption → transaction-bound snapshot → consumer profile → original detach plan → guarded detach`

и требует сохранения profile и consumer files после detach.

## GitHub bootstrap

Owner-facing `product` становится consumer project name. Provider readback поставляет default branch и repository visibility. Project type выводится только из validated selected Project Pack.

Bootstrap PR создаёт bounded consumer profile, а не регенерирует framework package integrity ради consumer identity.

## Truth boundary

`LIFECYCLE-005` доказывает implementation consumer profile/effective-config contract и synthetic lifecycle provenance invariants. Он не является real consumer/provider lifecycle evidence.

`CONSUMER_PROFILE_OVERLAY`, `MANAGED_SURFACE_CONTRACT`, `PROJECT_PACKS` и `PROJECT_RUNTIME_SAFETY` не повышаются до `LIVE_VERIFIED` только из-за unit/self-tests, CI или merge. Для этого нужен отдельный downstream real-consumer evidence cycle.
