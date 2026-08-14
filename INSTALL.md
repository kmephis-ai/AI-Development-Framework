# Установка ADWF v1.6 — для владельца без навыков программирования

## Основной режим

Канонический режим v1.6 — **публичный GitHub repository + `FREE_PUBLIC_GITHUB`**. Mandatory путь использует standard hosted runners, не требует платного AI/API и блокирует larger/unknown/metered provider.

Перед публикацией repository владелец отдельно принимает решение о публичности и LICENSE. ADWF не выбирает юридические условия автоматически.

## Windows

1. Распакуйте ADWF в папку проекта.
2. Запустите `START_ADWF.bat`.
3. Launcher выполняет Python preflight, запускает Portal только на `127.0.0.1`, ждёт реальный HTTP-ответ **ADWF v1.6 Executive Portal** и лишь затем открывает браузер.
4. В Portal укажите продукт и желаемый результат.
5. Подтвердите public/LICENSE только если решение действительно принято.
6. При подключённом GitHub ADWF запускает staged bootstrap.

Linux/macOS: `START_ADWF.sh`.

## Staged GitHub bootstrap

Bootstrap намеренно не создаёт protection rules «вслепую»:

1. создаёт безопасный seed PR, чтобы реальные contexts `fast-feedback`, `adwf/governance-gate`, `adwf/trusted-gate` появились от GitHub Actions;
2. читает их обратно и определяет единый GitHub Actions integration id;
3. создаёт/readback canonical main ruleset без bypass;
4. создаёт/readback tag ruleset для immutable runtime anchors;
5. определяет Project Pack;
6. если pack меняет trusted config — создаёт governance PR и ждёт exact-HEAD owner/admin approval;
7. только после materialization/readback сообщает готовность.

`WAITING_SEED_CHECKS` и `WAITING_OWNER_GOVERNANCE_APPROVAL` — нормальные промежуточные состояния, не `VERIFIED`.

## Проверка пакета инженером

<!-- adwf-doc: run -->
```bash
python .adwf/scripts/validate_ci.py
python .adwf/scripts/validate_pipeline_ir.py
python .adwf/adwf.py doctor --scope package_integrity
```

## Preview

Каноническая project команда — `python .adwf/scripts/run_preview.py --install-playwright`: она определяет Project Pack, запускает **текущий exact checkout** на loopback и только затем делает desktop/mobile capture. Низкоуровневый `adwf preview` также требует exact SHA; remote HTTPS preview допускается только с provider deployment attestation.

<!-- adwf-doc: skip(reason=requires-live-project-and-browser) -->
```bash
python .adwf/adwf.py preview --url http://127.0.0.1:4173 --head-sha <EXACT_SHA> --install
```

## Долгие задачи

Durable Orchestrator является SSOT. Private Work Memory содержит только handoff-факты, не raw chain-of-thought. Public GitHub ledger содержит safe operational projection и protected external anchor; arbitrary owner text туда не публикуется.

## AI

Если `ADWF_AGENT_COMMAND` не настроен и Agent Inbox не вернул валидный result, фаза `EXECUTE/RECOVERY` честно останавливается `WAITING_AGENT`. Hosted AI credential хранится в GitHub Secret/Environment Secret; локальный — в OS credential store. `.adwf-runtime` не является secret vault.

## Когда установка действительно завершена

Только когда живой readback подтверждает repository visibility, rulesets, required-check source и runtime anchor protection. Package-only проверки не превращают Control Plane в `VERIFIED`.
