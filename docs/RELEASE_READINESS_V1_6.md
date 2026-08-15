# ADWF v1.6.0 — Release Readiness

Этот документ разделяет **техническую готовность**, **live evidence** и **юридическое решение владельца**. Он не является разрешением на публикацию.

## Сертифицированный baseline канонического repository

Последний явно зафиксированный live-certified baseline **до этой documentation-транзакции** — `main@e4bc0a8eef368cfcee6bd2abc3e4d6c8d5bae5cb`, полученный после GOV-004. Merge этого документа создаст новый `main`, поэтому его актуальное состояние всегда должно подтверждаться свежим provider readback, а не выводиться из указанного здесь исторического SHA.

Для baseline `e4bc0a8eef368cfcee6bd2abc3e4d6c8d5bae5cb` live GitHub evidence подтвердило:

- `ADWF protected main` active, без bypass, с обязательными `fast-feedback`, `adwf/governance-gate`, `adwf/trusted-gate` от GitHub Actions;
- immutable runtime-anchor tag ruleset active;
- solo-maintainer R4 authorization привязана к exact PR HEAD и проверяется trusted/default-branch evaluator;
- trusted gates публикуют одно решение как audit-friendly Check Run и ruleset-consumable Commit Status; failure sentinel сохраняет fail-closed состояние при частичном provider failure;
- GOV-004 объединён без bypass после exact-head canonical CI и Independent Review PASS;
- post-merge `ADWF Main` и hosted Windows/Linux `ADWF Platform Smoke` — SUCCESS на `e4bc0a8eef368cfcee6bd2abc3e4d6c8d5bae5cb`.

Это **revision-bound evidence**, а не бессрочная декларация. Перед release текущий `main`, rulesets, checks и provider state должны быть перечитаны заново.

## Что это не доказывает

Framework control-plane certification не означает автоматически:

- `Product Health = VERIFIED` для любого подключённого продукта;
- наличие production deployment;
- здоровье внешнего runtime;
- 30-дневную стабильность новой установки;
- право внешнего распространения кода.

Product/deployment evidence собирается отдельно для каждого продукта и exact deployed revision.

## Checked-in Control Center

`CONTROL_CENTER.md` и `CONTROL_CENTER.html` — package/bootstrap projections, генерируемые из доступного state/evidence. Checked-in snapshot может быть консервативным или устаревшим относительно live provider state и **не является SSOT для текущего GitHub control plane**.

Для live-решений authoritative readback — GitHub rulesets/checks/Actions плюс публично-безопасный Runtime Ledger/AssuranceSnapshot. При отсутствии свежего readback состояние остаётся `NOT_VERIFIED`.

## Release path

Каноническая release policy сохраняет разделение `MERGED != RELEASED != DEPLOYED != HEALTHY`.

`.adwf/scripts/release.py` до сборки external release проверяет наличие `LICENSE`. При отсутствии файла `LICENSE` external path останавливается fail-closed. GitHub publication дополнительно требует explicit external mode, authenticated GitHub context, exact version, pre-release validation и provider readback.

## Текущий юридический gate

Файл `LICENSE` отсутствует. `LICENSE_DECISION_REQUIRED.md` является намеренным owner gate.

ADWF и ИИ **не выбирают лицензию автоматически**. До решения владельца нельзя утверждать, что проект open source или разрешён к внешнему распространению, и нельзя создавать version tag/GitHub Release как внешний выпуск.

## Third-party attribution preflight

Известный install-time/tooling dependency surface включает pinned Playwright preview runtime и pinned Python typecheck tooling. REL-001B / Issue #12 отдельно проверяет лицензии, notices и необходимость attribution перед external release.

До завершения этой проверки юридическая/атрибуционная готовность не считается подтверждённой.

## Release-readiness checklist

### Техническая часть

- [x] Canonical repository live control-plane certification завершена через GOV-004.
- [x] Main ruleset активен с `bypass_actors=[]` на последнем подтверждённом provider readback.
- [x] Hosted Windows/Linux smoke подтверждён на зафиксированном certified baseline.
- [x] Exact-HEAD trusted/governance checks подтверждены без bypass для зафиксированного certified baseline.
- [x] External release path fail-closed при отсутствии `LICENSE`.
- [x] Documentation truth определён REL-001A/GOV-005; факт его присутствия в текущем `main` проверяется provider readback непосредственно перед release.
- [ ] Third-party attribution preflight завершён.
- [ ] Reproducible release dry-run выполнен на финальном exact HEAD без публикации.
- [ ] Финальный provider readback непосредственно перед release подтверждён.

### Решение владельца

- [ ] Выбрать юридическую модель распространения и конкретный `LICENSE` либо принять решение не выпускать external release.

Только после выполнения технических пунктов, свежего provider readback и отдельного решения владельца может быть подготовлен owner-confirmed external release transaction. Tag/GitHub Release до этого не создаются.
