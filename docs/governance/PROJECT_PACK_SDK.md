# Project Pack SDK Contract v1

`PACKSDK-001` formalizes built-in Project Packs as strict, versioned, declarative contracts. A file under `.adwf/packs/*.json` is **not trusted because it is JSON**: it must validate before detection, command selection or materialization.

## Core rule

> **A Project Pack declares requirements; it does not grant itself authority.**

The pack contract cannot expand Managed Surface ownership, GitHub permissions, secret access, provider permissions, monetary budget or any other ADWF trust boundary.

## Definition identity

Every built-in pack must declare:

- `$schema = .adwf/schemas/project-pack.schema.json`;
- `schema_version = 1`;
- `role = PROJECT_PACK_DEFINITION`;
- canonical lowercase `id` matching its filename;
- one deterministic detection strategy;
- only registered command capability names;
- strict preview metadata;
- mandatory safety declaration.

The canonical SHA-256 digest is computed over deterministic JSON serialization of the validated definition. Detection and materialization expose that digest so later conformance evidence can bind to an exact pack definition rather than only a pack name.

## Detection

Detection is data-driven from the validated definition. v1 permits exactly one primary strategy:

- dependency presence in `package.json`;
- marker file presence;
- token presence in explicitly declared scan files.

`PACK_ORDER` remains the deterministic precedence rule when multiple safe definitions match. Unknown pack IDs cannot become executable merely by adding a JSON file; registration order is explicit in code.

`ASREF-001` adds canonical `apps-script` before generic `node`: an `appsscript.json` marker wins even when the data-centric project also has `package.json`. The built-in Apps Script definition is stricter than the generic schema: `network=NONE`, no `install`, no `start` and no preview runtime. Its mandatory reference path therefore does not require `clasp`, Google credentials or deployment.

## Command contract

Allowed capability names are bounded to:

`lint`, `unit`, `integration`, `build`, `smoke`, `golden_paths`, `e2e`, `install`, `start`.

Commands are argv arrays and downstream runners execute them without `shell=True`. v1 additionally rejects shell-control-shaped argv (`;`, pipes, redirections, command substitution, newlines) so a future adapter cannot accidentally reinterpret a pack as shell source.

`requires_file` is a canonical repository-relative path. Absolute paths, traversal, backslashes and non-canonical forms are blocked before materialization.

## Safety declaration

Every pack explicitly declares:

- `monetary_budget_usd = 0`;
- `secrets = FORBIDDEN`;
- bounded network requirement (`NONE`, `LOOPBACK`, `PACKAGE_REGISTRY`, or `PACKAGE_REGISTRY_AND_LOOPBACK`);
- `environment = PROCESS_MINIMAL`;
- data requirement `PROJECT_TREE` read + `TOOL_OUTPUTS_ONLY` write.

These fields are **requirements, not permissions**. They never override provider policy, FREE_ONLY, Managed Surface ownership or runtime execution policy. A pack asking for a broader or unknown value is invalid rather than implicitly authorized.

Package-install commands require a package-registry network declaration. Preview URLs must use loopback HTTP (`127.0.0.1` or `localhost`); an external preview endpoint is rejected.

## Materialization

`materialize_project_pack()` принимает только output strict loader, но после `LIFECYCLE-005` **не переписывает framework-private `.adwf/config.json` ради consumer identity**. Selected pack ID, exact digest, gate/runtime commands, preview и safety projection записываются в bounded consumer-owned `.adwf-consumer/profile.json`.

Profile cryptographically bound к exact canonical config digest и current pack digest, self-sealed и валидируется перед каждым effective load. Existing foreign/malformed profile не перезаписывается автоматически. Canonical framework config, effective-policy и package integrity SSOT остаются byte-stable относительно adoption provenance.

Подробный contract: [Consumer Project Profile Overlay](CONSUMER_PROFILE_CONTRACT.md).

## Fail-closed examples

The SDK blocks before execution/materialization when a definition contains:

- an unknown command capability;
- an unknown field/provider hint;
- a paid monetary budget or secret requirement;
- an undeclared/mismatched network requirement;
- traversal/absolute `requires_file`;
- shell-control-shaped argv;
- an external preview URL;
- malformed detection or unknown pack registration;
- any schema/type violation.

## Runtime enforcement follow-up

`ENVSAFE-001` добавляет отдельный [Project Command Runtime Environment/Data Safety Envelope](PROJECT_RUNTIME_SAFETY.md). SDK definition остаётся declaration layer: runtime повторно связывает selected pack/digest/safety с current validated definition, очищает direct child environment и выполняет consumer commands только в disposable exact-revision clone. Network declaration при этом **не** выдаётся за packet/domain enforcement.

## Truth boundary

Formal SDK tests prove implementation and exact definition identity. WEBREF/ASREF add synthetic reference conformance for Web and Apps Script, but they still do **not** constitute real downstream provider/runtime evidence. `PROJECT_PACKS` remains `LIVE_NOT_VERIFIED`; edge/Wiren Board reference conformance and real consumer/provider cycles remain separate proof boundaries.
