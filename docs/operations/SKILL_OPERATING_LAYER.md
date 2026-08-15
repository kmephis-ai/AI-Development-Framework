# Skill Operating Layer ADWF v1

## Для чего это владельцу

Skill Operating Layer превращает повторяемые AI-процедуры в управляемые объекты ADWF. Новая AI-сессия не должна загружать десятки инструкций и заново изобретать процесс: на старте видны только три коротких маршрутизатора, а конкретный Skill подключается по необходимости.

Это уменьшает расход контекста, риск случайных действий и зависимость от памяти конкретного чата. При этом Skills **не заменяют** машинные правила безопасности: exact SHA, required checks, FREE_ONLY, integrity, rulesets и owner authorization остаются deterministic code/policy/CI.

## Три стартовых маршрутизатора

- `adwf-develop` — разработка, диагностика, восстановление workspace, continuity и проверка завершения.
- `adwf-govern` — аудит, evidence truth, traceability, security review и governance.
- `adwf-operate` — release, incident, upgrade, rollback, cost и runtime operations.

В `skills/registry.json` поле `startup_routers` должно содержать ровно эти три записи. Leaf Skills имеют `startup_visible=false` и подключаются только через `routed_by`.

## Первый rolling-wave batch

Текущий v1 batch содержит:

- `adwf-local-git-mirror` — exact-SHA локальный Git workspace через Connector, если direct Git недоступен;
- `adwf-session-bootstrap` — восстановление новой сессии из live provider/repository truth;
- `adwf-session-handoff` — durable handoff только из проверяемых фактов;
- `adwf-verification-before-completion` — запрет DONE/PASS без fresh evidence.

Следующие process Skills добавляются только после evidence этого batch, без заранее созданных десятков Issues.

## Что делает Skill managed

Управляемый first-party пакет содержит как минимум `SKILL.md`, `SPEC.md`, `skill.json` и четыре eval fixture: positive trigger, negative/no-trigger, success и adversarial. Registry генерируется детерминированно и содержит package digest, security/eval status и no-trigger precision.

`ACTIVE` не означает право обходить governance. Любая операция всё равно проходит применимые deterministic policies и provider gates.

## Внешние Skills

Внешний Skill нельзя скачивать как `latest` и сразу выполнять. Vendor intake допускается только через pinned provenance, quarantine, security scan, evals и явный lifecycle. Неизвестный или непроверенный источник остаётся заблокированным.

## Handoff и приватность

В handoff/eval fixtures нельзя хранить credentials, tokens, secret values, hidden chain-of-thought или лишние private user data. Передаются только durable facts: exact refs/SHA, принятые решения, результаты проверок, blockers и следующий безопасный action.

## Truth rule

Наличие `SKILL.md` само по себе не доказывает работоспособность. PASS требует свежих deterministic eval/security evidence, канонического registry/integrity состояния и provider exact-head verification для изменения, которое входит в `main`.
