# Skill Operating Layer ADWF

## Назначение

Skill Operating Layer превращает повторяемые AI-процедуры ADWF из набора Markdown-инструкций в управляемые объекты Engineering OS. Наличие `SKILL.md` само по себе не означает, что навык проверен или разрешён к использованию: состояние, происхождение, заявленные эффекты, безопасность, routing и eval evidence должны подтверждаться машинно.

Детерминированные инварианты ADWF — exact SHA, required checks, FREE_ONLY, secret protection, package integrity и SHA-bound Owner-Attestation — остаются в code/policy/CI. Skill не может заменить их текстовой просьбой.

## Контракт управляемого Skill

Управляемый пакет находится в `skills/<skill-id>/` и содержит как минимум:

- `SKILL.md` — процедура для AI;
- `SPEC.md` — границы и контракт;
- `skill.json` — machine-readable descriptor;
- четыре eval fixture: `trigger-positive`, `trigger-negative`, `success-cases`, `adversarial`.

Опциональные `scripts/`, `references/` и другие ресурсы допустимы, но должны соответствовать заявленным side effects и security policy. Внешние executable dependencies обязаны быть явно перечислены и pinned.

## Lifecycle

First-party Skill проходит последовательность:

`DRAFT -> VALIDATED -> SECURITY_SCANNED -> EVAL_PASSED -> APPROVED -> ACTIVE -> DEPRECATED`.

Vendor Skill проходит более строгую последовательность:

`UNTRUSTED -> QUARANTINED -> SCANNED -> VENDORED -> EVAL_PASSED -> APPROVED -> ACTIVE -> DEPRECATED`.

Пропуск промежуточных состояний не считается допустимым lifecycle transition. Внешний Skill не скачивается и не исполняется автоматически.

## Progressive disclosure

Целевой startup surface ограничен тремя router Skills:

- `adwf-develop`;
- `adwf-govern`;
- `adwf-operate`.

Leaf Skills не должны становиться startup-visible. Registry проверяет ссылки router/leaf и ограничение не более трёх startup entries. Это уменьшает расход контекста и риск случайного выбора неподходящей процедуры.

## Security gate

`.adwf/scripts/validate_skills.py` fail-closed проверяет:

- schema и package identity;
- lifecycle, router/leaf invariants и registry consistency;
- prompt-override/system-prompt-exfiltration patterns вне adversarial fixtures;
- credential-like values;
- скрытые download-and-execute patterns;
- undeclared shell/network/filesystem/secret effects;
- undeclared external domains;
- unpinned executable dependencies;
- vendor provenance, включая exact source digest, source ref, license и attribution;
- deterministic eval evidence и context budget.

Неясный или неполный ACTIVE package считается ошибкой, а не автоматически разрешённым.

## Eval gate

`.adwf/scripts/eval_skills.py` выполняет deterministic fixtures. Он не изображает LLM-eval и не объявляет качество модели доказанным. Проверяется контракт, который можно воспроизвести локально:

- positive trigger recall;
- negative/no-trigger precision;
- outcome assertions по содержимому Skill;
- adversarial routing cases;
- declared context budget.

No-trigger precision является обязательной метрикой: Skill должен не только срабатывать там, где нужен, но и не включаться на нерелевантных запросах.

## Generated registry

Когда появляются managed packages, `skills/registry.json` генерируется `.adwf/scripts/generate_skill_registry.py`. Registry является projection из package truth; ручной drift блокируется `--check` и общим `validate_framework.py`.

До завершения SKILL-001 migration единственный ранее доказанный `adwf-local-git-mirror` допускается только через `.adwf/skill-legacy-allowlist.json`. Это не общий bypass: разрешены конкретный path и exact package SHA-256, а изменение любого управляемого байта инвалидирует bridge.

## Vendor intake

`.adwf/scripts/vendor_skill.py` работает только с уже имеющейся локальной директорией и не выполняет сетевую загрузку. Он проверяет provenance и exact source digest, запрещает symlink intake и помещает пакет только в `.adwf-runtime/skill-quarantine/`. Intake никогда не переводит внешний Skill в `ACTIVE` автоматически.

## Канонические проверки

Для текущего репозитория используются:

<!-- adwf-doc: skip(reason=manual-skill-operator-commands-not-core-doc-smoke) -->
```bash
python .adwf/scripts/validate_skills.py
python .adwf/scripts/generate_skill_registry.py --check
python .adwf/scripts/eval_skills.py
python .adwf/scripts/validate_framework.py
python .adwf/adwf.py self-test
```

Локальный PASS ускоряет feedback, но не заменяет provider exact-head CI, governance gate или Owner-Attestation для R4 изменений.
