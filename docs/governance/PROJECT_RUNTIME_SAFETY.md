# Project Command Runtime Environment/Data Safety Envelope v1

`ENVSAFE-001` превращает safety declaration Project Pack из одной только спецификации требований в portable execution baseline. Он не расширяет authority Project Pack и не выдаёт отсутствие OS-level sandbox за доказанную изоляцию.

## Core rule

> **DECLARATION IS NOT ENFORCEMENT.**

Project Pack по-прежнему только описывает требования. Исполнительная authority остаётся у ADWF runtime policy. Если selected pack, его exact digest или materialized safety projection не совпадают с текущей validated definition, project command не запускается.

## Exact pack/config binding

Consumer execution разрешён только когда validated **effective config** содержит materialized Project Pack. После `LIFECYCLE-005` effective config строится in-memory из immutable canonical `.adwf/config.json` + sealed consumer-owned `.adwf-consumer/profile.json`; profile не получает authority над governance/provider/trust sections. Одновременно доказаны:

- selected pack совпадает с deterministic current detection;
- `selected_digest` совпадает с SHA-256 текущей strict pack definition;
- materialized safety projection byte-semantically совпадает с validated definition;
- `monetary_budget_usd = 0`;
- `secrets = FORBIDDEN`;
- `environment = PROCESS_MINIMAL`;
- data access остаётся `PROJECT_TREE` read + `TOOL_OUTPUTS_ONLY` write.

Stale framework-config binding, profile tamper, stale pack digest, pack substitution или safety mismatch блокируют execution до запуска subprocess.

Framework self-host без consumer profile продолжает использовать canonical config; adopted consumer после explicit profile bootstrap больше не считается framework self-host.

## Minimal child environment

Consumer Project Pack command не получает `os.environ` целиком. Runtime строит новый child environment из небольшого portability allowlist и собственных temporary paths.

В частности:

- token/key/secret/password/credential variables не наследуются напрямую;
- `HOME` / `USERPROFILE`, temp и package-manager caches направляются в disposable execution root;
- interactive Git credential prompting отключается;
- stdout/stderr consumer gates не сохраняются в canonical runtime evidence.

Это доказывает **direct environment non-inheritance**, но не является kernel-level secret sandbox: процесс с иными host capabilities не объявляется изолированным только из-за очищенного env.

## Independent exact-revision execution clone

Consumer command не запускается в canonical checkout. Перед запуском ADWF требует чистый Git source и фиксирует exact `HEAD` + tree. Затем создаётся независимый local Git clone через local transport:

- `--no-local` / `--no-hardlinks`;
- detached exact source SHA;
- отдельный `.git` object/config store;
- `origin` удаляется перед consumer execution;
- shared object alternates запрещены.

После каждого command runtime проверяет:

- disposable clone всё ещё на исходном HEAD/tree;
- tracked mutation внутри clone;
- canonical checkout всё ещё имеет тот же HEAD/tree и остаётся clean.

Tracked mutation в disposable clone переводит command в safety `BLOCK`; canonical source при этом не изменяется. Untracked build/install outputs могут существовать только внутри disposable clone и уничтожаются вместе с ним — они не становятся Managed Surface ownership и не переносятся обратно автоматически.

## Project gates

Для consumer repository `run_project_gates.py` требует materialized exact Project Pack binding и выполняет configured gates через safety envelope. Если configured command совпадает с current pack command, дополнительно проверяется exact command binding.

ADWF framework repository имеет узкое self-host исключение: `project.type=framework` + `runtime_product=false` не является consumer Project Pack runtime, поэтому его внутренние framework gates продолжают canonical self-test path. Это исключение не распространяется на consumer products.

## Preview

`run_preview.py` выполняет Project Pack `install` и `start` в том же independent exact-revision clone. `capture_preview()` получает source root этого clone, поэтому local source attestation доказывает тот же exact HEAD/tree. Preview manifest и trusted bridge file сохраняются только в canonical ignored `.adwf-runtime` через отдельный `runtime_root`.

Framework-owned Playwright helper commands также получают minimal environment, но остаются framework tools, а не authority из Project Pack.

## Network truth boundary

Project Pack network field остаётся strict requirement:

- `NONE`;
- `LOOPBACK`;
- `PACKAGE_REGISTRY`;
- `PACKAGE_REGISTRY_AND_LOOPBACK`.

Portable v1 runtime **не имеет доказанного packet/domain confinement primitive** одновременно для поддерживаемых hosted/local platforms. Поэтому runtime evidence всегда разделяет:

- `declared_network` — что требует pack;
- `network_enforcement = DECLARATION_ONLY_NOT_ENFORCED` — что реально доказано execution layer.

Наличие network declaration никогда не преобразуется в `ENFORCED`/sandbox PASS. Package command может завершиться функционально, но это не является доказательством сетевой изоляции. Full network sandbox/hardening остаётся отдельной capability и требует реального provider/OS primitive + evidence.

## Runtime evidence

Каждая bounded session сохраняет ignored machine-readable record:

`.adwf-runtime/project-execution/PEX-<id>.json`

Record содержит только безопасную metadata:

- exact source HEAD/tree;
- exact pack ID/digest;
- названия переданных env variables без значений;
- declared network + truthful enforcement status;
- data isolation mode;
- per-command argv digest, return code и mutation counters;
- canonical source integrity;
- reason codes;
- self-sealed SHA-256.

Raw command output и environment values в evidence не входят. Self-seal обнаруживает локальную подмену record, но не превращает его в provider-attested evidence сам по себе.

## Fail-closed examples

Execution блокируется при:

- missing/unmaterialized Project Pack для consumer runtime;
- stale/substituted pack digest;
- safety projection mismatch;
- dirty/ambiguous canonical source;
- command substitution относительно pack binding;
- detached clone HEAD/tree drift;
- tracked mutation;
- canonical source mutation;
- malformed/tampered runtime evidence.

## Truth boundary

`ENVSAFE-001` доказывает implementation portable environment/data baseline. Он **не** доказывает:

- OS/container/kernel sandbox;
- package-registry-only domain egress;
- защиту от произвольного malicious process, имеющего возможность исследовать весь host filesystem/process space;
- heterogeneous consumer conformance;
- production secret isolation;
- real consumer runtime outcome.

Поэтому `PROJECT_RUNTIME_SAFETY` и `PROJECT_PACKS` не становятся `LIVE_VERIFIED` только из-за unit/self-tests, CI или merge. Нужен отдельный downstream consumer/provider evidence cycle.
