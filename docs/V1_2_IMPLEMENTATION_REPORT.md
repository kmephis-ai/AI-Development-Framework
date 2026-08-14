# Отчёт о внедрении аудита в ADWF v1.2.1

## Результат

v1.2.1 реализована как совместимый hardening-патч линии v1.2. Критические P0-дефекты v1.1 закрыты исполняемыми механизмами; добавленные P1/P2 safeguards не требуют платного SaaS, AI/API или hosted runner.

| Finding v1.1 | Реализация v1.2 | Доказательство |
| --- | --- | --- |
| False `VERIFIED` от doctor | четыре независимых Health category | `test_core`, `doctor --scope` |
| Permission был декларацией | единый `policy.py` | 25-case A×R matrix + negative tests |
| Unknown Health разрешал progression | mutation gates require safe Health | `test_unknown_product_blocks_feature` |
| State machine не исполнялась | compare-and-set transition engine | `test_state_engine` |
| Evidence не имел provenance/TTL/hash | strict record + verifier | `test_evidence` |
| Lease не имел lifecycle | TTL/heartbeat/expiry/recovery/atomic update | `test_leases` |
| Несколько Writer | default maximum 1 + reconcile | `test_orchestration` |
| Любой `#number` связывался с Issue | строгий Roadmap-ID/Issue/lease contract | `test_issue_contract` |
| PR code имел Issues write | read-only PR + trusted workflow_run controller | static CI security test |
| Lease marker можно было подменить/просрочить | строгий UUID, Writer/workspace, fresh heartbeat и expiry | contract/controller/reconciliation tests |
| Merge-close event мог нести другой SHA | event связывается с PR/merge SHA, но CI/review остаются exact PR HEAD | trusted controller tests |
| Floating Actions/runtime | full SHA + Python 3.12.13 + Node 24 | `validate_ci.py`, runtime gate |
| FREE_ONLY был декларацией | provider registry, zero budget, quota TTL | `test_cost_guard` |
| Shell command injection | command arrays + `shell=False` | schema + project gate code |
| GitLab отсутствовал как adapter | local includes + Issues/MR/pipeline normalization | provider parity tests |
| Agent workspace был декларацией | isolated worktree, heartbeat, retry/backoff, clean cleanup | workspace lifecycle tests |
| Policy-файлы могли расходиться | deterministic Effective Policy + source hashes | policy compiler tests |
| Product gates могли оставаться пустыми | bootstrap fail-closed для PR/main/unit/runtime gates | project gate config tests |
| Dashboard был спецификацией | exact-SHA live snapshot, Project sync/readback evidence, pinned Issue readback | dashboard/reconciliation/project tests |
| CI trust domains совпадали | отдельные untrusted/main/trusted runner labels | static CI security tests |
| Quota guard не видел storage | minutes + artifacts + cache + `$0` stop proof | cost guard simulations |
| Migration не имела rollback proof | SHA backup manifest + pre-rollback backup | migration apply/rollback tests |

Локальный проверочный набор содержит **105 тестов**: contracts, policy/state/evidence, provider/cost, CI security, reconciliation, migration, workspace lifecycle, dashboard truth и adversarial canaries. Это доказательство package-level механизмов, а не подмена live end-to-end certification.

## Что получает владелец

- один экран с общим режимом, свежестью, текущей задачей/workspace, следующим действием и решениями владельца;
- отдельные Product/Control/Package/Configuration/Security статусы без ложного зелёного;
- CI p50/p95, очередь, время до первой ошибки и flake rate;
- private GitHub `2 000` minutes отдельно от бесплатных public standard runners;
- внутренние hard stops до лимита: 1 600 minutes, 350 MB artifacts, 2 GB cache;
- автоматическую блокировку unknown/paid/metered provider, auto credits и mandatory AI/API.
- Project projection не становится `PASS`, пока значения активной Issue не записаны и не прочитаны обратно; сбой не маскируется зелёным Control Plane.

## Остаточные ограничения

- Пакет создан из предоставленного ZIP; он не опубликован в конкретный repository, потому что repository identity/checkout не был доступен.
- Live branch/ruleset, три runner trust domains, Project views и controller connectivity можно подтвердить только после установки в целевой GitHub/GitLab project.
- Для exact-SHA independent review нужен второй GitHub collaborator/login. Private GitHub Free не предоставляет protected branches/rulesets; trusted controller сохраняет process gate, но риск ручного обходного merge остаётся видимым через Security и общий ограниченный режим панели.
- GitLab adapter строит provider-neutral snapshot, но автоматическая GitLab Board-проекция в v1.2.1 не реализована; локальный `CONTROL_CENTER.md` остаётся доступным, а полный Project v2 workflow реализован для GitHub.
- GitLab trusted reconciliation требует protected masked `ADWF_GITLAB_TOKEN` с `read_api`; токен не передаётся в MR jobs.
- Self-hosted runner требует один раз предоставить controller/runner access; hosted quota не включается автоматически.
- Product и Control Plane в шаблоне намеренно `NOT_VERIFIED`.
- Pricing/plan facts в registry имеют срок действия до `2026-09-13`; после него hosted/AI capabilities блокируются до повторной проверки официальных условий.
- External release намеренно заблокирован до решения владельца о LICENSE.

## Следующий milestone после установки

M1 certification: live repository reconciliation, real gate commands, первый R0/R1 Issue end-to-end, runtime Golden Paths, exact-SHA evidence, workspace stall/recovery drill, Project readback, rollback drill и truthful Control Center. Только затем допускается обсуждать A2; A3/A4 не включаются автоматически.
