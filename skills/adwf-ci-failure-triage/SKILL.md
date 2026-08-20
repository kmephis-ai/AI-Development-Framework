---
name: adwf-ci-failure-triage
description: Diagnose CI failures from fresh exact-SHA provider evidence, classify the failure boundary and choose the smallest safe repair without weakening gates or mistaking infrastructure faults for product defects.
---

# ADWF CI Failure Triage

Используй этот Skill, когда CI/workflow/job/check упал, завис, был cancelled/timeout или ведёт себя нестабильно и нужно установить причину до повторного запуска или изменения кода.

## Exact evidence first

1. Свежо прочитай repository, exact subject SHA, workflow run, job и step evidence у provider. Не диагностируй по старому run, названию Issue или тексту агента.
2. Найди первый meaningful failure boundary. `skipped` downstream steps обычно являются следствием предыдущего failure и не считаются отдельными причинами.
3. Зафиксируй runner/image/toolchain и внешний dependency boundary, если failure произошёл в setup/bootstrap/install/download/readback.
4. Если есть несколько попыток, сравни только доказанно сопоставимые runs. Same-SHA повтор особенно ценен: он отделяет изменение кода от runtime/provider variability.

## Canonical classification

Выбери ровно одну primary classification; если доказательств недостаточно — `UNKNOWN`.

- `INTRODUCED` — evidence связывает failure с candidate change или новым exact HEAD, а релевантный baseline был green.
- `INHERITED` — failure уже существовал в baseline/parent или лежит вне изменённого scope и это подтверждено provider evidence.
- `FLAKY` — неизменённый exact SHA воспроизводит непостоянный результат без устойчивой deterministic cause; retry может быть диагностикой, но не превращает failure в PASS.
- `PROVIDER` — ломается provider control plane: API/check publication/Actions service/provider readback/queue semantics, а не код job environment.
- `ENVIRONMENT` — ломается execution substrate или внешний bootstrap dependency: hosted runner image, OS package manager, mirror/CDN, cache/toolchain/browser/runtime install и подобное.
- `POLICY` — deterministic governance/policy/ruleset/permission/cost/security gate корректно блокирует действие.
- `UNKNOWN` — evidence отсутствует, противоречиво или не позволяет безопасно выбрать один класс.

Timeout/cancelled — это симптом, а не root cause. По возможности укажи конкретный step/resource, который съел budget или перестал прогрессировать.

## Repair selection

После классификации выбери минимальный repair на правильном уровне:

- `GENERIC_FRAMEWORK` — общий дефект ADWF, воспроизводимый независимо от конкретного consumer;
- `PROJECT_PACK` — stack/class-specific дефект адаптера;
- `CONSUMER_INTEGRATION_CONFIG` — CI/config/integration конкретного consumer без изменения product logic;
- `CONSUMER_PRODUCT` — реальный defect product code/tests/runtime.

Не переносить consumer-specific workaround в generic core без доказательства общей природы дефекта.

Для dependency/bootstrap latency сначала исправляй доказанную причину: например, bounded approved fallback/repository endpoint, version/toolchain mismatch или некорректный setup. Увеличение timeout допустимо только как обоснованный budget change, а не как замена root-cause analysis. Не превращай конкретный endpoint в глобальный blacklist: сегодняшний degraded mirror/provider не доказывает постоянную непригодность сервиса.

Cache, prebuilt container или новый external provider не выбираются автоматически: сначала оцени supply-chain, trust, reproducibility, FREE_ONLY и maintenance impact.

## Retry rule

Не повторяй deterministic failure без cause classification. Retry допустим, когда он проверяет конкретную гипотезу о flake/provider/environment variability или когда repair уже изменил причинный фактор. Старый SUCCESS на том же SHA не маскирует более новый pending/failure.

## Safety boundary

Никогда не:

- отключай required checks или visual/security/privacy/contract gates ради зелёного CI;
- повышай retry count до фактического `retry-until-green`;
- считай timeout, cancellation или provider error PASS;
- расширяй permissions/ruleset bypass;
- добавляй paid/unknown mandatory provider при `FREE_ONLY`;
- объявляй product defect без evidence, если failure находится в provider/environment setup boundary.

`UNKNOWN` => `BLOCK/NOT_VERIFIED`, а не оптимистичный repair.

## Verification after repair

1. Запусти fresh exact-head validation после изменения причинного фактора.
2. Требуй успех не только repaired step, но и применимого downstream chain, который раньше был `skipped` или не успел выполниться.
3. Для same-SHA diagnostic retry не переиспользуй старый результат как authorization; canonical provider resolver должен выбрать актуальное evidence по policy.
4. Если repair затрагивает consumer, отдельно проверь отсутствие unrelated product/runtime/data drift.

## Owner-facing output

Кратко сообщи:

- где точная failure boundary;
- какие provider facts это доказывают;
- primary classification и repair layer;
- минимальный safe next action;
- blocker и нужен ли owner action.
