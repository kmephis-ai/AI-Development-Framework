# AI Development Framework v1.6 — Executive Autopilot Remediation

**ADWF v1.6** — public-first framework для AI-разработки, где владелец формулирует результат обычным языком, а техническая система организует проверяемый цикл разработки. Главный принцип:

> **ИИ создаёт. GitHub доказывает и ограничивает. ADWF организует, помнит и исполняет безопасные шаги. Владелец принимает только продуктовые и необратимые решения.**

Канонический обязательный профиль — `FREE_PUBLIC_GITHUB`: public GitHub repository, standard GitHub-hosted runners, mandatory AI/API calls = `0`, автоматический денежный бюджет обязательного контура = `$0`, larger runners = `BLOCK`, self-hosted runner не нужен.

<!-- ADWF:STATUS:START -->
## Что доказано

- Framework package: `1.6.0`.
- Package Integrity: `VERIFIED` только при совпадении manifest/checksums.
- Configuration: `VERIFIED` только при валидных policy/schema/generated projections.
- Canonical GitHub Control Plane: live-certified через GOV-004 на `main@e4bc0a8eef368cfcee6bd2abc3e4d6c8d5bae5cb`; перед релизом или эксплуатационным решением текущий статус всегда перечитывается из GitHub, а не выводится из этой строки.
- Product Health: отдельное product/deployment evidence. Сертификация самого framework repository не превращает подключённый продукт в `HEALTHY` автоматически.

Локальный ZIP сам по себе не создаёт live provider evidence. Если live readback недоступен или устарел, `NOT_VERIFIED` остаётся правильным fail-closed состоянием.
<!-- ADWF:STATUS:END -->

## Что исправлено относительно v1.5

1. **Trust-boundary gate.** Trusted controller сам читает PR diff через GitHub API. Изменение workflows/evaluators/policy не может получить `adwf/trusted-gate` только потому, что PR запустил ослабленную собственную проверку. Governance change требует отдельной exact-HEAD human R4 authorization. В multi-admin repository это может быть независимый approved review repository admin; в solo-maintainer режиме используется SHA-bound `Owner-Attestation` с provider-verified admin identity. Trusted gate публикует одно решение одновременно как audit-friendly Check Run и ruleset-consumable Commit Status; failure sentinel сохраняет fail-closed состояние при частичном provider failure. Adversarial AI review дополняет доказательства, но не заменяет owner attestation.
2. **Единый Runtime Supervisor.** Для каждой durable phase существует один executor в `ActionExecutorRegistry` либо явный `HUMAN_REQUIRED`. Legacy `orchestrate_event.py` исключён из production control workflow.
3. **Owner Intent действительно будит controller.** `start` сначала проверяет active run, затем создаёт Brief/Run/Work Memory; connected GitHub path создаёт Issue, safe checkpoint и trusted workflow dispatch.
4. **«ПРОДОЛЖИТЬ» действительно продолжает.** Provider-authenticated owner decision записывает exact-SHA result для `OWNER_ACCEPTANCE`, запускает Supervisor и будит trusted controller.
5. **Единый SSOT.** Durable Orchestrator — authoritative workflow state. Work Memory — private handoff context. Dashboard/Roadmap/Portfolio/Issue labels — projections.
6. **Exact-revision Preview.** Loopback preview разрешён только если локальный Git HEAD равен заявленному SHA. PR runner публикует безопасный preview marker, но trusted controller принимает его только после exact-HEAD `fast-feedback + governance-gate + trusted-gate` и читает marker обратно из GitHub job logs; содержимое untrusted log само по себе evidence не является. Remote preview требует отдельный provider deployment readback exact SHA.
7. **Transactional Auto Release.** `adwf release --auto` только планирует/готовит version-bump transaction. Архив нельзя выпустить под версией, отличной от `VERSION` и canonical version fields.
8. **Project Packs материализуются.** Python/FastAPI/Node/React/Vue/Angular/Go pack создаёт deterministic config projection; GitHub bootstrap оформляет trust-changing materialization отдельным governance PR.
9. **Public-safe multi-day ledger.** В public Issue не сохраняется raw owner task/Work Memory/произвольный stderr. Checkpoint имеет safe projection, hash-chain, provider object binding и отдельный protected annotated-tag anchor.
10. **Ruleset readback усилен.** Проверяются `bypass_actors=[]`, PR requirement, force-push/delete protection, exact required contexts и единый GitHub Actions app integration id.
11. **Pipeline IR — generator SSOT для GitHub workflows.** Ручной drift generated workflows блокируется `generate_pipeline.py --check`.
12. **Performance Plane живой.** Trusted collector собирает execution/queue/TTFF/flake/superseded cancellation, sample window, per-impact и per-pack projections. Недостаточная выборка остаётся `NOT_VERIFIED`.
13. **Delivery adapter contract.** `REFERENCE_LOCAL` доказывает promotion→artifact digest→observation без ложного заявления production hosting. `COMMAND` больше не доверяет `exit 0`: проектный adapter обязан вернуть exact-SHA structured attestation, artifact digest, provider readback и evidence refs.
14. **Windows/Linux functional smoke.** Канонический GitHub repository прошёл hosted functional smoke на Ubuntu и Windows для сертифицированного `main`. Любая другая установка ADWF должна получить собственное live evidence и не наследует этот статус автоматически.
15. **Capability Traceability.** `.adwf/capability-traceability.json` связывает каждое крупное заявление с CLI/UI entrypoint, production path, verification и честной live boundary. `validate_capabilities.py` блокирует релиз при исчезновении заявленного production wiring.

## Канонический путь владельца

<!-- adwf-doc: skip(reason=conceptual-flow) -->
```text
«Сделай страницу регистрации»
        ↓
Product Brief + private Work Memory
        ↓
Durable Orchestrator + Runtime Supervisor
        ↓
GitHub Issue → Creative Agent → exact branch/commit → PR
        ↓
impact-aware CI → governance-gate → trusted-gate
        ↓
exact-SHA Playwright preview
        ↓
Executive Portal → [ ПРОДОЛЖИТЬ ]
        ↓
exact-head merge → release transaction → promote → observe
```

Creative Agent остаётся сменным adapter. Без настроенного агента система **не притворяется автономной**: фаза `EXECUTE/RECOVERY` возвращает `WAITING_AGENT`. Платный AI не является correctness gate.

## Быстрый старт

Windows: `START_ADWF.bat`. Linux/macOS: `START_ADWF.sh`.

Инженерная проверка пакета:

<!-- adwf-doc: run -->
```bash
python .adwf/scripts/validate_ci.py
python .adwf/scripts/validate_pipeline_ir.py
python .adwf/adwf.py doctor --scope package_integrity
```

Пошагово: [docs/QUICKSTART_V1_6.md](docs/QUICKSTART_V1_6.md).

## Граница заявлений

Локальный ZIP не может доказать текущее состояние GitHub rulesets, hosted runners, provider identity, deployment или длительную эксплуатацию. Для канонического repository live control-plane certification подтверждена на `main@e4bc0a8eef368cfcee6bd2abc3e4d6c8d5bae5cb`; это evidence привязано к repository/revision и перед последующим решением должно подтверждаться свежим provider readback. `Product Health` остаётся отдельным product/deployment состоянием и не выводится из сертификации framework control plane.

Checked-in `CONTROL_CENTER.md`/`CONTROL_CENTER.html` — package/bootstrap projection, сгенерированная из доступного ей state/evidence на момент материализации. Она не должна использоваться как замена свежему GitHub Runtime Ledger/Actions/ruleset readback для оценки live control plane.

## Документация

- [INSTALL.md](INSTALL.md) — установка/bootstrap.
- [SPECIFICATION.md](SPECIFICATION.md) — нормативная архитектура v1.6.
- [ADWS.md](ADWS.md) — рабочий стандарт.
- [SECURITY.md](SECURITY.md) — trust/privacy/secrets.
- [docs/QUICKSTART_V1_6.md](docs/QUICKSTART_V1_6.md) — владелец без Git/CLI.
- [docs/RELEASE_READINESS_V1_6.md](docs/RELEASE_READINESS_V1_6.md) — граница технической готовности и owner LICENSE decision.
- [docs/architecture/EXECUTIVE_AUTOPILOT_V1_6.md](docs/architecture/EXECUTIVE_AUTOPILOT_V1_6.md) — connected model.
- [docs/migration/V1_5_TO_V1_6.md](docs/migration/V1_5_TO_V1_6.md) — транзакционная миграция.
- [docs/V1_6_IMPLEMENTATION_REPORT.md](docs/V1_6_IMPLEMENTATION_REPORT.md) — finding→fix traceability.
